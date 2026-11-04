"""ModelGateway: the only component that calls an inference provider.

Request lifecycle (blueprint Part 5.3):

1. the configuration must be admitted: its probes passed for this exact
   configuration digest, and the capabilities the call needs were observed;
2. the adapter enforces the endpoint policy (loopback only);
3. recognizable secrets are redacted from every message;
4. tokens are counted and an infeasible request is refused before any
   inference runs (context_infeasible);
5. a compute slot is reserved, with a deadline;
6. the reply is parsed and validated in full; a stream that ended early is
   `truncated` and nothing in it is ever acted on;
7. the call is recorded through the controller (digests, usage, status).

The model never chooses its own identities, budgets or deadlines: the
caller (controller code) supplies them.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from forge.contracts.digests import digest_json, sha256_digest
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.versions import SCHEMA_VERSION
from forge.gateway.adapters import AdapterTimeout, AdapterTransportError, ModelAdapter
from forge.gateway.contracts import (
    CapabilityReport,
    GenerationRequest,
    GenerationResult,
    Message,
    ModelConfig,
)
from forge.gateway.redaction import redact

SAFETY_TOKENS = 256
TEMPLATE_OVERHEAD = 512
MAX_RECORDED_OUTPUT = 64 * 1024

_PROBE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["answer"],
    "additionalProperties": False,
}


class ModelGateway:
    def __init__(
        self,
        controller=None,  # noqa: ANN001 - forge.controller.service.Controller, for call records
        *,
        adapters: dict[str, ModelAdapter],
        max_concurrent: int = 1,
    ) -> None:
        self.controller = controller
        self.adapters = adapters
        self.configs: dict[str, ModelConfig] = {}
        self.reports: dict[str, CapabilityReport] = {}
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._calls: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------- admission

    def register(self, config: ModelConfig) -> str:
        self.configs[config.model_config_id] = config
        return config.digest()

    def admitted(self, model_config_id: str) -> CapabilityReport | None:
        config = self.configs.get(model_config_id)
        if config is None:
            return None
        return self.reports.get(config.digest())

    def probe(self, model_config_id: str) -> CapabilityReport:
        """Exercise the interface with synthetic inputs. Proves behavior, not reasoning ability."""
        config = self.configs[model_config_id]
        adapter = self.adapters[config.provider_type]
        capabilities: dict[str, str] = {}
        notes: list[str] = []

        def request(messages: list[str], schema: dict | None) -> GenerationRequest:
            return GenerationRequest(
                call_id=new_id("request"), task_id=None, model_config_id=model_config_id, slot_id="probe",
                messages=[Message(role="user", content=text) for text in messages], output_schema=schema,
                max_output_tokens=32, deadline_ms=30_000,
            )

        try:
            raw = adapter.generate(config, request(["Reply with the single word: ready"], None))
            capabilities["text_generation"] = "observed" if raw.text.strip() else "failed"
        except (AdapterTimeout, AdapterTransportError) as error:
            capabilities["text_generation"] = "failed"
            notes.append(f"minimal completion failed: {error}")
        try:
            raw = adapter.generate(config, request(['Is 2 greater than 1? Answer as JSON {"answer": "yes" or "no"}.'],
                                                   _PROBE_SCHEMA))
            parsed = json.loads(raw.text)
            ok = raw.finished and isinstance(parsed, dict) and set(parsed) == {"answer"} and parsed["answer"] in ("yes", "no")
            capabilities["structured_output"] = "observed" if ok else "failed"
            if not ok:
                notes.append("structured-output probe returned JSON outside the schema")
        except (json.JSONDecodeError, AdapterTimeout, AdapterTransportError):
            # INT-T01: a provider that accepts the schema parameter but emits prose.
            capabilities["structured_output"] = "failed"
            notes.append("structured-output probe did not return parseable JSON")
        token_count = adapter.count_tokens(config, request(["count these words"], None))
        capabilities["exact_token_count"] = "observed" if token_count.mode == "exact_template" else "unverified"
        report = CapabilityReport(config_digest=config.digest(), capabilities=capabilities, notes=notes)
        self.reports[config.digest()] = report
        return report

    # ------------------------------------------------------------ generate

    def generate(
        self,
        *,
        model_config_id: str,
        slot_id: str,
        messages: list[Message],
        output_model: type[BaseModel] | None,
        output_schema: dict[str, Any] | None = None,
        task_id: str | None = None,
        deadline_ms: int = 60_000,
        call_id: str | None = None,
    ) -> GenerationResult:
        call_id = call_id or new_id("request")
        config = self.configs.get(model_config_id)
        report = self.admitted(model_config_id)
        if config is None or report is None:
            raise ForgeError(ErrorCode.MISSING_CAPABILITY,
                             f"model configuration {model_config_id!r} is not admitted (probe it first)")
        needed = ["text_generation"] + (["structured_output"] if output_model is not None else [])
        missing = report.admits(needed)
        if missing:
            return self._finish(call_id, config, slot_id, task_id, "policy_rejected", messages, None, None,
                                [f"capability not observed by probes: {', '.join(missing)}"], 0, [])
        adapter = self.adapters[config.provider_type]

        redacted_messages, redactions = [], []
        for message in messages:
            text, kinds = redact(message.content)
            redacted_messages.append(Message(role=message.role, content=text))
            redactions.extend(kinds)
        request = GenerationRequest(
            call_id=call_id, task_id=task_id, model_config_id=model_config_id, slot_id=slot_id,
            messages=redacted_messages, output_schema=output_schema, max_output_tokens=config.max_output_tokens,
            deadline_ms=deadline_ms,
        )

        count = adapter.count_tokens(config, request)
        budget = config.context_capacity - config.max_output_tokens - SAFETY_TOKENS
        if count.mode != "exact_template":
            budget -= TEMPLATE_OVERHEAD  # template tokens were not counted
        if count.count > budget:
            return self._finish(call_id, config, slot_id, task_id, "context_infeasible", redacted_messages, None, None,
                                [f"prompt needs {count.count} tokens ({count.mode}); budget is {budget}"], 0,
                                sorted(set(redactions)))

        if not self._slots.acquire(timeout=deadline_ms / 1000):
            return self._finish(call_id, config, slot_id, task_id, "timeout", redacted_messages, None, None,
                                ["no compute slot became free before the deadline"], 0, sorted(set(redactions)))
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                outcome["raw"] = adapter.generate(config, request)
            except AdapterTimeout as error:
                outcome["timeout"] = str(error)
            except AdapterTransportError as error:
                outcome["transport"] = str(error)
            finally:
                # The reservation is released only when the provider really returns.
                self._slots.release()
                with self._lock:
                    self._calls.pop(call_id, None)

        started = time.monotonic()
        thread = threading.Thread(target=work, daemon=True)
        with self._lock:
            self._calls[call_id] = thread
        thread.start()
        thread.join(deadline_ms / 1000)
        wall_ms = int((time.monotonic() - started) * 1000)
        if thread.is_alive():
            confirmed = adapter.cancel(call_id)
            status = "timeout" if confirmed else "cancel_unconfirmed"
            return self._finish(call_id, config, slot_id, task_id, status, redacted_messages, None, None,
                                ["deadline exceeded" + ("" if confirmed else "; provider may still be computing")],
                                wall_ms, sorted(set(redactions)))
        cancelled = call_id in getattr(adapter, "cancelled", set())
        if cancelled or "timeout" in outcome:
            # A proposal that arrives after cancellation is never accepted.
            return self._finish(call_id, config, slot_id, task_id, "cancelled" if cancelled else "timeout",
                                redacted_messages, None, None, [outcome.get("timeout", "cancelled by the user")],
                                wall_ms, sorted(set(redactions)))
        if "transport" in outcome:
            return self._finish(call_id, config, slot_id, task_id, "transport_error", redacted_messages, None, None,
                                [outcome["transport"]], wall_ms, sorted(set(redactions)))
        raw = outcome["raw"]
        status, output, errors = self._validate(raw, output_model)
        return self._finish(call_id, config, slot_id, task_id, status, redacted_messages, output, raw, errors, wall_ms,
                            sorted(set(redactions)))

    def cancel(self, call_id: str, model_config_id: str) -> bool:
        config = self.configs[model_config_id]
        return self.adapters[config.provider_type].cancel(call_id)

    def reservations_held(self) -> int:
        with self._lock:
            return len(self._calls)

    @staticmethod
    def _validate(raw, output_model):  # noqa: ANN001, ANN205
        if not raw.finished:
            return "truncated", None, [f"the reply ended early ({raw.finish_reason}); nothing in it is used"]
        if output_model is None:
            return "valid", {"text": raw.text}, []
        try:
            data = json.loads(raw.text)
        except json.JSONDecodeError:
            return "malformed_json", None, ["the reply is not valid JSON"]
        try:
            parsed = output_model.model_validate(data)
        except ValidationError as error:
            problems = [f"{'.'.join(map(str, e['loc'])) or '$'}: {e['msg']}"
                        for e in error.errors(include_url=False, include_input=False)[:5]]
            action = data.get("action") if isinstance(data, dict) else None
            allowed = _allowed_actions(output_model)
            if action is not None and allowed and action not in allowed:
                return "unsupported_action", None, [f"action {str(action)[:60]!r} is not allowed here"]
            return "schema_violation", None, problems
        return "valid", parsed.model_dump(mode="json"), []

    def _finish(self, call_id, config, slot_id, task_id, status, messages, output, raw, errors, wall_ms, redactions):  # noqa: ANN001, ANN202, PLR0913
        result = GenerationResult(
            call_id=call_id, status=status, output=output, errors=errors,
            prompt_tokens=getattr(raw, "prompt_tokens", None), completion_tokens=getattr(raw, "completion_tokens", None),
            wall_ms=wall_ms,
        )
        if self.controller is not None and self.controller.store.project() is not None:
            prompt_digest = digest_json([m.model_dump() for m in messages])
            output_text = json.dumps(output) if output is not None else (raw.text if raw is not None else "")
            self.controller.apply_command(
                {"schema_version": SCHEMA_VERSION, "method": "model.call_record",
                 "project_id": self.controller.store.project().project_id, "idempotency_key": new_id("request"),
                 "params": {
                     "call_id": call_id, "task_id": task_id, "model_config_id": config.model_config_id,
                     "config_digest": config.digest(), "slot_id": slot_id, "status": status,
                     "prompt_digest": prompt_digest, "output_digest": sha256_digest(output_text.encode()),
                     "output": output if output is not None and len(json.dumps(output)) <= MAX_RECORDED_OUTPUT else None,
                     "errors": errors[:10], "redactions": redactions,
                     "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
                     "wall_ms": wall_ms,
                 }},
                self.controller.system_actor,
            )
        return result


def _allowed_actions(model: type[BaseModel]) -> set[str]:
    field = model.model_fields.get("action")
    if field is None:
        return set()
    args = getattr(field.annotation, "__args__", ())
    return {arg for arg in args if isinstance(arg, str)}

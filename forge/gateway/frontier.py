"""Frontier model adapter (Anthropic Messages API), used only when a person escalates.

FORGE's default is a local model. When a problem exceeds it, a person may
send an escalation package (forge.gateway.escalation) to a frontier model.
This adapter is the transport for that send, through the same gateway as
local calls: the same token budget check, secret redaction, output
validation and call record apply, and whatever comes back is verified by
the same evaluators before anything is accepted.

Request shape (from the current API reference, not memory):

- model defaults to `claude-opus-5-5`; any configured model name is used as is;
- adaptive thinking with an explicit effort (default `high`, raise to
  `xhigh` or `max` for the hardest problems): the model decides how much to
  reason, the effort sets how hard to try;
- structured output through `output_config.format` (json_schema) when the
  slot has a schema (forced tool choice is rejected by current models);
- server-side refusal fallback (`fallbacks: "default"`): if a model's
  safety classifier declines, the API re-runs the request on Anthropic's
  recommended fallback model. If the whole chain still declines, the call
  is recorded as `refusal` with the stated category and explanation, and
  FORGE reports it to the person. FORGE never rewrites a request to get
  around a refusal;
- streaming, so long answers are not cut off by HTTP timeouts.

The `anthropic` package is an optional dependency (`pip install
"forge[frontier]"`); it is imported only when this adapter is used, so the
offline core never needs it. Credentials come from the SDK's standard
sources (ANTHROPIC_API_KEY, or a profile from `ant auth login`).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from forge.gateway.adapters import AdapterTimeout, AdapterTransportError
from forge.gateway.contracts import GenerationRequest, ModelConfig, RawGeneration, TokenCount

DEFAULT_FRONTIER_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs need every object closed: additionalProperties false and all properties required."""
    result = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node.setdefault("additionalProperties", False)
                node["required"] = sorted(set(node.get("required", [])) | set(node["properties"]))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


def _default_client() -> Any:
    try:
        import anthropic
    except ImportError:
        raise AdapterTransportError('the frontier adapter needs the anthropic package: pip install "forge[frontier]"') \
            from None
    return anthropic.Anthropic()


class AnthropicAdapter:
    """Transport for escalated calls. `client_factory` is injectable so tests never touch the network."""

    def __init__(self, *, effort: str = "high", client_factory: Callable[[], Any] | None = None) -> None:
        if effort not in EFFORTS:
            raise ValueError(f"effort must be one of {EFFORTS}")
        self.effort = effort
        self._factory = client_factory or _default_client
        self._client: Any = None
        self.cancelled: set[str] = set()

    def client(self) -> Any:
        if self._client is None:
            self._client = self._factory()
        return self._client

    @staticmethod
    def _split(request: GenerationRequest) -> tuple[str, list[dict[str, str]]]:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        messages = [{"role": m.role, "content": m.content} for m in request.messages if m.role != "system"]
        return system, messages

    def count_tokens(self, config: ModelConfig, request: GenerationRequest) -> TokenCount:
        system, messages = self._split(request)
        try:
            params: dict[str, Any] = {"model": config.model_name, "messages": messages}
            if system:
                params["system"] = system
            counted = self.client().messages.count_tokens(**params)
            return TokenCount(count=int(counted.input_tokens), mode="exact_template",
                              tokenizer_id=f"anthropic:{config.model_name}")
        except AdapterTransportError:
            raise
        except Exception:  # noqa: BLE001 - counting is advisory; an unreachable service is caught at generate time
            characters = len(system) + sum(len(m["content"]) for m in messages)
            return TokenCount(count=characters // 3 + 16, mode="estimated", tokenizer_id="characters/3")

    def _params(self, config: ModelConfig, request: GenerationRequest, *, with_format: bool) -> dict[str, Any]:
        system, messages = self._split(request)
        output_config: dict[str, Any] = {"effort": self.effort}
        if with_format and request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": strict_schema(request.output_schema)}
        params: dict[str, Any] = {
            "model": config.model_name, "max_tokens": request.max_output_tokens, "messages": messages,
            "thinking": {"type": "adaptive"}, "output_config": output_config,
            "betas": [FALLBACK_BETA], "fallbacks": "default",
        }
        if system:
            params["system"] = system
        return params

    def generate(self, config: ModelConfig, request: GenerationRequest) -> RawGeneration:
        try:
            import anthropic
            errors: tuple[type[BaseException], ...] = (anthropic.APIError,)
            timeout_error: type[BaseException] = anthropic.APITimeoutError
            bad_request: type[BaseException] = anthropic.BadRequestError
        except ImportError:   # an injected test client without the package installed
            errors, timeout_error, bad_request = (RuntimeError,), TimeoutError, ValueError
        params = self._params(config, request, with_format=True)
        try:
            try:
                message = self._stream(params)
            except bad_request as error:
                if "format" not in params["output_config"] or "schema" not in str(error).lower():
                    raise
                # The schema used a feature structured outputs does not support: ask for JSON in
                # the prompt instead. The gateway validates the reply against the schema either way.
                params = self._params(config, request, with_format=False)
                params["messages"] = [*params["messages"][:-1], {
                    "role": "user", "content": params["messages"][-1]["content"]
                    + "\n\nReply with one JSON object only, matching this schema:\n" + str(request.output_schema)}]
                message = self._stream(params)
        except timeout_error as error:
            raise AdapterTimeout(str(error)) from None
        except errors as error:
            raise AdapterTransportError(f"{type(error).__name__}: {str(error)[:300]}") from None
        usage = getattr(message, "usage", None)
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            explanation = getattr(details, "explanation", None) or "no explanation given"
            return RawGeneration(text="", finished=True, finish_reason=f"refusal:{category}:{explanation}"[:500],
                                 prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        return RawGeneration(text=text, finished=message.stop_reason in ("end_turn", "stop_sequence"),
                             finish_reason=message.stop_reason, prompt_tokens=prompt_tokens,
                             completion_tokens=completion_tokens)

    def _stream(self, params: dict[str, Any]) -> Any:
        with self.client().beta.messages.stream(**params) as stream:
            return stream.get_final_message()

    def cancel(self, call_id: str) -> bool:
        # A request already sent cannot be stopped server-side; the gateway reports cancel_unconfirmed.
        return False

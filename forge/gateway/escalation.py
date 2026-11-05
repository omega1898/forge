"""Escalation: when the local model cannot finish, say why, and let a person decide.

FORGE never refuses work and never decides on its own to send anything off
the machine. When the local loop stops without a result, FORGE:

1. explains the gap in plain terms from the record (`explain_gap`): which
   checks kept failing and how, whether the turn or candidate budget ran
   out, whether the model repeated itself, whether replies were malformed or
   declined, whether the context did not fit;
2. prepares an escalation package: the objective, the engineer's own
   description of the work, the explanation, what was tried, and the exact
   files, rendered as the exact text that would be sent;
3. sends it only when a trusted person approves that exact package (the
   approval names the package digest, so nothing can be added afterwards).

The answer comes back through the same gateway (redaction, validation, call
record) and, for a patch, through the same protected evaluator as local
output: a frontier model's confidence is not evidence either. If the model
declines, the person sees its stated reason and can choose another model.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.clock import format_timestamp
from forge.contracts.commands import ActorContext
from forge.contracts.digests import canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.gateway.contracts import Message
from forge.workspace.snapshot import load_manifest

MAX_PACKAGE_FILE_BYTES = 512 * 1024


class PackageFile(StrictModel):
    path: str
    digest: str
    text: str


class EscalationPackage(StrictModel):
    package_id: str
    created_at: str
    task_id: str | None
    objective: str = Field(max_length=20_000)
    engineer_context: str = Field(max_length=20_000)     # written by the person, never generated
    why_local_stopped: list[str]
    attempts: list[str]
    files: list[PackageFile]
    question: str = Field(max_length=20_000)

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))

    def render(self) -> str:
        """Exactly the user message that would be sent."""
        parts = [f"## Objective\n{self.objective}", f"## Context from the engineer\n{self.engineer_context}",
                 "## Why the local model stopped\n" + "\n".join(f"- {line}" for line in self.why_local_stopped),
                 "## What was tried\n" + ("\n".join(f"- {line}" for line in self.attempts) or "- nothing yet")]
        for item in self.files:
            parts.append(f"## File {item.path} ({item.digest})\n```\n{item.text}\n```")
        parts.append(f"## Question\n{self.question}")
        return "\n\n".join(parts)


SYSTEM_PROMPT = (
    "You are assisting a professional software engineer with their own systems. The engineer has described the "
    "work below in their own words. A local model attempted it first; its attempts and the checks that failed are "
    "included so you can avoid repeating them. Work the problem fully. If something cannot be done, say exactly "
    "what is missing. Any change you propose will be verified by the engineer's own test suite before it is used."
)


def explain_gap(report: Any) -> list[str]:  # noqa: ANN401 - forge.amplifier.loop.AssistReport
    """Why the local loop stopped, in plain terms, from what it recorded."""
    reasons: list[str] = []
    status = getattr(report, "status", "")
    if status == "budget_exhausted":
        reasons.append(f"the attempt budget ran out after {report.turns} turns and "
                       f"{len(report.candidates)} candidate(s): {report.detail}")
    elif status == "no_progress":
        reasons.append(f"the model repeated an action with no new evidence ({report.detail})")
    elif status == "waiting_for_user":
        reasons.append(f"the model needs information only the engineer has: {report.detail}")
    failing = [o for o in report.observations if o.startswith(("patch rejected", "case "))]
    for line in failing[-5:]:
        reasons.append(f"a check failed: {line[:300]}")
    rejected = [o for o in report.observations if o.startswith("the previous reply was rejected")]
    if rejected:
        kinds = sorted({o.rsplit(":", 1)[-1].strip() for o in rejected})
        reasons.append(f"{len(rejected)} replies were unusable ({', '.join(kinds)})")
        if "refusal" in kinds:
            reasons.append("the local model declined at least once")
        if "context_infeasible" in kinds:
            reasons.append("the needed context did not fit the local model's context window")
    return reasons or [f"the local loop ended with status {status!r}"]


def files_from_snapshot(controller, snapshot_id: str, paths: list[str]) -> list[PackageFile]:  # noqa: ANN001
    snapshot = controller.store.snapshot(snapshot_id)
    if snapshot is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
    entries = {e.path: e for e in load_manifest(controller.blobs, snapshot.manifest_digest).entries}
    files = []
    for path in paths:
        if path not in entries:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{path} is not in {snapshot_id}")
        data = controller.blobs.read_bytes(entries[path].digest, max_bytes=MAX_PACKAGE_FILE_BYTES)
        files.append(PackageFile(path=path, digest=entries[path].digest, text=data.decode("utf-8", "replace")))
    return files


class EscalationService:
    def __init__(self, controller, gateway) -> None:  # noqa: ANN001
        self.controller, self.gateway = controller, gateway

    def prepare(self, *, objective: str, engineer_context: str, report: Any, files: list[PackageFile],  # noqa: ANN401
                question: str, task_id: str | None = None) -> EscalationPackage:
        attempts = [o[:400] for o in getattr(report, "observations", [])][-12:]
        package = EscalationPackage(
            package_id=new_id("artifact"), created_at=format_timestamp(self.controller.clock.now()), task_id=task_id,
            objective=objective, engineer_context=engineer_context, why_local_stopped=explain_gap(report),
            attempts=attempts, files=files, question=question)
        blob = self.controller.blobs.put_bytes(canonical_json_bytes(package.model_dump(mode="json")))
        self._send_command("artifact.register", {"digest": blob.digest, "size": blob.size,
                                                 "content_type": "application/json",
                                                 "artifact_kind": "escalation_package", "sensitivity": "sensitive"})
        return package

    def _send_command(self, method: str, params: dict[str, Any]) -> Any:
        project = self.controller.store.project()
        return self.controller.apply_command({"schema_version": "1.0", "method": method,
                                              "project_id": project.project_id, "idempotency_key": new_id("request"),
                                              "params": params}, self.controller.system_actor)

    def send(self, package: EscalationPackage, *, approved_digest: str, actor: ActorContext, model_config_id: str,
             output_model=None, output_schema: dict | None = None, deadline_ms: int = 600_000) -> dict[str, Any]:  # noqa: ANN001
        """Send exactly the approved package. Only a trusted person can approve it."""
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only the engineer decides to send an escalation")
        if approved_digest != package.digest():
            raise ForgeError(ErrorCode.INVALID_REQUEST, "the approval is for a different package; review it again")
        if model_config_id not in self.gateway.configs:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"no model configuration {model_config_id!r}")
        result = self.gateway.generate(
            model_config_id=model_config_id, slot_id="escalation",
            messages=[Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=package.render())],
            output_model=output_model, output_schema=output_schema, task_id=package.task_id, deadline_ms=deadline_ms)
        outcome: dict[str, Any] = {"status": result.status, "call_id": result.call_id, "output": result.output,
                                   "errors": result.errors, "package_digest": package.digest(),
                                   "model_config_id": model_config_id}
        if result.status == "refusal":
            others = sorted(c for c in self.gateway.configs if c != model_config_id)
            outcome["next"] = ("the model declined; its stated reason is above. You can send the same package to "
                               f"another model you choose: {others or 'none configured'}")
        return outcome


def render_for_review(package: EscalationPackage) -> str:
    """What the engineer reads before approving: the full outgoing text and its digest."""
    return (f"Escalation package {package.package_id}\nDigest to approve: {package.digest()}\n"
            f"Files: {json.dumps([f.path for f in package.files])}\n\n--- system ---\n{SYSTEM_PROMPT}\n\n"
            f"--- message ---\n{package.render()}")

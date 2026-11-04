"""Commands: the only way to ask the controller to change project state.

A command arrives as untrusted JSON. It is checked in this order:

1. schema_version (so a newer client gets "unsupported version", not noise)
2. the envelope shape (method, project_id, idempotency_key, ...)
3. the method-specific params shape

Only then does the controller check meaning: do referenced records exist, is
the expected version current, is the actor allowed. Shape checks live here;
meaning checks live in forge/controller/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from forge.contracts.base import IdempotencyKey, RevisionRef, Slug, StrictModel, Text
from forge.contracts.clock import Timestamp
from forge.contracts.digests import Digest, canonical_json_bytes
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import (
    ActorId,
    AnyId,
    CommandId,
    CriterionId,
    DecisionId,
    GrantId,
    IdOf,
    ProjectId,
    TaskId,
)
from forge.contracts.paths import check_relative_path
from forge.contracts.records import Budget, DecisionKind, TaskStatus, TaskType, WaiverPolicy
from forge.contracts.versions import check_schema_version

Method = Literal[
    "project.create",
    "requirement.create",
    "criterion.create",
    "task.create",
    "task.add_dependency",
    "task.transition",
    "decision.request",
    "decision.record",
    "workspace.capture",
    "suite.admit",
    "artifact.register",
    "evidence.record_execution",
    "evidence.record_review",
    "assessment.record",
    "finding.record",
    "plan.apply",
    "task.complete",
]

# Methods that change an existing record and therefore must say which version
# of that record the client last saw.
VERSIONED_METHODS: frozenset[str] = frozenset(
    {"task.add_dependency", "task.transition", "decision.record", "task.complete"}
)

MAX_PARAMETERS_BYTES = 16 * 1024


# ---------------------------------------------------------------- actors ----

ActorKind = Literal["human", "model", "system", "host"]
Channel = Literal["trusted_local", "api", "repository"]


class ActorContext(StrictModel):
    """Who is acting, established by the controller's authentication layer.

    This object is never read from a request body or a repository file. A
    model cannot name itself a human reviewer by putting text in a payload.
    """

    actor_id: ActorId
    kind: ActorKind
    channel: Channel

    @property
    def is_trusted_human(self) -> bool:
        return self.kind == "human" and self.channel == "trusted_local"


# ---------------------------------------------------------------- params ----


class ProjectCreateParams(StrictModel):
    name: Text(200)
    objective: Text(4000)
    profile_id: Slug = "standard"
    # Labels for approved source roots. Binding a label to a host directory is
    # trusted user-level configuration (ticket F03), never a repository value.
    source_roots: list[Slug] = Field(default_factory=list, max_length=16)

    @field_validator("source_roots")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("source_roots must not contain duplicates")
        return value


class RequirementCreateParams(StrictModel):
    statement: Text(4000)
    criterion_ids: list[CriterionId] = Field(default_factory=list, max_length=64)

    @field_validator("criterion_ids")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("criterion_ids must not contain duplicates")
        return value


class CriterionCreateParams(StrictModel):
    subject: Text(2000)
    assessment_method: Slug
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    required_environments: list[Slug] = Field(default_factory=list, max_length=16)
    waiver_policy: WaiverPolicy = "forbidden"

    @field_validator("parameters")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            size = len(canonical_json_bytes(value))
        except ValueError as error:
            raise ValueError(f"parameters are not canonical JSON: {error}") from None
        if size > MAX_PARAMETERS_BYTES:
            raise ValueError(f"parameters exceed {MAX_PARAMETERS_BYTES} bytes")
        return value


class TaskCreateParams(StrictModel):
    task_type: TaskType
    title: Text(300)
    requirement_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    criterion_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    # Snapshot and artifact inputs arrive with ticket F03.
    input_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    depends_on: list[TaskId] = Field(default_factory=list, max_length=64)
    authority_ref: GrantId | None = None
    budget: Budget
    workflow_revision: int = Field(default=1, ge=1)

    @field_validator("depends_on")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("depends_on must not contain duplicates")
        return value


class TaskAddDependencyParams(StrictModel):
    task_id: TaskId
    depends_on_task_id: TaskId

    @model_validator(mode="after")
    def _not_self(self) -> TaskAddDependencyParams:
        if self.task_id == self.depends_on_task_id:
            raise ValueError("a task cannot depend on itself")
        return self


class TaskTransitionParams(StrictModel):
    task_id: TaskId
    to_status: TaskStatus
    reason: Text(2000)


class DecisionRequestParams(StrictModel):
    decision_kind: DecisionKind
    subject_ref: RevisionRef
    question: Text(4000)
    proposal_digest: Digest | None = None
    authority_ref: GrantId | None = None
    expires_at: Timestamp | None = None


class DecisionRecordParams(StrictModel):
    decision_id: DecisionId
    disposition: Literal["approved", "rejected", "withdrawn"]
    reason: Text(4000)
    # The decision is bound to the exact proposal the person saw.
    proposal_digest: Digest | None = None


class WorkspaceCaptureParams(StrictModel):
    source_root: Slug
    # Editor buffers with unsaved changes. Their unsaved text is NOT captured;
    # the snapshot records that they existed so nobody assumes otherwise.
    dirty_paths: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("dirty_paths")
    @classmethod
    def _relative(cls, value: list[str]) -> list[str]:
        return [check_relative_path(item) for item in value]


class SuiteAdmitParams(StrictModel):
    # The suite itself (validated as an EvaluatorSuite by the controller).
    suite: dict[str, JsonValue]


class ArtifactRegisterParams(StrictModel):
    digest: Digest
    size: int = Field(ge=0)
    content_type: Text(100)
    artifact_kind: Slug
    sensitivity: Literal["public", "project", "sensitive"] = "project"
    retention: Literal["keep", "ephemeral"] = "keep"
    producer_ref: AnyId | None = None


class EvidenceRecordExecutionParams(StrictModel):
    job_id: IdOf("job")
    attempt_number: int = Field(ge=1)
    snapshot_id: IdOf("snapshot")
    tool_ref: Text(200)
    environment_digest: Digest
    evaluator_digest: Digest | None = None
    observation_type: Slug
    observation: dict[str, JsonValue]
    artifact_refs: list[IdOf("artifact")] = Field(default_factory=list, max_length=64)


class EvidenceRecordReviewParams(StrictModel):
    subject_ref: RevisionRef
    observation_type: Slug = "review_observation"
    observation: dict[str, JsonValue]


class AssessmentRecordParams(StrictModel):
    criterion_ref: RevisionRef
    snapshot_id: IdOf("snapshot") | None
    evidence_refs: list[IdOf("evidence")] = Field(default_factory=list, max_length=64)
    assessment_result: Literal["pass", "fail", "inconclusive", "not_run", "error", "not_applicable"]
    assessor_ref: Text(200)
    limitations: list[Text(1000)] = Field(default_factory=list, max_length=32)
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    supersedes_assessment_id: IdOf("assessment") | None = None


class FindingRecordParams(StrictModel):
    rule_id: Text(100)
    claim: Text(2000)
    severity: Literal["critical", "high", "medium", "low", "info"]
    confirmation: Literal["suspected", "confirmed"] = "suspected"
    path: str | None = None
    line: int | None = Field(default=None, ge=1)
    fingerprint: Digest
    snapshot_id: IdOf("snapshot") | None = None
    evidence_refs: list[IdOf("evidence")] = Field(default_factory=list, max_length=64)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        return None if value is None else check_relative_path(value)


class PlannedTask(StrictModel):
    key: Slug  # local name, used by other entries in the same plan change
    task_type: TaskType
    title: Text(300)
    requirement_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    criterion_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    input_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    authority_ref: GrantId | None = None
    budget: Budget
    supersedes_task_id: TaskId | None = None


class PlanDependency(StrictModel):
    task: str  # a key from this change or an existing task ID
    depends_on: str


class PlanApplyParams(StrictModel):
    base_plan_revision: int = Field(ge=0)
    summary: Text(1000)
    add_tasks: list[PlannedTask] = Field(default_factory=list, max_length=200)
    add_dependencies: list[PlanDependency] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _not_empty(self) -> PlanApplyParams:
        if not self.add_tasks and not self.add_dependencies:
            raise ValueError("a plan change must add tasks or dependencies")
        return self


class TaskCompleteParams(StrictModel):
    task_id: TaskId
    # The snapshot whose assessments establish completion (None for tasks
    # judged without a snapshot, such as decide tasks).
    snapshot_id: IdOf("snapshot") | None = None
    reason: Text(2000)


PARAMS_BY_METHOD: dict[str, type[StrictModel]] = {
    "project.create": ProjectCreateParams,
    "requirement.create": RequirementCreateParams,
    "criterion.create": CriterionCreateParams,
    "task.create": TaskCreateParams,
    "task.add_dependency": TaskAddDependencyParams,
    "task.transition": TaskTransitionParams,
    "decision.request": DecisionRequestParams,
    "decision.record": DecisionRecordParams,
    "workspace.capture": WorkspaceCaptureParams,
    "suite.admit": SuiteAdmitParams,
    "artifact.register": ArtifactRegisterParams,
    "evidence.record_execution": EvidenceRecordExecutionParams,
    "evidence.record_review": EvidenceRecordReviewParams,
    "assessment.record": AssessmentRecordParams,
    "finding.record": FindingRecordParams,
    "plan.apply": PlanApplyParams,
    "task.complete": TaskCompleteParams,
}


# -------------------------------------------------------------- envelope ----


class CommandEnvelope(StrictModel):
    schema_version: str
    method: Method
    project_id: ProjectId | None = None
    idempotency_key: IdempotencyKey
    expected_record_version: int | None = Field(default=None, ge=1)
    params: dict[str, JsonValue]

    @model_validator(mode="after")
    def _project_rules(self) -> CommandEnvelope:
        if self.method == "project.create":
            if self.project_id is not None:
                raise ValueError("project.create must not carry project_id; the controller assigns it")
        elif self.project_id is None:
            raise ValueError(f"{self.method} requires project_id")
        if self.method in VERSIONED_METHODS and self.expected_record_version is None:
            raise ValueError(f"{self.method} requires expected_record_version")
        return self


@dataclass(frozen=True)
class Command:
    """A fully validated command: envelope plus typed params."""

    envelope: CommandEnvelope
    params: StrictModel

    @property
    def method(self) -> str:
        return self.envelope.method

    def semantic_payload(self) -> dict[str, Any]:
        """What the deduplication digest covers.

        Transport details such as the JSON-RPC request id are excluded on
        purpose: a retry may use a new transport id but is the same request.
        """
        return {
            "method": self.envelope.method,
            "project_id": self.envelope.project_id,
            "expected_record_version": self.envelope.expected_record_version,
            "params": self.params.model_dump(mode="json"),
        }


def _validation_message(error: ValidationError) -> str:
    parts = []
    for item in error.errors(include_url=False, include_input=False)[:5]:
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def parse_command(raw: object) -> Command:
    """Validate untrusted command data; raise ForgeError with a clear code."""
    if not isinstance(raw, dict):
        raise ForgeError(ErrorCode.INVALID_REQUEST, "command must be a JSON object")
    check_schema_version(raw.get("schema_version"), what="command")
    try:
        envelope = CommandEnvelope.model_validate(raw)
    except ValidationError as error:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST, f"invalid command envelope: {_validation_message(error)}"
        ) from None
    params_model = PARAMS_BY_METHOD[envelope.method]
    try:
        params = params_model.model_validate(envelope.params)
    except ValidationError as error:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"invalid params for {envelope.method}: {_validation_message(error)}",
        ) from None
    return Command(envelope=envelope, params=params)


# --------------------------------------------------------------- receipt ----


class CommandReceipt(StrictModel):
    """The controller's durable answer to an accepted command.

    Receiving a receipt means the command's effect is recorded. It does not
    mean any long-running work has finished.
    """

    schema_version: str
    command_id: CommandId
    method: Method
    project_id: ProjectId
    idempotency_key: IdempotencyKey
    request_digest: Digest
    actor_id: ActorId
    accepted_at: Timestamp
    event_seqs: list[int]
    result: dict[str, JsonValue]
    # True only when this receipt is a stored answer returned for a repeated
    # delivery of the same request. Never stored as true.
    duplicate_delivery: bool = False


__all__ = [
    "ActorContext",
    "AnyId",
    "Command",
    "CommandEnvelope",
    "CommandReceipt",
    "PARAMS_BY_METHOD",
    "VERSIONED_METHODS",
    "parse_command",
]

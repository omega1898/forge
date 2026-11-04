"""Typed views of the project records stored by the controller.

These are what readers get back. They are not commands: nothing outside the
controller creates or edits them directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from forge.contracts.base import RevisionRef, Slug, StrictModel
from forge.contracts.clock import Timestamp
from forge.contracts.digests import Digest
from forge.contracts.ids import (
    ActorId,
    AnyId,
    ArtifactId,
    AssessmentId,
    ControllerId,
    CriterionId,
    DecisionId,
    EvidenceId,
    FindingId,
    GrantId,
    JobId,
    ProjectId,
    RequirementId,
    SnapshotId,
    TaskId,
)

# Task categories from the blueprint. Only "execute" launches workload code and
# therefore needs a job; the others run as bounded controller operations.
TaskType = Literal["prepare", "execute", "assess", "decide"]

TaskStatus = Literal[
    "queued", "running", "blocked", "waiting_for_user", "completed", "failed", "cancelled"
]
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

WaiverPolicy = Literal["forbidden", "allowed_with_decision"]
RequirementStatus = Literal["accepted", "superseded"]
DecisionKind = Literal["apply_change", "waiver", "architecture", "grant", "release"]
Disposition = Literal["pending", "approved", "rejected", "withdrawn", "expired"]


class CommonFields(StrictModel):
    project_id: ProjectId
    created_at: Timestamp
    created_by: ActorId
    record_version: int = Field(ge=1)


class Project(CommonFields):
    name: str
    objective: str
    controller_id: ControllerId
    source_roots: list[Slug]
    profile_id: Slug
    last_event_seq: int = Field(ge=0)
    plan_revision: int = Field(default=0, ge=0)


class Requirement(CommonFields):
    requirement_id: RequirementId
    statement: str
    revision: int = Field(ge=1)
    status: RequirementStatus
    criterion_ids: list[CriterionId]


class Criterion(CommonFields):
    criterion_id: CriterionId
    revision: int = Field(ge=1)
    subject: str
    assessment_method: Slug
    parameters: dict[str, JsonValue]
    required_environments: list[Slug]
    waiver_policy: WaiverPolicy


class Budget(StrictModel):
    """Upper limits a task may consume. Milliseconds, measured monotonically."""

    max_wall_ms: int = Field(ge=1, le=24 * 60 * 60 * 1000)
    max_attempts: int = Field(ge=1, le=20)


class Task(CommonFields):
    task_id: TaskId
    task_type: TaskType
    title: str
    status: TaskStatus
    requirement_refs: list[RevisionRef]
    criterion_refs: list[RevisionRef]
    input_refs: list[RevisionRef]
    authority_ref: GrantId | None
    budget: Budget
    workflow_revision: int = Field(ge=1)
    depends_on: list[TaskId]
    updated_at: Timestamp
    supersedes_task_id: TaskId | None = None
    superseded_by_task_id: TaskId | None = None
    # How completion was judged (snapshot, assessments, waivers); None until completed.
    completion: dict[str, JsonValue] | None = None


class Decision(CommonFields):
    decision_id: DecisionId
    decision_kind: DecisionKind
    subject_ref: RevisionRef
    question: str
    proposal_digest: Digest | None
    disposition: Disposition
    reason: str | None
    actor_id: ActorId | None
    authority_ref: GrantId | None
    expires_at: Timestamp | None
    updated_at: Timestamp


Sensitivity = Literal["public", "project", "sensitive"]
Retention = Literal["keep", "ephemeral"]


class Artifact(CommonFields):
    artifact_id: ArtifactId
    digest: Digest
    size: int = Field(ge=0)
    content_type: str
    artifact_kind: Slug
    sensitivity: Sensitivity
    retention: Retention
    producer_ref: AnyId | None
    tombstoned_at: Timestamp | None
    tombstone_reason: str | None


class Snapshot(CommonFields):
    snapshot_id: SnapshotId
    manifest_digest: Digest
    manifest_artifact_id: ArtifactId
    source_root: Slug
    base_commit: str | None
    capture_policy_revision: int
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    dirty_paths: list[str]
    parent_snapshot_id: SnapshotId | None
    origin: Literal["capture", "candidate"]


AssessmentResult = Literal["pass", "fail", "inconclusive", "not_run", "error", "not_applicable"]
Severity = Literal["critical", "high", "medium", "low", "info"]


class Evidence(CommonFields):
    evidence_id: EvidenceId
    origin: Literal["execution", "review"]
    job_id: JobId | None
    attempt_number: int | None
    snapshot_id: SnapshotId | None
    tool_ref: str | None
    environment_digest: Digest | None
    evaluator_digest: Digest | None
    subject_ref: RevisionRef | None
    reviewer_id: ActorId | None
    observation_type: Slug
    observation: dict[str, JsonValue]
    artifact_refs: list[ArtifactId]


class Assessment(CommonFields):
    assessment_id: AssessmentId
    criterion_ref: RevisionRef
    snapshot_id: SnapshotId | None
    evidence_refs: list[EvidenceId]
    assessment_result: AssessmentResult
    assessor_ref: str
    limitations: list[str]
    summary: dict[str, JsonValue]
    supersedes_assessment_id: AssessmentId | None
    # Position of the recording event in the log: the authoritative order.
    recorded_seq: int | None = None


class Finding(CommonFields):
    finding_id: FindingId
    rule_id: str
    claim: str
    severity: Severity
    status: Literal["open", "resolved", "waived", "false_positive"]
    confirmation: Literal["suspected", "confirmed"]
    path: str | None
    line: int | None
    fingerprint: Digest
    snapshot_id: SnapshotId | None
    evidence_refs: list[EvidenceId]


class EventRecord(StrictModel):
    """One committed, immutable entry in the project's event log."""

    event_seq: int = Field(ge=1)
    event_id: AnyId
    project_id: ProjectId
    command_id: AnyId
    event_type: str
    event_schema_version: int = Field(ge=1)
    actor_id: ActorId
    occurred_at: Timestamp
    causation_id: AnyId | None
    payload: dict[str, JsonValue]

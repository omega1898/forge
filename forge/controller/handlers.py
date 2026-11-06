"""Meaning checks and event production for each command method.

A handler runs inside the controller's write transaction. It reads current
state, decides whether the command is allowed *now*, and returns the events
that describe the accepted change. It never writes to the database itself and
never performs side effects; the controller appends and projects the events.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forge.artifacts.blobs import BlobInfo, BlobStore
from forge.authority.grants import TrustStore
from forge.contracts.base import RevisionRef
from forge.contracts.clock import parse_timestamp
from forge.contracts.commands import (
    ActorContext,
    Command,
    CriterionCreateParams,
    DecisionRecordParams,
    DecisionRequestParams,
    ProjectCreateParams,
    RequirementCreateParams,
    TaskAddDependencyParams,
    TaskCreateParams,
    ArtifactRegisterParams,
    AssessmentRecordParams,
    EvidenceRecordExecutionParams,
    EvidenceRecordReviewParams,
    FindingRecordParams,
    IntentSubmitParams,
    ModelCallRecordParams,
    SnapshotRecordCandidateParams,
    ProposalCreateParams,
    ChangeRecordApplicationParams,
    JobCreateParams,
    JobObserveParams,
    JobReceiptParams,
    JobReconciliationParams,
    JobRetryParams,
    PlanApplyParams,
    SuiteAdmitParams,
    TaskCancelParams,
    TaskCompleteParams,
    TaskTransitionParams,
    WorkspaceCaptureParams,
)
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import id_prefix, new_id
from forge.contracts.records import TERMINAL_TASK_STATUSES, Project
from forge.engine.completion import evaluate_completion
from forge.engine.graph import find_cycle
from forge.engine.plans import validate_plan
from forge.engine.lifecycle import is_allowed
from forge.record.events import DomainEvent
from forge.record.store import ProjectStore
from forge.workspace.snapshot import CapturePolicy, CaptureResult, capture_tree
from forge.workspace.visibility import is_protected


@dataclass
class HandlerContext:
    store: ProjectStore
    actor: ActorContext
    now: datetime
    controller_id: str
    trust_store: TrustStore | None
    project: Project | None
    blobs: BlobStore | None = None
    # Result of the method's preparer, computed before the write lock.
    prepared: Any = None


@dataclass
class PrepareContext:
    """What a preparer may use. It runs with no database transaction open."""

    store: ProjectStore
    actor: ActorContext
    trust_store: TrustStore | None
    blobs: BlobStore | None


@dataclass
class Outcome:
    events: list[DomainEvent]
    result: dict[str, Any] = field(default_factory=dict)
    # Set by project.create, whose project does not exist before the command.
    project_id: str | None = None


Handler = Callable[[HandlerContext, Command], Outcome]


# ------------------------------------------------------------------ helpers --


def _status_event(task, to_status: str, reason: str, *, version: int | None = None) -> DomainEvent:  # noqa: ANN001
    """A task.status_changed event that respects the lifecycle table.

    Handlers that change a task's status as a side effect (job admission,
    reconciliation, superseding) go through this too, so no code path can
    emit a transition the table forbids.
    """
    if not is_allowed(task.status, to_status):
        raise ForgeError(
            ErrorCode.INTERNAL_ERROR,
            f"internal transition {task.status} -> {to_status} for {task.task_id} violates the task lifecycle",
        )
    return DomainEvent(
        "task.status_changed",
        {"task_id": task.task_id, "from_status": task.status, "to_status": to_status, "reason": reason,
         "record_version": (version if version is not None else task.record_version + 1)},
    )


def _require_version(command: Command, current: int, object_id: str) -> None:
    expected = command.envelope.expected_record_version
    if expected != current:
        raise ForgeError(
            ErrorCode.STALE_VERSION,
            f"{object_id} is at record version {current}, but the request expected {expected}; "
            "re-read it and decide again",
            related_ids=[object_id],
            details={"expected": expected, "current": current},
        )


def _with_compliance_gate(ctx: HandlerContext, task_type: str, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """New execute tasks carry the project's compliance gate, so the domain's checks kick in by themselves."""
    from forge.compliance.service import active_gate

    gate = active_gate(ctx.store)
    if task_type != "execute" or gate is None or gate["parameters"].get("profile_id") == "none":
        return refs
    if any(ref["id"] == gate["criterion_id"] for ref in refs):
        return refs
    return [*refs, {"id": gate["criterion_id"], "revision": gate["revision"]}]


def _check_ref(ctx: HandlerContext, ref: RevisionRef, kind: str) -> None:
    """A reference must name an existing record of the right kind at its current revision."""
    if id_prefix(ref.id) != kind:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST, f"expected a {kind} reference, got {ref.id}", related_ids=[ref.id]
        )
    current = _current_revision(ctx, ref.id)
    if current is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{ref.id} does not exist", related_ids=[ref.id])
    if current != ref.revision:
        raise ForgeError(
            ErrorCode.STALE_VERSION,
            f"{ref.id} is at revision {current}, but the reference names revision {ref.revision}",
            related_ids=[ref.id],
            details={"expected": ref.revision, "current": current},
        )


def _current_revision(ctx: HandlerContext, object_id: str) -> int | None:
    kind = id_prefix(object_id)
    store = ctx.store
    if kind == "requirement":
        record = store.requirement(object_id)
        return None if record is None else record.revision
    if kind == "criterion":
        record = store.criterion(object_id)
        return None if record is None else record.revision
    if kind == "task":
        record = store.task(object_id)
        return None if record is None else record.record_version
    if kind == "decision":
        record = store.decision(object_id)
        return None if record is None else record.record_version
    if kind == "proposal":
        proposal = store.proposal(object_id)
        return None if proposal is None else proposal["record_version"]
    raise ForgeError(
        ErrorCode.INVALID_REQUEST, f"{kind} records cannot be referenced here", related_ids=[object_id]
    )


def _require_grant(ctx: HandlerContext, grant_id: str) -> None:
    if ctx.trust_store is None:
        raise ForgeError(
            ErrorCode.MISSING_CAPABILITY,
            "no trust store is configured, so grant references cannot be checked",
            related_ids=[grant_id],
        )
    assert ctx.project is not None
    if ctx.trust_store.effective_grant(grant_id, ctx.project.project_id) is None:
        raise ForgeError(
            ErrorCode.PERMISSION_DENIED,
            f"{grant_id} is not an approved, unexpired grant for this project",
            related_ids=[grant_id],
        )


# ----------------------------------------------------------------- handlers --


def project_create(ctx: HandlerContext, command: Command) -> Outcome:
    params: ProjectCreateParams = command.params  # type: ignore[assignment]
    if ctx.actor.kind != "human":
        raise ForgeError(ErrorCode.PERMISSION_DENIED, "only a human can create a project")
    if ctx.project is not None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"this database already holds {ctx.project.project_id}",
            related_ids=[ctx.project.project_id],
        )
    project_id = new_id("project")
    project = {
        "project_id": project_id,
        "name": params.name,
        "objective": params.objective,
        "controller_id": ctx.controller_id,
        "source_roots": params.source_roots,
        "profile_id": params.profile_id,
    }
    return Outcome(
        events=[DomainEvent("project.created", {"project": project})],
        result={"project_id": project_id},
        project_id=project_id,
    )


def requirement_create(ctx: HandlerContext, command: Command) -> Outcome:
    params: RequirementCreateParams = command.params  # type: ignore[assignment]
    for criterion_id in params.criterion_ids:
        if ctx.store.criterion(criterion_id) is None:
            raise ForgeError(
                ErrorCode.INVALID_REQUEST, f"{criterion_id} does not exist", related_ids=[criterion_id]
            )
    requirement_id = new_id("requirement")
    body = {
        "requirement_id": requirement_id,
        "statement": params.statement,
        "revision": 1,
        "status": "accepted",
        "criterion_ids": params.criterion_ids,
    }
    return Outcome(
        events=[DomainEvent("requirement.created", {"requirement": body})],
        result={"requirement_id": requirement_id, "revision": 1},
    )


def criterion_create(ctx: HandlerContext, command: Command) -> Outcome:
    params: CriterionCreateParams = command.params  # type: ignore[assignment]
    if params.assessment_method == "black_box_cases":
        _check_suite_reference(ctx, params.parameters)
    if params.assessment_method == "compliance_gate":
        # The gate decides what "done" means for the whole project: only a person sets or removes it.
        if not ctx.actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only a trusted person chooses the compliance domain")
        from forge.compliance.profiles import resolve

        values = params.parameters
        options = values.get("options") or {}
        catalogs = values.get("catalogs") or []
        if not isinstance(values.get("profile_id"), str) or not isinstance(options, dict) \
                or not isinstance(catalogs, list):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "a compliance gate needs profile_id, options and catalogs")
        resolve(values["profile_id"], {str(k): str(v) for k, v in options.items()},
                extra_catalogs=[str(c) for c in catalogs])
    criterion_id = new_id("criterion")
    body = {
        "criterion_id": criterion_id,
        "revision": 1,
        "subject": params.subject,
        "assessment_method": params.assessment_method,
        "parameters": params.parameters,
        "required_environments": params.required_environments,
        "waiver_policy": params.waiver_policy,
    }
    return Outcome(
        events=[DomainEvent("criterion.created", {"criterion": body})],
        result={"criterion_id": criterion_id, "revision": 1},
    )


def task_create(ctx: HandlerContext, command: Command) -> Outcome:
    params: TaskCreateParams = command.params  # type: ignore[assignment]
    for ref in params.requirement_refs:
        _check_ref(ctx, ref, "requirement")
    for ref in params.criterion_refs:
        _check_ref(ctx, ref, "criterion")
    for ref in params.input_refs:
        if id_prefix(ref.id) != "snapshot" or ctx.store.snapshot(ref.id) is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"input {ref.id} is not an existing snapshot")
    for dependency in params.depends_on:
        if ctx.store.task(dependency) is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{dependency} does not exist", related_ids=[dependency])
    if params.authority_ref is not None:
        _require_grant(ctx, params.authority_ref)
    task_id = new_id("task")
    task = {
        "task_id": task_id,
        "task_type": params.task_type,
        "title": params.title,
        "status": "queued",
        "requirement_refs": [ref.model_dump() for ref in params.requirement_refs],
        "criterion_refs": _with_compliance_gate(ctx, params.task_type,
                                                [ref.model_dump() for ref in params.criterion_refs]),
        "input_refs": [ref.model_dump() for ref in params.input_refs],
        "authority_ref": params.authority_ref,
        "budget": params.budget.model_dump(),
        "workflow_revision": params.workflow_revision,
    }
    # A brand-new task cannot close a cycle (nothing depends on it yet), so no
    # cycle check is needed here; task.add_dependency performs it.
    return Outcome(
        events=[DomainEvent("task.created", {"task": task, "depends_on": params.depends_on})],
        result={"task_id": task_id, "record_version": 1},
    )


def task_add_dependency(ctx: HandlerContext, command: Command) -> Outcome:
    params: TaskAddDependencyParams = command.params  # type: ignore[assignment]
    task = ctx.store.task(params.task_id)
    if task is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.task_id} does not exist", related_ids=[params.task_id])
    if ctx.store.task(params.depends_on_task_id) is None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{params.depends_on_task_id} does not exist",
            related_ids=[params.depends_on_task_id],
        )
    _require_version(command, task.record_version, task.task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{task.task_id} is {task.status}; finished tasks are not re-planned",
            related_ids=[task.task_id],
        )
    if params.depends_on_task_id in task.depends_on:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "that dependency already exists", related_ids=[task.task_id])
    edges = ctx.store.dependency_edges() + [(params.task_id, params.depends_on_task_id)]
    cycle = find_cycle(edges)
    if cycle is not None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            "dependency rejected: it would create a cycle " + " -> ".join(cycle),
            related_ids=cycle[:-1],
            details={"cycle": cycle},
        )
    new_version = task.record_version + 1
    return Outcome(
        events=[
            DomainEvent(
                "task.dependency_added",
                {
                    "task_id": task.task_id,
                    "depends_on_task_id": params.depends_on_task_id,
                    "record_version": new_version,
                },
            )
        ],
        result={"task_id": task.task_id, "record_version": new_version},
    )


def task_transition(ctx: HandlerContext, command: Command) -> Outcome:
    params: TaskTransitionParams = command.params  # type: ignore[assignment]
    task = ctx.store.task(params.task_id)
    if task is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.task_id} does not exist", related_ids=[params.task_id])
    _require_version(command, task.record_version, task.task_id)
    if not is_allowed(task.status, params.to_status):
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"a task cannot move from {task.status} to {params.to_status}",
            related_ids=[task.task_id],
        )
    if params.to_status == "completed":
        # Completion must come from the completion evaluator checking required
        # assessments and decisions. A command saying "it's done" is not evidence.
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            "use task.complete: completion is decided by the completion evaluator from recorded assessments",
            related_ids=[task.task_id],
        )
    if params.to_status == "running":
        if task.task_type == "execute":
            raise ForgeError(
                ErrorCode.MISSING_CAPABILITY,
                "execute tasks need job admission and an isolation backend (tickets F04/F07)",
                related_ids=[task.task_id],
            )
        unfinished = [
            dependency
            for dependency in task.depends_on
            if (found := ctx.store.task(dependency)) is None or found.status != "completed"
        ]
        if unfinished:
            raise ForgeError(
                ErrorCode.INVALID_REQUEST,
                f"{task.task_id} is not ready: waiting on {len(unfinished)} dependency task(s)",
                related_ids=unfinished,
            )
        if task.authority_ref is not None:
            # Authority is rechecked at start: a grant revoked while the task
            # waited must stop it.
            _require_grant(ctx, task.authority_ref)
    new_version = task.record_version + 1
    return Outcome(
        events=[
            DomainEvent(
                "task.status_changed",
                {
                    "task_id": task.task_id,
                    "from_status": task.status,
                    "to_status": params.to_status,
                    "reason": params.reason,
                    "record_version": new_version,
                },
            )
        ],
        result={"task_id": task.task_id, "status": params.to_status, "record_version": new_version},
    )


def decision_request(ctx: HandlerContext, command: Command) -> Outcome:
    params: DecisionRequestParams = command.params  # type: ignore[assignment]
    _check_ref(ctx, params.subject_ref, id_prefix(params.subject_ref.id))
    if params.expires_at is not None and parse_timestamp(params.expires_at) <= ctx.now:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "decision expiry must be in the future")
    if params.authority_ref is not None:
        _require_grant(ctx, params.authority_ref)
    decision_id = new_id("decision")
    decision = {
        "decision_id": decision_id,
        "decision_kind": params.decision_kind,
        "subject_ref": params.subject_ref.model_dump(),
        "question": params.question,
        "proposal_digest": params.proposal_digest,
        "authority_ref": params.authority_ref,
        "expires_at": params.expires_at,
    }
    return Outcome(
        events=[DomainEvent("decision.requested", {"decision": decision})],
        result={"decision_id": decision_id, "record_version": 1},
    )


def decision_record(ctx: HandlerContext, command: Command) -> Outcome:
    params: DecisionRecordParams = command.params  # type: ignore[assignment]
    if not ctx.actor.is_trusted_human:
        raise ForgeError(
            ErrorCode.PERMISSION_DENIED,
            "decisions are recorded only by a human on the trusted local channel",
            related_ids=[params.decision_id],
        )
    decision = ctx.store.decision(params.decision_id)
    if decision is None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST, f"{params.decision_id} does not exist", related_ids=[params.decision_id]
        )
    _require_version(command, decision.record_version, decision.decision_id)
    if decision.disposition != "pending":
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{decision.decision_id} is already {decision.disposition}",
            related_ids=[decision.decision_id],
        )
    if decision.expires_at is not None and parse_timestamp(decision.expires_at) <= ctx.now:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{decision.decision_id} expired at {decision.expires_at}; request a new decision",
            related_ids=[decision.decision_id],
        )
    if decision.proposal_digest != params.proposal_digest:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            "the proposal digest does not match the proposal awaiting this decision; "
            "an approval cannot be carried over to different content",
            related_ids=[decision.decision_id],
            details={"awaiting": decision.proposal_digest, "received": params.proposal_digest},
        )
    new_version = decision.record_version + 1
    return Outcome(
        events=[
            DomainEvent(
                "decision.recorded",
                {
                    "decision_id": decision.decision_id,
                    "disposition": params.disposition,
                    "reason": params.reason,
                    "proposal_digest": params.proposal_digest,
                    "record_version": new_version,
                },
            )
        ],
        result={
            "decision_id": decision.decision_id,
            "disposition": params.disposition,
            "record_version": new_version,
        },
    )


# ---------------------------------------------------------- workspace capture --


@dataclass(frozen=True)
class PreparedCapture:
    result: CaptureResult
    manifest_blob: BlobInfo


def prepare_workspace_capture(ctx: PrepareContext, command: Command) -> PreparedCapture:
    """Hash and store the source tree before the write lock is taken."""
    params: WorkspaceCaptureParams = command.params  # type: ignore[assignment]
    project = ctx.store.project()
    if project is None or project.project_id != command.envelope.project_id:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "project_id does not match this controller's project")
    root = _resolve_source_root(project, params.source_root, ctx.trust_store)
    if ctx.blobs is None:
        raise ForgeError(ErrorCode.MISSING_CAPABILITY, "no artifact store is configured")
    result = capture_tree(root, ctx.blobs, CapturePolicy())
    manifest_blob = ctx.blobs.put_bytes(result.manifest.canonical_bytes())
    if manifest_blob.digest != result.manifest_digest:
        raise ForgeError(ErrorCode.INTERNAL_ERROR, "stored manifest digest does not match the computed one")
    return PreparedCapture(result=result, manifest_blob=manifest_blob)


def _resolve_source_root(project: Project, label: str, trust_store: TrustStore | None):  # noqa: ANN202
    if label not in project.source_roots:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"source root {label!r} is not declared by this project",
            details={"declared": project.source_roots},
        )
    if trust_store is None:
        raise ForgeError(ErrorCode.MISSING_CAPABILITY, "no trust store is configured to resolve source roots")
    root = trust_store.source_root(project.project_id, label)
    if root is None:
        raise ForgeError(
            ErrorCode.PERMISSION_DENIED,
            f"source root {label!r} has not been approved for this project; "
            "approve it from the trusted local interface",
        )
    return root


def workspace_capture(ctx: HandlerContext, command: Command) -> Outcome:
    params: WorkspaceCaptureParams = command.params  # type: ignore[assignment]
    prepared: PreparedCapture = ctx.prepared
    manifest = prepared.result.manifest
    artifact_id = new_id("artifact")
    snapshot_id = new_id("snapshot")
    artifact = {
        "artifact_id": artifact_id,
        "digest": prepared.manifest_blob.digest,
        "size": prepared.manifest_blob.size,
        "content_type": "application/json",
        "artifact_kind": "snapshot_manifest",
        "sensitivity": "project",
        "retention": "keep",
        "producer_ref": None,
    }
    snapshot = {
        "snapshot_id": snapshot_id,
        "manifest_digest": prepared.result.manifest_digest,
        "manifest_artifact_id": artifact_id,
        "source_root": params.source_root,
        "base_commit": manifest.base_commit,
        "capture_policy_revision": manifest.capture_policy_revision,
        "file_count": len(manifest.entries),
        "total_bytes": manifest.total_bytes,
        "excluded_count": len(manifest.excluded),
        "dirty_paths": params.dirty_paths,
        "parent_snapshot_id": None,
        "origin": "capture",
    }
    return Outcome(
        events=[
            DomainEvent("artifact.registered", {"artifact": artifact}),
            DomainEvent("snapshot.captured", {"snapshot": snapshot}),
        ],
        result={
            "snapshot_id": snapshot_id,
            "manifest_digest": prepared.result.manifest_digest,
            "file_count": len(manifest.entries),
            "excluded": [entry.model_dump() for entry in manifest.excluded[:50]],
            "excluded_count": len(manifest.excluded),
            "dirty_paths_not_captured": params.dirty_paths,
            "capture_attempts": prepared.result.attempts,
        },
    )


# ------------------------------------------------- evaluator suites (F05) --


def _check_suite_reference(ctx: HandlerContext, parameters: dict[str, Any]) -> None:
    """A black-box criterion must name an admitted suite by artifact ID and digest."""
    artifact_id, digest = parameters.get("suite_artifact_id"), parameters.get("suite_digest")
    if not isinstance(artifact_id, str) or not isinstance(digest, str):
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            "black_box_cases criteria need parameters suite_artifact_id and suite_digest",
        )
    artifact = ctx.store.artifact(artifact_id)
    if artifact is None or artifact.artifact_kind != "evaluator_suite":
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{artifact_id} is not an admitted evaluator suite")
    if artifact.digest != digest:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "suite_digest does not match the admitted suite")


@dataclass(frozen=True)
class PreparedSuite:
    blob: BlobInfo
    suite_id: str
    case_count: int


def prepare_suite_admit(ctx: PrepareContext, command: Command) -> PreparedSuite:
    from forge.assessment.suite import EvaluatorSuite  # local: assessment depends on records

    params: SuiteAdmitParams = command.params  # type: ignore[assignment]
    try:
        suite = EvaluatorSuite.model_validate(params.suite)
    except ValueError as error:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"invalid evaluator suite: {str(error)[:500]}") from None
    if ctx.blobs is None:
        raise ForgeError(ErrorCode.MISSING_CAPABILITY, "no artifact store is configured")
    blob = ctx.blobs.put_bytes(suite.canonical_bytes())
    return PreparedSuite(blob=blob, suite_id=suite.suite_id, case_count=len(suite.cases))


def suite_admit(ctx: HandlerContext, command: Command) -> Outcome:
    prepared: PreparedSuite = ctx.prepared
    artifact_id = new_id("artifact")
    artifact = {
        "artifact_id": artifact_id,
        "digest": prepared.blob.digest,
        "size": prepared.blob.size,
        "content_type": "application/json",
        "artifact_kind": "evaluator_suite",
        "sensitivity": "sensitive",  # expected answers: keep out of model context
        "retention": "keep",
        "producer_ref": None,
    }
    return Outcome(
        events=[DomainEvent("artifact.registered", {"artifact": artifact})],
        result={
            "suite_artifact_id": artifact_id,
            "suite_digest": prepared.blob.digest,
            "suite_id": prepared.suite_id,
            "case_count": prepared.case_count,
        },
    )


def artifact_register(ctx: HandlerContext, command: Command) -> Outcome:
    params: ArtifactRegisterParams = command.params  # type: ignore[assignment]
    if ctx.blobs is None or not ctx.blobs.exists(params.digest):
        # A row must never point at bytes that were not published first.
        raise ForgeError(ErrorCode.ARTIFACT_MISSING, f"no stored blob has digest {params.digest}")
    artifact_id = new_id("artifact")
    artifact = {"artifact_id": artifact_id, **params.model_dump(mode="json")}
    return Outcome(
        events=[DomainEvent("artifact.registered", {"artifact": artifact})],
        result={"artifact_id": artifact_id},
    )


def _require_snapshot(ctx: HandlerContext, snapshot_id: str) -> None:
    if ctx.store.snapshot(snapshot_id) is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist", related_ids=[snapshot_id])


def _require_artifacts(ctx: HandlerContext, artifact_ids: list[str]) -> None:
    for artifact_id in artifact_ids:
        if ctx.store.artifact(artifact_id) is None:
            raise ForgeError(ErrorCode.ARTIFACT_MISSING, f"{artifact_id} does not exist", related_ids=[artifact_id])


def evidence_record_execution(ctx: HandlerContext, command: Command) -> Outcome:
    params: EvidenceRecordExecutionParams = command.params  # type: ignore[assignment]
    _require_snapshot(ctx, params.snapshot_id)
    _require_artifacts(ctx, params.artifact_refs)
    evidence_id = new_id("evidence")
    item = {
        "evidence_id": evidence_id,
        "origin": "execution",
        "job_id": params.job_id,
        "attempt_number": params.attempt_number,
        "snapshot_id": params.snapshot_id,
        "tool_ref": params.tool_ref,
        "environment_digest": params.environment_digest,
        "evaluator_digest": params.evaluator_digest,
        "subject_ref": None,
        "reviewer_id": None,
        "observation_type": params.observation_type,
        "observation": params.observation,
        "artifact_refs": params.artifact_refs,
    }
    return Outcome(events=[DomainEvent("evidence.recorded", {"evidence": item})], result={"evidence_id": evidence_id})


def evidence_record_review(ctx: HandlerContext, command: Command) -> Outcome:
    """Attributed human review: no invented job, attempt or snapshot."""
    params: EvidenceRecordReviewParams = command.params  # type: ignore[assignment]
    if not ctx.actor.is_trusted_human:
        raise ForgeError(ErrorCode.PERMISSION_DENIED, "review evidence is recorded by the human reviewer")
    kind = id_prefix(params.subject_ref.id)
    if kind == "artifact":
        if ctx.store.artifact(params.subject_ref.id) is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.subject_ref.id} does not exist")
    elif kind == "snapshot":
        _require_snapshot(ctx, params.subject_ref.id)
    else:
        _check_ref(ctx, params.subject_ref, kind)
    evidence_id = new_id("evidence")
    item = {
        "evidence_id": evidence_id,
        "origin": "review",
        "job_id": None,
        "attempt_number": None,
        "snapshot_id": None,
        "tool_ref": None,
        "environment_digest": None,
        "evaluator_digest": None,
        "subject_ref": params.subject_ref.model_dump(),
        "reviewer_id": ctx.actor.actor_id,
        "observation_type": params.observation_type,
        "observation": params.observation,
        "artifact_refs": [],
    }
    return Outcome(events=[DomainEvent("evidence.recorded", {"evidence": item})], result={"evidence_id": evidence_id})


def assessment_record(ctx: HandlerContext, command: Command) -> Outcome:
    params: AssessmentRecordParams = command.params  # type: ignore[assignment]
    criterion = ctx.store.criterion(params.criterion_ref.id)
    if criterion is None or params.criterion_ref.revision > criterion.revision:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{params.criterion_ref.id} revision {params.criterion_ref.revision} does not exist",
        )
    if params.snapshot_id is not None:
        _require_snapshot(ctx, params.snapshot_id)
    for evidence_id in params.evidence_refs:
        if ctx.store.evidence(evidence_id) is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{evidence_id} does not exist", related_ids=[evidence_id])
    if params.assessment_result in ("pass", "fail") and not params.evidence_refs:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "a pass or fail assessment must cite evidence")
    if params.supersedes_assessment_id and ctx.store.assessment(params.supersedes_assessment_id) is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.supersedes_assessment_id} does not exist")
    assessment_id = new_id("assessment")
    item = {"assessment_id": assessment_id, **params.model_dump(mode="json")}
    return Outcome(
        events=[DomainEvent("assessment.recorded", {"assessment": item})],
        result={"assessment_id": assessment_id, "assessment_result": params.assessment_result},
    )


def finding_record(ctx: HandlerContext, command: Command) -> Outcome:
    params: FindingRecordParams = command.params  # type: ignore[assignment]
    if params.snapshot_id is not None:
        _require_snapshot(ctx, params.snapshot_id)
    finding_id = new_id("finding")
    item = {"finding_id": finding_id, **params.model_dump(mode="json")}
    return Outcome(events=[DomainEvent("finding.recorded", {"finding": item})], result={"finding_id": finding_id})


# ----------------------------------------------------------- engine (F06) --


def plan_apply(ctx: HandlerContext, command: Command) -> Outcome:
    params: PlanApplyParams = command.params  # type: ignore[assignment]
    assert ctx.project is not None
    validated = validate_plan(ctx.store, ctx.project, params, trust_store=ctx.trust_store, now=ctx.now)
    key_to_id = validated.key_to_id
    events: list[DomainEvent] = []
    for task in params.add_tasks:
        depends_on = [key_to_id.get(ref, ref) for ref in task.depends_on]
        record = {
            "task_id": key_to_id[task.key],
            "task_type": task.task_type,
            "title": task.title,
            "status": "queued",
            "requirement_refs": [ref.model_dump() for ref in task.requirement_refs],
            "criterion_refs": _with_compliance_gate(ctx, task.task_type,
                                                    [ref.model_dump() for ref in task.criterion_refs]),
            "input_refs": [ref.model_dump() for ref in task.input_refs],
            "authority_ref": task.authority_ref,
            "budget": task.budget.model_dump(),
            "workflow_revision": ctx.project.plan_revision + 1,
            "supersedes_task_id": task.supersedes_task_id,
        }
        events.append(DomainEvent("task.created", {"task": record, "depends_on": depends_on}))
    # Edges between new tasks travel with task.created; edges added to new
    # tasks from this change's dependency list are applied after creation.
    versions: dict[str, int] = {}
    for link in params.add_dependencies:
        source = key_to_id.get(link.task, link.task)
        target = key_to_id.get(link.depends_on, link.depends_on)
        existing = ctx.store.task(source)
        version = versions.get(source, existing.record_version if existing else 1) + 1
        versions[source] = version
        events.append(
            DomainEvent(
                "task.dependency_added",
                {"task_id": source, "depends_on_task_id": target, "record_version": version},
            )
        )
    superseded = []
    for task in params.add_tasks:
        if task.supersedes_task_id is None:
            continue
        old = ctx.store.task(task.supersedes_task_id)
        version = versions.get(old.task_id, old.record_version) + 1
        events.append(
            DomainEvent(
                "task.superseded",
                {"task_id": old.task_id, "superseded_by_task_id": key_to_id[task.key], "record_version": version},
            )
        )
        if old.status not in TERMINAL_TASK_STATUSES:
            version += 1
            events.append(_status_event(old, "cancelled", f"superseded by {key_to_id[task.key]}", version=version))
        versions[old.task_id] = version
        superseded.append(old.task_id)
    plan_revision = ctx.project.plan_revision + 1
    events.append(
        DomainEvent(
            "plan.revised",
            {
                "plan_revision": plan_revision,
                "summary": params.summary,
                "added_tasks": key_to_id,
                "added_dependencies": len(params.add_dependencies),
                "superseded": superseded,
            },
        )
    )
    return Outcome(events=events, result={"plan_revision": plan_revision, "task_ids": key_to_id})


def task_complete(ctx: HandlerContext, command: Command) -> Outcome:
    params: TaskCompleteParams = command.params  # type: ignore[assignment]
    task = ctx.store.task(params.task_id)
    if task is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.task_id} does not exist", related_ids=[params.task_id])
    _require_version(command, task.record_version, task.task_id)
    if task.status != "running":
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{task.task_id} is {task.status}; only a running task can complete",
            related_ids=[task.task_id],
        )
    if params.snapshot_id is not None:
        _require_snapshot(ctx, params.snapshot_id)
    criteria = {}
    assessments = []
    for ref in task.criterion_refs:
        criterion = ctx.store.criterion(ref.id)
        if criterion is not None:
            criteria[ref.id] = criterion
        assessments.extend(ctx.store.assessments(criterion_id=ref.id, snapshot_id=params.snapshot_id))
    evaluation = evaluate_completion(
        task,
        snapshot_id=params.snapshot_id,
        criteria=criteria,
        assessments=assessments,
        decisions=ctx.store.decisions(),
        acknowledged_by_trusted_actor=ctx.actor.is_trusted_human or ctx.actor.kind == "system",
        now=ctx.now,
    )
    if not evaluation.eligible:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{task.task_id} is not complete: {evaluation.explanation}",
            related_ids=[task.task_id],
            details={"evaluation": evaluation.model_dump(mode="json")},
        )
    completion = {**evaluation.model_dump(mode="json"), "requested_reason": params.reason}
    new_version = task.record_version + 1
    return Outcome(
        events=[
            DomainEvent(
                "task.completed",
                {"task_id": task.task_id, "from_status": task.status, "record_version": new_version,
                 "completion": completion},
            )
        ],
        result={"task_id": task.task_id, "status": "completed", "reason": evaluation.reason,
                "record_version": new_version},
    )


# ------------------------------------------------------------- jobs (F07) --


def _parse_spec(raw: dict[str, Any], digest: str):  # noqa: ANN202 - JobSpec
    from forge.contracts.jobs import JobSpec

    try:
        spec = JobSpec.model_validate(raw)
    except ValueError as error:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"invalid job spec: {str(error)[:300]}") from None
    if spec.digest() != digest:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "job_digest does not match the job spec")
    return spec


def _check_job_inputs(ctx: HandlerContext, spec, task) -> None:  # noqa: ANN001
    assert ctx.project is not None
    if spec.project_id != ctx.project.project_id or spec.controller_id != ctx.controller_id:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "job spec names another project or controller")
    if task.task_type != "execute":
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{task.task_id} is a {task.task_type} task and runs without jobs")
    if not any(s.manifest_digest == spec.snapshot_digest for s in ctx.store.snapshots()):
        raise ForgeError(ErrorCode.INVALID_REQUEST, "job snapshot_digest does not name a recorded snapshot")
    if task.authority_ref is not None and task.authority_ref not in spec.grant_ids:
        raise ForgeError(ErrorCode.PERMISSION_DENIED, f"job must run under the task's grant {task.authority_ref}")
    # Authority is rechecked at the moment work is admitted: a grant revoked
    # while the job waited in a queue stops it here (EX-T02).
    for grant_id in spec.grant_ids:
        _require_grant(ctx, grant_id)


def _job_record(spec, digest: str) -> dict[str, Any]:  # noqa: ANN001
    return {
        "job_id": spec.job_id,
        "attempt_number": spec.attempt_number,
        "task_id": spec.task_id,
        "host_id": spec.host_id,
        "lease_epoch": spec.lease_epoch,
        "spec": spec.model_dump(mode="json"),
        "job_digest": digest,
        "effect_class": spec.effect_class,
        "retry_class": spec.retry_class,
    }


def job_create(ctx: HandlerContext, command: Command) -> Outcome:
    params: JobCreateParams = command.params  # type: ignore[assignment]
    spec = _parse_spec(params.spec, params.job_digest)
    if spec.attempt_number != 1 or spec.lease_epoch != 1:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "new jobs start at attempt 1, epoch 1; use job.retry")
    task = ctx.store.task(spec.task_id)
    if task is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{spec.task_id} does not exist")
    _check_job_inputs(ctx, spec, task)
    if task.status != "queued":
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{task.task_id} is {task.status}; a job starts a queued task")
    unfinished = [d for d in task.depends_on if (t := ctx.store.task(d)) is None or t.status != "completed"]
    if unfinished:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{task.task_id} is not ready", related_ids=unfinished)
    if ctx.store.job(spec.job_id) is not None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{spec.job_id} already exists")
    return Outcome(
        events=[
            DomainEvent("job.created", {"job": _job_record(spec, params.job_digest), "outbox_id": new_id("command")}),
            _status_event(task, "running", f"job {spec.job_id} admitted"),
        ],
        result={"job_id": spec.job_id, "attempt_number": 1, "lease_epoch": 1},
    )


def _fenced_job(ctx: HandlerContext, job_id: str, attempt: int, epoch: int):  # noqa: ANN202 - Job
    job = ctx.store.job(job_id, attempt)
    latest = ctx.store.job(job_id)
    if job is None or latest is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{job_id} attempt {attempt} does not exist")
    if latest.attempt_number != attempt or job.lease_epoch != epoch:
        raise ForgeError(
            ErrorCode.STALE_VERSION,
            f"fenced: {job_id} is at attempt {latest.attempt_number} epoch {latest.lease_epoch}; "
            f"a report for attempt {attempt} epoch {epoch} cannot change it",
            related_ids=[job_id],
            details={"reason": "fenced"},
        )
    return job


def job_receipt(ctx: HandlerContext, command: Command) -> Outcome:
    params: JobReceiptParams = command.params  # type: ignore[assignment]
    job = _fenced_job(ctx, params.job_id, params.attempt_number, params.lease_epoch)
    outbox = ctx.store.conn.execute(
        "SELECT outbox_id FROM outbox WHERE job_id = ? AND attempt_number = ? AND kind = ? AND state = 'pending'",
        (job.job_id, job.attempt_number, params.kind),
    ).fetchone()
    update: dict[str, Any] = {"job_id": job.job_id, "attempt_number": job.attempt_number}
    if outbox is not None:
        update["outbox_acknowledged"] = outbox[0]
    if params.kind == "submit" and job.execution_status == "queued":
        if params.accepted:
            update["execution_status"] = "leased"
        elif job.cancel_requested_at is not None:
            update["execution_status"] = "cancelled"
            update["outcome"] = {"cancelled_before_dispatch": True}
        else:
            update["execution_status"] = "failed"
            update["outcome"] = {"rejected_by_host": params.reason}
    return Outcome(events=[DomainEvent("job.updated", update)], result={"status": update.get("execution_status", job.execution_status)})


def job_observe(ctx: HandlerContext, command: Command) -> Outcome:
    from forge.contracts.jobs import TERMINAL_JOB_STATUSES

    params: JobObserveParams = command.params  # type: ignore[assignment]
    job = _fenced_job(ctx, params.job_id, params.attempt_number, params.lease_epoch)
    if params.sequence <= job.host_event_seq:
        return Outcome(events=[], result={"ignored": "duplicate"})
    update: dict[str, Any] = {"job_id": job.job_id, "attempt_number": job.attempt_number,
                              "host_event_seq": params.sequence}
    if job.execution_status not in TERMINAL_JOB_STATUSES:
        update["execution_status"] = params.state
    if params.kind in ("finished", "cancel_acknowledged", "lost"):
        update["outcome"] = params.payload
    return Outcome(events=[DomainEvent("job.updated", update)], result={"status": update.get("execution_status", job.execution_status)})


def job_retry(ctx: HandlerContext, command: Command) -> Outcome:
    params: JobRetryParams = command.params  # type: ignore[assignment]
    spec = _parse_spec(params.spec, params.job_digest)
    previous = ctx.store.job(spec.job_id, params.previous_attempt)
    latest = ctx.store.job(spec.job_id)
    if previous is None or latest is None or latest.attempt_number != params.previous_attempt:
        raise ForgeError(ErrorCode.STALE_VERSION, "retry must follow the latest attempt")
    if previous.execution_status not in ("lost", "failed"):
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"attempt {previous.attempt_number} is {previous.execution_status}")
    if spec.attempt_number != previous.attempt_number + 1 or spec.lease_epoch != previous.lease_epoch + 1:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "a retry needs the next attempt number and a new lease epoch")
    if previous.retry_class == "never_automatic":
        raise ForgeError(ErrorCode.PERMISSION_DENIED, "this job's effects are never retried automatically")
    if previous.retry_class == "reconcile_before_retry":
        state = (previous.outcome or {}).get("target_state")
        if state not in ("absent", "not_attempted"):
            raise ForgeError(
                ErrorCode.PERMISSION_DENIED,
                "the external effect is not confirmed absent; reconcile before any retry",
            )
    task = ctx.store.task(spec.task_id)
    assert task is not None
    if spec.attempt_number > task.budget.max_attempts:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{task.task_id} has used its {task.budget.max_attempts} attempt(s)")
    _check_job_inputs(ctx, spec, task)
    return Outcome(
        events=[DomainEvent("job.created", {"job": _job_record(spec, params.job_digest), "outbox_id": new_id("command")})],
        result={"job_id": spec.job_id, "attempt_number": spec.attempt_number, "lease_epoch": spec.lease_epoch},
    )


def job_reconciliation_required(ctx: HandlerContext, command: Command) -> Outcome:
    params: JobReconciliationParams = command.params  # type: ignore[assignment]
    job = ctx.store.job(params.job_id, params.attempt_number)
    if job is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.job_id} attempt {params.attempt_number} does not exist")
    task = ctx.store.task(job.task_id)
    assert task is not None
    events = [DomainEvent("job.updated", {"job_id": job.job_id, "attempt_number": job.attempt_number,
                                          "execution_status": "lost", "reconciliation": "required"})]
    if task.status == "running":
        # Reconciling an uncertain external effect needs a person: per the
        # lifecycle table a running task waits for the user (it is not "blocked").
        events.append(_status_event(task, "waiting_for_user", f"reconciliation required: {params.reason}"))
    return Outcome(events=events, result={"task_id": task.task_id, "reconciliation": "required"})


def task_cancel(ctx: HandlerContext, command: Command) -> Outcome:
    """Request cancellation. Running work stops through the host, then is observed."""
    params: TaskCancelParams = command.params  # type: ignore[assignment]
    task = ctx.store.task(params.task_id)
    if task is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.task_id} does not exist")
    if task.status in TERMINAL_TASK_STATUSES:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{task.task_id} is already {task.status}")
    active = ctx.store.jobs(task_id=task.task_id, active_only=True)
    if not active:
        return Outcome(
            events=[_status_event(task, "cancelled", params.reason)],
            result={"task_id": task.task_id, "status": "cancelled"},
        )
    events = [
        DomainEvent("job.updated", {"job_id": job.job_id, "attempt_number": job.attempt_number,
                                    "cancel_requested": True, "outbox_cancel": new_id("command")})
        for job in active if job.cancel_requested_at is None
    ]
    return Outcome(
        events=events,
        result={"task_id": task.task_id, "status": "cancellation_pending",
                "jobs": [job.job_id for job in active]},
    )


def intent_submit(ctx: HandlerContext, command: Command) -> Outcome:
    """Record the request verbatim and create the planning task for it."""
    params: IntentSubmitParams = command.params  # type: ignore[assignment]
    _require_artifacts(ctx, params.artifact_refs)
    intent_id, task_id = new_id("intent"), new_id("task")
    first_line = " ".join(params.text.split())[:240]
    task = {
        "task_id": task_id, "task_type": "prepare", "title": f"Plan: {first_line}", "status": "queued",
        "requirement_refs": [], "criterion_refs": [], "input_refs": [], "authority_ref": None,
        "budget": {"max_wall_ms": 3_600_000, "max_attempts": 3}, "workflow_revision": 1,
    }
    intent = {"intent_id": intent_id, "text": params.text, "mode": params.mode,
              "artifact_refs": params.artifact_refs, "planning_task_id": task_id}
    return Outcome(
        events=[DomainEvent("task.created", {"task": task, "depends_on": []}),
                DomainEvent("intent.submitted", {"intent": intent})],
        result={"intent_id": intent_id, "planning_task_id": task_id},
    )


def model_call_record(ctx: HandlerContext, command: Command) -> Outcome:
    params: ModelCallRecordParams = command.params  # type: ignore[assignment]
    if ctx.store.conn.execute("SELECT 1 FROM model_calls WHERE call_id = ?", (params.call_id,)).fetchone():
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"model call {params.call_id} is already recorded")
    return Outcome(events=[DomainEvent("model.call_recorded", {"call": params.model_dump(mode="json")})],
                   result={"call_id": params.call_id})


def snapshot_record_candidate(ctx: HandlerContext, command: Command) -> Outcome:
    """Record a candidate built from a parent snapshot plus proposed files."""
    from forge.workspace.snapshot import load_manifest

    params: SnapshotRecordCandidateParams = command.params  # type: ignore[assignment]
    parent = ctx.store.snapshot(params.parent_snapshot_id)
    if parent is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.parent_snapshot_id} does not exist")
    if ctx.blobs is None or not ctx.blobs.exists(params.manifest_digest):
        raise ForgeError(ErrorCode.ARTIFACT_MISSING, "candidate manifest was not stored first")
    manifest = load_manifest(ctx.blobs, params.manifest_digest)  # verifies canonical form and digest
    for entry in manifest.entries:
        if not ctx.blobs.exists(entry.digest):
            raise ForgeError(ErrorCode.ARTIFACT_MISSING, f"candidate file {entry.path} has no stored bytes")
    artifact_id, snapshot_id = new_id("artifact"), new_id("snapshot")
    artifact = {"artifact_id": artifact_id, "digest": params.manifest_digest, "size": params.manifest_size,
                "content_type": "application/json", "artifact_kind": "snapshot_manifest", "sensitivity": "project",
                "retention": "keep", "producer_ref": params.producer_ref}
    snapshot = {"snapshot_id": snapshot_id, "manifest_digest": params.manifest_digest,
                "manifest_artifact_id": artifact_id, "source_root": parent.source_root,
                "base_commit": manifest.base_commit, "capture_policy_revision": manifest.capture_policy_revision,
                "file_count": len(manifest.entries), "total_bytes": manifest.total_bytes,
                "excluded_count": len(manifest.excluded), "dirty_paths": [],
                "parent_snapshot_id": parent.snapshot_id, "origin": "candidate"}
    return Outcome(
        events=[DomainEvent("artifact.registered", {"artifact": artifact}),
                DomainEvent("snapshot.captured", {"snapshot": snapshot})],
        result={"snapshot_id": snapshot_id, "manifest_digest": params.manifest_digest},
    )


def proposal_create(ctx: HandlerContext, command: Command) -> Outcome:
    from forge.contracts.digests import digest_json
    from forge.workspace.snapshot import load_manifest

    params: ProposalCreateParams = command.params  # type: ignore[assignment]
    if params.task_id is not None and ctx.store.task(params.task_id) is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.task_id} does not exist", related_ids=[params.task_id])
    base, candidate = ctx.store.snapshot(params.base_snapshot_id), ctx.store.snapshot(params.candidate_snapshot_id)
    if base is None or candidate is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "base or candidate snapshot does not exist")
    if candidate.parent_snapshot_id != base.snapshot_id:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "the candidate was not built from this base snapshot")
    old = {e.path: e for e in load_manifest(ctx.blobs, base.manifest_digest).entries}
    new = {e.path: e for e in load_manifest(ctx.blobs, candidate.manifest_digest).entries}
    changes = []
    for path in sorted(set(old) | set(new)):
        before, after = old.get(path), new.get(path)
        if before is not None and after is not None and before.digest == after.digest and before.executable == after.executable:
            continue
        changes.append({"path": path, "base_digest": before.digest if before else None,
                        "new_digest": after.digest if after else None,
                        "change": "add" if before is None else ("delete" if after is None else "modify")})
    if not changes:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "the candidate makes no changes")
    # Tests, evaluator files and forge.toml define success; an implementation proposal may not change them,
    # which needs its own reviewed test_change proposal (INT-T10). visibility.py holds the one definition.
    protected = [c["path"] for c in changes if is_protected(c["path"])]
    if protected and params.kind == "implementation":
        raise ForgeError(
            ErrorCode.PERMISSION_DENIED,
            "an implementation proposal may not change tests or evaluator configuration; "
            "submit a separate test_change proposal for review",
            details={"protected_paths": protected},
        )
    digest = digest_json({"base_manifest": base.manifest_digest, "changes": changes})
    proposal_id = new_id("proposal")
    item = {"proposal_id": proposal_id, "task_id": params.task_id, "kind": params.kind,
            "base_snapshot_id": base.snapshot_id, "candidate_snapshot_id": candidate.snapshot_id,
            "proposal_digest": digest, "changes": changes}
    return Outcome(events=[DomainEvent("proposal.created", {"proposal": item})],
                   result={"proposal_id": proposal_id, "proposal_digest": digest, "changes": len(changes)})


def change_record_application(ctx: HandlerContext, command: Command) -> Outcome:
    params: ChangeRecordApplicationParams = command.params  # type: ignore[assignment]
    if ctx.store.proposal(params.proposal_id) is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{params.proposal_id} does not exist")
    return Outcome(events=[DomainEvent("change.application_recorded", params.model_dump(mode="json"))],
                   result={"apply_id": params.apply_id, "outcome": params.outcome})


Preparer = Callable[[PrepareContext, Command], Any]

PREPARERS: dict[str, Preparer] = {
    "workspace.capture": prepare_workspace_capture,
    "suite.admit": prepare_suite_admit,
}


HANDLERS: dict[str, Handler] = {
    "project.create": project_create,
    "requirement.create": requirement_create,
    "criterion.create": criterion_create,
    "task.create": task_create,
    "task.add_dependency": task_add_dependency,
    "task.transition": task_transition,
    "decision.request": decision_request,
    "decision.record": decision_record,
    "workspace.capture": workspace_capture,
    "suite.admit": suite_admit,
    "artifact.register": artifact_register,
    "evidence.record_execution": evidence_record_execution,
    "evidence.record_review": evidence_record_review,
    "assessment.record": assessment_record,
    "finding.record": finding_record,
    "plan.apply": plan_apply,
    "task.complete": task_complete,
    "job.create": job_create,
    "job.receipt": job_receipt,
    "job.observe": job_observe,
    "job.retry": job_retry,
    "job.reconciliation_required": job_reconciliation_required,
    "task.cancel": task_cancel,
    "intent.submit": intent_submit,
    "model.call_record": model_call_record,
    "snapshot.record_candidate": snapshot_record_candidate,
    "proposal.create": proposal_create,
    "change.record_application": change_record_application,
}

# Execution hosts report observations; they do not edit plans or decide.
ALLOWED_ACTOR_KINDS: dict[str, frozenset[str]] = {
    method: frozenset({"human", "model", "system"}) for method in HANDLERS
}
# Only FORGE's own trusted services record what was measured. A client or a
# model cannot write "pass" into the record by calling a method.
for _method in (
    "artifact.register", "evidence.record_execution", "assessment.record", "finding.record",
    "job.create", "job.receipt", "job.observe", "job.retry", "job.reconciliation_required",
    "model.call_record", "snapshot.record_candidate", "proposal.create", "change.record_application",
):
    ALLOWED_ACTOR_KINDS[_method] = frozenset({"system"})
# Generated tests stay proposals until a person (or FORGE itself) admits them.
ALLOWED_ACTOR_KINDS["suite.admit"] = frozenset({"human", "system"})
ALLOWED_ACTOR_KINDS["evidence.record_review"] = frozenset({"human"})

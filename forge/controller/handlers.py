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
    TaskTransitionParams,
    WorkspaceCaptureParams,
)
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import id_prefix, new_id
from forge.contracts.records import TERMINAL_TASK_STATUSES, Project
from forge.engine.graph import find_cycle
from forge.engine.lifecycle import is_allowed
from forge.record.events import DomainEvent
from forge.record.store import ProjectStore
from forge.workspace.snapshot import CapturePolicy, CaptureResult, capture_tree


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
    if params.input_refs:
        raise ForgeError(
            ErrorCode.MISSING_CAPABILITY,
            "snapshot and artifact inputs are not supported yet (ticket F03)",
        )
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
        "criterion_refs": [ref.model_dump() for ref in params.criterion_refs],
        "input_refs": [],
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
            ErrorCode.MISSING_CAPABILITY,
            "tasks complete only through the completion evaluator (tickets F05/F06)",
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


Preparer = Callable[[PrepareContext, Command], Any]

PREPARERS: dict[str, Preparer] = {
    "workspace.capture": prepare_workspace_capture,
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
}

# Execution hosts report observations; they do not edit plans or decide.
ALLOWED_ACTOR_KINDS: dict[str, frozenset[str]] = {
    method: frozenset({"human", "model", "system"}) for method in HANDLERS
}

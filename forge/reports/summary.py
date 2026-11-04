"""project.summary: the "standup" skill as deterministic code (ticket D01).

A standup answers: what got done, what is happening now, and what is stuck.
Every answer already exists in FORGE's records, so no language model is
involved. The summary is a pure function of:

- the project records as of one event sequence number (as_of), and
- the events in a window (since, as_of], and
- an explicit evaluation time (only used to mark decisions as expired).

Same inputs, same summary, on any machine. A test proves that a summary
computed from the live tables equals one computed by replaying the log.

Readiness is computed, never stored: a queued task is ready when every task
it depends on is completed. "Ready" does not mean "dispatchable": an execute
task also needs job admission and a host (later tickets), so the summary
shows the task type and lets the reader see the difference.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import Field

from forge.contracts.base import RevisionRef, StrictModel
from forge.contracts.clock import Timestamp, format_timestamp, parse_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import DecisionId, ProjectId, TaskId
from forge.contracts.records import DecisionKind, Task, TaskStatus, TaskType
from forge.contracts.versions import SCHEMA_VERSION
from forge.record.store import MAX_PAGE, ProjectStore, RecordReader

ALL_STATUSES: tuple[str, ...] = (
    "queued", "running", "blocked", "waiting_for_user", "completed", "failed", "cancelled"
)
DEFAULT_SECTION_LIMIT = 50

Item = TypeVar("Item")


class Section(StrictModel, Generic[Item]):
    """A bounded list: at most `limit` items shown, `total` says how many exist."""

    total: int = Field(ge=0)
    items: list[Item]

    @property
    def hidden(self) -> int:
        return self.total - len(self.items)


class TaskLine(StrictModel):
    task_id: TaskId
    title: str
    task_type: TaskType
    status: TaskStatus
    record_version: int


class TaskChangeLine(TaskLine):
    """A task plus the status change that put it in this section."""

    event_seq: int
    occurred_at: Timestamp
    reason: str


class WaitingLine(TaskLine):
    waiting_on: list[TaskId]


class DecisionLine(StrictModel):
    decision_id: DecisionId
    decision_kind: DecisionKind
    question: str
    subject_ref: RevisionRef
    expires_at: Timestamp | None
    expired: bool
    requested_at: Timestamp


class DecidedLine(StrictModel):
    decision_id: DecisionId
    decision_kind: DecisionKind
    question: str
    disposition: str
    reason: str
    event_seq: int
    occurred_at: Timestamp


class ProjectSummary(StrictModel):
    schema_version: str
    project_id: ProjectId
    name: str
    objective: str
    since_event_seq: int = Field(ge=0)
    as_of_event_seq: int = Field(ge=0)
    evaluated_at: Timestamp
    status_counts: dict[str, int]
    done: Section[TaskChangeLine]
    ended: Section[TaskChangeLine]
    running: Section[TaskLine]
    ready: Section[TaskLine]
    waiting_on_tasks: Section[WaitingLine]
    blocked: Section[TaskChangeLine]
    waiting_for_user: Section[TaskChangeLine]
    pending_decisions: Section[DecisionLine]
    decided: Section[DecidedLine]
    activity: dict[str, int]


def build_summary(
    store: ProjectStore,
    *,
    now: datetime,
    since_seq: int = 0,
    as_of_seq: int | None = None,
    limit: int = DEFAULT_SECTION_LIMIT,
    force_replay: bool = False,
) -> ProjectSummary:
    """Summarize the project as of `as_of_seq`, reporting changes after `since_seq`.

    The live tables are used when as_of is the latest event; otherwise the
    project is rebuilt from the event log up to as_of. `force_replay` makes
    the replay path run even for the latest event (used to prove both paths
    agree).
    """
    latest = store.last_event_seq()
    as_of = latest if as_of_seq is None else int(as_of_seq)
    since = int(since_seq)
    if not 0 <= as_of <= latest:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST, f"as_of {as_of} is outside this project's log (0 to {latest})"
        )
    if not 0 <= since <= as_of:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"since {since} must be between 0 and as_of {as_of}")
    limit = max(1, min(int(limit), MAX_PAGE))

    if as_of == latest and not force_replay:
        return _summarize(store, now=now, since=since, as_of=as_of, limit=limit)
    with store.replayed(as_of) as reader:
        return _summarize(reader, now=now, since=since, as_of=as_of, limit=limit)


def _summarize(
    reader: RecordReader, *, now: datetime, since: int, as_of: int, limit: int
) -> ProjectSummary:
    project = reader.project()
    if project is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "no project exists at this point in the log")

    # One pass over status changes up to as_of: remember the latest change per
    # task (its reason explains the current status) and collect changes inside
    # the window (what got done or ended since the last standup).
    latest_change = {}
    done_events, ended_events = [], []
    for event in reader.iter_events(through_seq=as_of, event_types=("task.status_changed",)):
        latest_change[event.payload["task_id"]] = event
        if event.event_seq > since:
            if event.payload["to_status"] == "completed":
                done_events.append(event)
            elif event.payload["to_status"] in ("failed", "cancelled"):
                ended_events.append(event)

    tasks = {task.task_id: task for task in reader.iter_tasks()}

    def change_line(task: Task, event) -> TaskChangeLine:  # noqa: ANN001 - EventRecord
        return TaskChangeLine(
            **_task_fields(task),
            event_seq=event.event_seq,
            occurred_at=event.occurred_at,
            reason=event.payload["reason"],
        )

    def current_reason_lines(status: str) -> list[TaskChangeLine]:
        lines = []
        for task in tasks.values():
            if task.status == status:
                # A blocked or waiting task always reached that status through a
                # recorded status change, so the reason is never invented.
                lines.append(change_line(task, latest_change[task.task_id]))
        return sorted(lines, key=lambda line: (line.event_seq, line.task_id))

    ready, waiting = [], []
    for task in sorted(tasks.values(), key=lambda item: (item.created_at, item.task_id)):
        if task.status != "queued":
            continue
        unfinished = [dep for dep in task.depends_on if tasks[dep].status != "completed"]
        if unfinished:
            waiting.append(WaitingLine(**_task_fields(task), waiting_on=unfinished))
        else:
            ready.append(TaskLine(**_task_fields(task)))

    running = sorted(
        (TaskLine(**_task_fields(task)) for task in tasks.values() if task.status == "running"),
        key=lambda line: line.task_id,
    )

    pending = []
    for decision in reader.decisions(disposition="pending"):
        expired = decision.expires_at is not None and parse_timestamp(decision.expires_at) <= now
        pending.append(
            DecisionLine(
                decision_id=decision.decision_id,
                decision_kind=decision.decision_kind,
                question=decision.question,
                subject_ref=decision.subject_ref,
                expires_at=decision.expires_at,
                expired=expired,
                requested_at=decision.created_at,
            )
        )

    decided = []
    for event in reader.iter_events(after_seq=since, through_seq=as_of, event_types=("decision.recorded",)):
        decision = reader.decision(event.payload["decision_id"])
        assert decision is not None
        decided.append(
            DecidedLine(
                decision_id=decision.decision_id,
                decision_kind=decision.decision_kind,
                question=decision.question,
                disposition=event.payload["disposition"],
                reason=event.payload["reason"],
                event_seq=event.event_seq,
                occurred_at=event.occurred_at,
            )
        )

    activity: dict[str, int] = {}
    for event in reader.iter_events(after_seq=since, through_seq=as_of):
        activity[event.event_type] = activity.get(event.event_type, 0) + 1

    counts = dict.fromkeys(ALL_STATUSES, 0)
    counts.update(reader.status_counts())

    return ProjectSummary(
        schema_version=SCHEMA_VERSION,
        project_id=project.project_id,
        name=project.name,
        objective=project.objective,
        since_event_seq=since,
        as_of_event_seq=as_of,
        evaluated_at=format_timestamp(now),
        status_counts=counts,
        done=_section([change_line(tasks[e.payload["task_id"]], e) for e in done_events], limit),
        ended=_section([change_line(tasks[e.payload["task_id"]], e) for e in ended_events], limit),
        running=_section(running, limit),
        ready=_section(ready, limit),
        waiting_on_tasks=_section(waiting, limit),
        blocked=_section(current_reason_lines("blocked"), limit),
        waiting_for_user=_section(current_reason_lines("waiting_for_user"), limit),
        pending_decisions=_section(pending, limit),
        decided=_section(decided, limit),
        activity=dict(sorted(activity.items())),
    )


def _task_fields(task: Task) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "title": task.title,
        "task_type": task.task_type,
        "status": task.status,
        "record_version": task.record_version,
    }


def _section(items: Iterable[Item], limit: int) -> dict[str, object]:
    # Returned as plain data so ProjectSummary validates it against the exact
    # section type (Section[TaskLine], Section[DecisionLine], ...).
    collected = list(items)
    return {"total": len(collected), "items": collected[:limit]}

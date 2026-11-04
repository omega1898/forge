"""Projections: current-state tables derived from the event log.

Think of a bank account. The list of deposits and withdrawals (events) is the
truth. The balance (projection) is a convenience computed from that list. If
the balance is lost, you recompute it from the list. You never "fix" history
by editing the balance.

Each projector below applies exactly one event type. Projectors must:
- only touch projection tables,
- perform no network, file, or hardware side effects,
- be deterministic: the same events always produce the same rows.

That last property is tested by rebuilding all projections from the log and
comparing them with the live tables.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from forge.contracts.digests import canonical_json_bytes
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.records import EventRecord

Projector = Callable[[sqlite3.Connection, EventRecord], None]

# Order matters when clearing: children before parents (foreign keys).
PROJECTION_TABLES = ("task_dependencies", "decisions", "tasks", "records", "projects")


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _expect_one(cursor: sqlite3.Cursor, event: EventRecord, what: str) -> None:
    if cursor.rowcount != 1:
        raise ForgeError(
            ErrorCode.INTERNAL_ERROR,
            f"projection out of step applying event {event.event_seq} ({event.event_type}) to {what}",
            related_ids=[event.event_id],
        )


def _project_created(conn: sqlite3.Connection, event: EventRecord) -> None:
    project = event.payload["project"]
    conn.execute(
        "INSERT INTO projects (project_id, name, objective, controller_id, source_roots_json,"
        " profile_id, created_at, created_by, record_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            project["project_id"],
            project["name"],
            project["objective"],
            project["controller_id"],
            _json(project["source_roots"]),
            project["profile_id"],
            event.occurred_at,
            event.actor_id,
        ),
    )


def _record_created(kind: str) -> Projector:
    def apply(conn: sqlite3.Connection, event: EventRecord) -> None:
        body = event.payload[kind]
        conn.execute(
            "INSERT INTO records (record_id, record_kind, project_id, revision, body_json,"
            " created_at, created_by, record_version) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (
                body[f"{kind}_id"],
                kind,
                event.project_id,
                body["revision"],
                _json(body),
                event.occurred_at,
                event.actor_id,
            ),
        )

    return apply


def _task_created(conn: sqlite3.Connection, event: EventRecord) -> None:
    task = event.payload["task"]
    conn.execute(
        "INSERT INTO tasks (task_id, project_id, task_type, title, status, requirement_refs_json,"
        " criterion_refs_json, input_refs_json, authority_ref, budget_json, workflow_revision,"
        " created_at, created_by, updated_at, record_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            task["task_id"],
            event.project_id,
            task["task_type"],
            task["title"],
            task["status"],
            _json(task["requirement_refs"]),
            _json(task["criterion_refs"]),
            _json(task["input_refs"]),
            task["authority_ref"],
            _json(task["budget"]),
            task["workflow_revision"],
            event.occurred_at,
            event.actor_id,
            event.occurred_at,
        ),
    )
    for dependency in event.payload["depends_on"]:
        conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
            (task["task_id"], dependency),
        )


def _task_dependency_added(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    conn.execute(
        "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
        (payload["task_id"], payload["depends_on_task_id"]),
    )
    # Only advance the task if it is at exactly the version before this event.
    cursor = conn.execute(
        "UPDATE tasks SET updated_at = ?, record_version = ? WHERE task_id = ? AND record_version = ?",
        (event.occurred_at, payload["record_version"], payload["task_id"], payload["record_version"] - 1),
    )
    _expect_one(cursor, event, payload["task_id"])


def _task_status_changed(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    cursor = conn.execute(
        "UPDATE tasks SET status = ?, updated_at = ?, record_version = ?"
        " WHERE task_id = ? AND record_version = ? AND status = ?",
        (
            payload["to_status"],
            event.occurred_at,
            payload["record_version"],
            payload["task_id"],
            payload["record_version"] - 1,
            payload["from_status"],
        ),
    )
    _expect_one(cursor, event, payload["task_id"])


def _decision_requested(conn: sqlite3.Connection, event: EventRecord) -> None:
    decision = event.payload["decision"]
    conn.execute(
        "INSERT INTO decisions (decision_id, project_id, decision_kind, subject_ref_json, question,"
        " proposal_digest, disposition, reason, actor_id, authority_ref, expires_at, created_at,"
        " created_by, updated_at, record_version)"
        " VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?, ?, ?, 1)",
        (
            decision["decision_id"],
            event.project_id,
            decision["decision_kind"],
            _json(decision["subject_ref"]),
            decision["question"],
            decision["proposal_digest"],
            decision["authority_ref"],
            decision["expires_at"],
            event.occurred_at,
            event.actor_id,
            event.occurred_at,
        ),
    )


def _decision_recorded(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    cursor = conn.execute(
        "UPDATE decisions SET disposition = ?, reason = ?, actor_id = ?, updated_at = ?,"
        " record_version = ? WHERE decision_id = ? AND record_version = ? AND disposition = 'pending'",
        (
            payload["disposition"],
            payload["reason"],
            event.actor_id,
            event.occurred_at,
            payload["record_version"],
            payload["decision_id"],
            payload["record_version"] - 1,
        ),
    )
    _expect_one(cursor, event, payload["decision_id"])


PROJECTORS: dict[tuple[str, int], Projector] = {
    ("project.created", 1): _project_created,
    ("requirement.created", 1): _record_created("requirement"),
    ("criterion.created", 1): _record_created("criterion"),
    ("task.created", 1): _task_created,
    ("task.dependency_added", 1): _task_dependency_added,
    ("task.status_changed", 1): _task_status_changed,
    ("decision.requested", 1): _decision_requested,
    ("decision.recorded", 1): _decision_recorded,
}


def apply_event(conn: sqlite3.Connection, event: EventRecord) -> None:
    projector = PROJECTORS.get((event.event_type, event.event_schema_version))
    if projector is None:
        raise ForgeError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"no projector for event type {event.event_type} v{event.event_schema_version}",
            related_ids=[event.event_id],
        )
    projector(conn, event)


def dump_projections(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """All projection rows in a stable order, for comparison and inspection."""
    dump: dict[str, list[dict[str, Any]]] = {}
    for table in reversed(PROJECTION_TABLES):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()  # noqa: S608
        dump[table] = [dict(row) for row in rows]
    return dump


def parse_json_column(text: str | None) -> Any:
    return None if text is None else json.loads(text)

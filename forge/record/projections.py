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
PROJECTION_TABLES = (
    "outbox", "jobs", "plan_revisions", "findings", "assessments", "evidence",
    "task_dependencies", "decisions", "tasks", "snapshots", "artifacts", "records", "projects",
)


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
        " created_at, created_by, updated_at, record_version, supersedes_task_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
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
            task.get("supersedes_task_id"),
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


def _plan_revised(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    conn.execute(
        "INSERT INTO plan_revisions (plan_revision, project_id, summary_json, created_at, created_by)"
        " VALUES (?, ?, ?, ?, ?)",
        (payload["plan_revision"], event.project_id, _json(payload), event.occurred_at, event.actor_id),
    )
    cursor = conn.execute(
        "UPDATE projects SET plan_revision = ? WHERE project_id = ? AND plan_revision = ?",
        (payload["plan_revision"], event.project_id, payload["plan_revision"] - 1),
    )
    _expect_one(cursor, event, event.project_id)


def _task_superseded(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    cursor = conn.execute(
        "UPDATE tasks SET superseded_by_task_id = ?, updated_at = ?, record_version = ?"
        " WHERE task_id = ? AND record_version = ? AND superseded_by_task_id IS NULL",
        (payload["superseded_by_task_id"], event.occurred_at, payload["record_version"],
         payload["task_id"], payload["record_version"] - 1),
    )
    _expect_one(cursor, event, payload["task_id"])


def _task_completed(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    cursor = conn.execute(
        "UPDATE tasks SET status = 'completed', completion_json = ?, updated_at = ?, record_version = ?"
        " WHERE task_id = ? AND record_version = ? AND status = ?",
        (_json(payload["completion"]), event.occurred_at, payload["record_version"], payload["task_id"],
         payload["record_version"] - 1, payload["from_status"]),
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


def _artifact_registered(conn: sqlite3.Connection, event: EventRecord) -> None:
    artifact = event.payload["artifact"]
    conn.execute(
        "INSERT INTO artifacts (artifact_id, project_id, digest, size, content_type, artifact_kind,"
        " sensitivity, retention, producer_ref, created_at, created_by, record_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            artifact["artifact_id"],
            event.project_id,
            artifact["digest"],
            artifact["size"],
            artifact["content_type"],
            artifact["artifact_kind"],
            artifact["sensitivity"],
            artifact["retention"],
            artifact["producer_ref"],
            event.occurred_at,
            event.actor_id,
        ),
    )


def _artifact_tombstoned(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    cursor = conn.execute(
        "UPDATE artifacts SET tombstoned_at = ?, tombstone_reason = ?, record_version = ?"
        " WHERE artifact_id = ? AND record_version = ? AND tombstoned_at IS NULL",
        (
            event.occurred_at,
            payload["reason"],
            payload["record_version"],
            payload["artifact_id"],
            payload["record_version"] - 1,
        ),
    )
    _expect_one(cursor, event, payload["artifact_id"])


def _snapshot_captured(conn: sqlite3.Connection, event: EventRecord) -> None:
    snapshot = event.payload["snapshot"]
    conn.execute(
        "INSERT INTO snapshots (snapshot_id, project_id, manifest_digest, manifest_artifact_id,"
        " source_root, base_commit, capture_policy_revision, file_count, total_bytes,"
        " excluded_count, dirty_paths_json, parent_snapshot_id, origin, created_at, created_by,"
        " record_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            snapshot["snapshot_id"],
            event.project_id,
            snapshot["manifest_digest"],
            snapshot["manifest_artifact_id"],
            snapshot["source_root"],
            snapshot["base_commit"],
            snapshot["capture_policy_revision"],
            snapshot["file_count"],
            snapshot["total_bytes"],
            snapshot["excluded_count"],
            _json(snapshot["dirty_paths"]),
            snapshot["parent_snapshot_id"],
            snapshot["origin"],
            event.occurred_at,
            event.actor_id,
        ),
    )


def _evidence_recorded(conn: sqlite3.Connection, event: EventRecord) -> None:
    item = event.payload["evidence"]
    conn.execute(
        "INSERT INTO evidence (evidence_id, project_id, origin, job_id, attempt_number, snapshot_id,"
        " tool_ref, environment_digest, evaluator_digest, subject_ref_json, reviewer_id,"
        " observation_type, observation_json, artifact_refs_json, created_at, created_by,"
        " record_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            item["evidence_id"], event.project_id, item["origin"], item["job_id"],
            item["attempt_number"], item["snapshot_id"], item["tool_ref"],
            item["environment_digest"], item["evaluator_digest"],
            None if item["subject_ref"] is None else _json(item["subject_ref"]),
            item["reviewer_id"], item["observation_type"], _json(item["observation"]),
            _json(item["artifact_refs"]), event.occurred_at, event.actor_id,
        ),
    )


def _assessment_recorded(conn: sqlite3.Connection, event: EventRecord) -> None:
    item = event.payload["assessment"]
    conn.execute(
        "INSERT INTO assessments (assessment_id, project_id, criterion_id, criterion_revision,"
        " snapshot_id, evidence_refs_json, assessment_result, assessor_ref, limitations_json,"
        " summary_json, supersedes_assessment_id, created_at, created_by, record_version, recorded_seq)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (
            item["assessment_id"], event.project_id, item["criterion_ref"]["id"],
            item["criterion_ref"]["revision"], item["snapshot_id"], _json(item["evidence_refs"]),
            item["assessment_result"], item["assessor_ref"], _json(item["limitations"]),
            _json(item["summary"]), item["supersedes_assessment_id"], event.occurred_at, event.actor_id,
            event.event_seq,
        ),
    )


def _finding_recorded(conn: sqlite3.Connection, event: EventRecord) -> None:
    item = event.payload["finding"]
    conn.execute(
        "INSERT INTO findings (finding_id, project_id, rule_id, claim, severity, status,"
        " confirmation, path, line, fingerprint, snapshot_id, evidence_refs_json, created_at,"
        " created_by, record_version) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            item["finding_id"], event.project_id, item["rule_id"], item["claim"], item["severity"],
            item["confirmation"], item["path"], item["line"], item["fingerprint"], item["snapshot_id"],
            _json(item["evidence_refs"]), event.occurred_at, event.actor_id,
        ),
    )


def _job_created(conn: sqlite3.Connection, event: EventRecord) -> None:
    job = event.payload["job"]
    conn.execute(
        "INSERT INTO jobs (job_id, attempt_number, project_id, task_id, host_id, lease_epoch, spec_json,"
        " job_digest, effect_class, retry_class, execution_status, created_at, created_by, updated_at,"
        " record_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, 1)",
        (job["job_id"], job["attempt_number"], event.project_id, job["task_id"], job["host_id"],
         job["lease_epoch"], _json(job["spec"]), job["job_digest"], job["effect_class"], job["retry_class"],
         event.occurred_at, event.actor_id, event.occurred_at),
    )
    conn.execute(
        "INSERT INTO outbox (outbox_id, job_id, attempt_number, lease_epoch, kind, state, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'submit', 'pending', ?, ?)",
        (event.payload["outbox_id"], job["job_id"], job["attempt_number"], job["lease_epoch"],
         event.occurred_at, event.occurred_at),
    )


def _job_updated(conn: sqlite3.Connection, event: EventRecord) -> None:
    payload = event.payload
    sets, values = ["updated_at = ?", "record_version = record_version + 1"], [event.occurred_at]
    for column in ("execution_status", "host_event_seq", "reconciliation"):
        if column in payload:
            sets.append(f"{column} = ?")
            values.append(payload[column])
    if "outcome" in payload:
        sets.append("outcome_json = ?")
        values.append(_json(payload["outcome"]))
    if payload.get("cancel_requested"):
        sets.append("cancel_requested_at = ?")
        values.append(event.occurred_at)
    cursor = conn.execute(
        f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ? AND attempt_number = ?",  # noqa: S608 - fixed columns
        (*values, payload["job_id"], payload["attempt_number"]),
    )
    _expect_one(cursor, event, payload["job_id"])
    if payload.get("outbox_acknowledged"):
        conn.execute(
            "UPDATE outbox SET state = 'acknowledged', updated_at = ? WHERE outbox_id = ?",
            (event.occurred_at, payload["outbox_acknowledged"]),
        )
    if payload.get("outbox_cancel"):
        conn.execute(
            "INSERT INTO outbox (outbox_id, job_id, attempt_number, lease_epoch, kind, state, created_at,"
            " updated_at) SELECT ?, job_id, attempt_number, lease_epoch, 'cancel', 'pending', ?, ?"
            " FROM jobs WHERE job_id = ? AND attempt_number = ?",
            (payload["outbox_cancel"], event.occurred_at, event.occurred_at, payload["job_id"],
             payload["attempt_number"]),
        )


PROJECTORS: dict[tuple[str, int], Projector] = {
    ("project.created", 1): _project_created,
    ("requirement.created", 1): _record_created("requirement"),
    ("criterion.created", 1): _record_created("criterion"),
    ("task.created", 1): _task_created,
    ("task.dependency_added", 1): _task_dependency_added,
    ("task.status_changed", 1): _task_status_changed,
    ("decision.requested", 1): _decision_requested,
    ("decision.recorded", 1): _decision_recorded,
    ("artifact.registered", 1): _artifact_registered,
    ("artifact.tombstoned", 1): _artifact_tombstoned,
    ("snapshot.captured", 1): _snapshot_captured,
    ("evidence.recorded", 1): _evidence_recorded,
    ("assessment.recorded", 1): _assessment_recorded,
    ("finding.recorded", 1): _finding_recorded,
    ("plan.revised", 1): _plan_revised,
    ("task.superseded", 1): _task_superseded,
    ("task.completed", 1): _task_completed,
    ("job.created", 1): _job_created,
    ("job.updated", 1): _job_updated,
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

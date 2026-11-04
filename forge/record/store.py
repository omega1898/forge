"""ProjectStore: owns one project's database file, its lock, and typed reads.

Only the controller writes through this store. Other modules (editor, CLI,
model gateway, execution hosts) submit commands to the controller; they never
open state.db for writing.

Reads live in RecordReader so the same code can read either the live
database or a scratch copy rebuilt by replaying the event log up to a chosen
point ("what did the project look like at event 12?").
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from forge.contracts.clock import Clock, SystemClock
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.records import (
    Artifact,
    Assessment,
    Evidence,
    Finding,
    Job,
    Criterion,
    Decision,
    EventRecord,
    Project,
    Requirement,
    Snapshot,
    Task,
)
from forge.record.db import open_connection, write_transaction
from forge.record.events import row_to_event
from forge.record.lock import ProjectLock
from forge.record.migrate import migrate
from forge.record.projections import PROJECTION_TABLES, apply_event, dump_projections

DEFAULT_PAGE = 50
MAX_PAGE = 200


class RecordReader:
    """Typed, read-only queries over one project database connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def project(self) -> Project | None:
        row = self.conn.execute("SELECT * FROM projects").fetchone()
        if row is None:
            return None
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            objective=row["objective"],
            controller_id=row["controller_id"],
            source_roots=json.loads(row["source_roots_json"]),
            profile_id=row["profile_id"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            record_version=row["record_version"],
            last_event_seq=self.last_event_seq(),
            plan_revision=row["plan_revision"],
        )

    def last_event_seq(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(event_seq), 0) FROM events").fetchone()[0]

    def _record_row(self, record_id: str, kind: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM records WHERE record_id = ? AND record_kind = ?", (record_id, kind)
        ).fetchone()

    def requirement(self, requirement_id: str) -> Requirement | None:
        row = self._record_row(requirement_id, "requirement")
        if row is None:
            return None
        return Requirement(**_common(row), **json.loads(row["body_json"]))

    def criterion(self, criterion_id: str) -> Criterion | None:
        row = self._record_row(criterion_id, "criterion")
        if row is None:
            return None
        return Criterion(**_common(row), **json.loads(row["body_json"]))

    def task(self, task_id: str) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return None if row is None else self._task_from_row(row)

    def tasks(
        self, *, status: str | None = None, after_task_id: str | None = None, limit: int = DEFAULT_PAGE
    ) -> list[Task]:
        """A bounded page of tasks ordered by ID (opaque cursor = last task ID seen)."""
        limit = max(1, min(int(limit), MAX_PAGE))
        sql = "SELECT * FROM tasks WHERE task_id > ?"
        values: list[Any] = [after_task_id or ""]
        if status is not None:
            sql += " AND status = ?"
            values.append(status)
        sql += " ORDER BY task_id LIMIT ?"
        values.append(limit)
        return [self._task_from_row(row) for row in self.conn.execute(sql, values)]

    def iter_tasks(self, *, status: str | None = None) -> Iterator[Task]:
        """Every task (optionally of one status), fetched page by page."""
        after: str | None = None
        while page := self.tasks(status=status, after_task_id=after, limit=MAX_PAGE):
            yield from page
            after = page[-1].task_id

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")
        return {row[0]: row[1] for row in rows}

    def dependencies(self, task_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?"
            " ORDER BY depends_on_task_id",
            (task_id,),
        )
        return [row[0] for row in rows]

    def dependency_edges(self) -> list[tuple[str, str]]:
        rows = self.conn.execute("SELECT task_id, depends_on_task_id FROM task_dependencies")
        return [(row[0], row[1]) for row in rows]

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        return Task(
            **_common(row),
            task_id=row["task_id"],
            task_type=row["task_type"],
            title=row["title"],
            status=row["status"],
            requirement_refs=json.loads(row["requirement_refs_json"]),
            criterion_refs=json.loads(row["criterion_refs_json"]),
            input_refs=json.loads(row["input_refs_json"]),
            authority_ref=row["authority_ref"],
            budget=json.loads(row["budget_json"]),
            workflow_revision=row["workflow_revision"],
            depends_on=self.dependencies(row["task_id"]),
            updated_at=row["updated_at"],
            supersedes_task_id=row["supersedes_task_id"],
            superseded_by_task_id=row["superseded_by_task_id"],
            completion=None if row["completion_json"] is None else json.loads(row["completion_json"]),
        )

    def decision(self, decision_id: str) -> Decision | None:
        row = self.conn.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return None if row is None else _decision_from_row(row)

    def decisions(self, *, disposition: str | None = None) -> list[Decision]:
        sql = "SELECT * FROM decisions"
        values: list[Any] = []
        if disposition is not None:
            sql += " WHERE disposition = ?"
            values.append(disposition)
        sql += " ORDER BY created_at, decision_id"
        return [_decision_from_row(row) for row in self.conn.execute(sql, values)]

    def artifact(self, artifact_id: str) -> Artifact | None:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            return None
        return Artifact(
            **_common(row),
            artifact_id=row["artifact_id"],
            digest=row["digest"],
            size=row["size"],
            content_type=row["content_type"],
            artifact_kind=row["artifact_kind"],
            sensitivity=row["sensitivity"],
            retention=row["retention"],
            producer_ref=row["producer_ref"],
            tombstoned_at=row["tombstoned_at"],
            tombstone_reason=row["tombstone_reason"],
        )

    def snapshot(self, snapshot_id: str) -> Snapshot | None:
        row = self.conn.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def snapshots(self) -> list[Snapshot]:
        rows = self.conn.execute("SELECT * FROM snapshots ORDER BY created_at, snapshot_id")
        return [_snapshot_from_row(row) for row in rows]

    def evidence(self, evidence_id: str) -> Evidence | None:
        row = self.conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
        return None if row is None else _evidence_from_row(row)

    def assessment(self, assessment_id: str) -> Assessment | None:
        row = self.conn.execute(
            "SELECT * FROM assessments WHERE assessment_id = ?", (assessment_id,)
        ).fetchone()
        return None if row is None else _assessment_from_row(row)

    def assessments(self, *, criterion_id: str | None = None, snapshot_id: str | None = None) -> list[Assessment]:
        sql = "SELECT * FROM assessments WHERE 1 = 1"
        values: list[Any] = []
        if criterion_id is not None:
            sql += " AND criterion_id = ?"
            values.append(criterion_id)
        if snapshot_id is not None:
            sql += " AND snapshot_id = ?"
            values.append(snapshot_id)
        sql += " ORDER BY recorded_seq, assessment_id"
        return [_assessment_from_row(row) for row in self.conn.execute(sql, values)]

    def findings(self, *, snapshot_id: str | None = None) -> list[Finding]:
        sql = "SELECT * FROM findings"
        values: list[Any] = []
        if snapshot_id is not None:
            sql += " WHERE snapshot_id = ?"
            values.append(snapshot_id)
        sql += " ORDER BY created_at, finding_id"
        return [_finding_from_row(row) for row in self.conn.execute(sql, values)]

    def job(self, job_id: str, attempt_number: int | None = None) -> Job | None:
        if attempt_number is None:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE job_id = ? ORDER BY attempt_number DESC LIMIT 1", (job_id,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE job_id = ? AND attempt_number = ?", (job_id, attempt_number)
            ).fetchone()
        return None if row is None else _job_from_row(row)

    def jobs(self, *, task_id: str | None = None, active_only: bool = False) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE 1 = 1"
        values: list[Any] = []
        if task_id is not None:
            sql += " AND task_id = ?"
            values.append(task_id)
        if active_only:
            sql += " AND execution_status IN ('queued', 'leased', 'running')"
        sql += " ORDER BY created_at, job_id, attempt_number"
        return [_job_from_row(row) for row in self.conn.execute(sql, values)]

    def pending_outbox(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM outbox WHERE state = 'pending' ORDER BY created_at, outbox_id")
        return [dict(row) for row in rows]

    def intent(self, intent_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["artifact_refs"] = json.loads(item.pop("artifact_refs_json"))
        return item

    def model_calls(self, *, task_id: str | None = None) -> list[dict[str, Any]]:
        sql, values = "SELECT * FROM model_calls", []
        if task_id is not None:
            sql += " WHERE task_id = ?"
            values.append(task_id)
        rows = self.conn.execute(sql + " ORDER BY created_at, call_id", values)
        calls = []
        for row in rows:
            item = dict(row)
            for key in ("output_json", "errors_json", "redactions_json"):
                value = item.pop(key)
                item[key.removesuffix("_json")] = None if value is None else json.loads(value)
            calls.append(item)
        return calls

    def proposal(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["changes"] = json.loads(item.pop("changes_json"))
        return item

    def applications(self, proposal_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM applications WHERE proposal_id = ? ORDER BY created_at, apply_id",
                                 (proposal_id,))
        return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]

    def plan_revisions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM plan_revisions ORDER BY plan_revision")
        return [
            {"plan_revision": row["plan_revision"], "summary": json.loads(row["summary_json"]),
             "created_at": row["created_at"], "created_by": row["created_by"]}
            for row in rows
        ]

    def events(self, *, after_seq: int = 0, limit: int = DEFAULT_PAGE) -> list[EventRecord]:
        limit = max(1, min(int(limit), MAX_PAGE))
        rows = self.conn.execute(
            "SELECT * FROM events WHERE event_seq > ? ORDER BY event_seq LIMIT ?",
            (int(after_seq), limit),
        )
        return [row_to_event(row) for row in rows]

    def iter_events(
        self, *, after_seq: int = 0, through_seq: int | None = None, event_types: tuple[str, ...] = ()
    ) -> Iterator[EventRecord]:
        """Events in (after_seq, through_seq], oldest first, optionally filtered by type."""
        sql = "SELECT * FROM events WHERE event_seq > ?"
        values: list[Any] = [int(after_seq)]
        if through_seq is not None:
            sql += " AND event_seq <= ?"
            values.append(int(through_seq))
        if event_types:
            sql += " AND event_type IN (" + ", ".join("?" for _ in event_types) + ")"
            values.extend(event_types)
        sql += " ORDER BY event_seq"
        for row in self.conn.execute(sql, values):
            yield row_to_event(row)

    def find_receipt(self, actor_id: str, idempotency_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM command_receipts WHERE actor_id = ? AND idempotency_key = ?",
            (actor_id, idempotency_key),
        ).fetchone()


class ProjectStore(RecordReader):
    def __init__(self, path: Path, conn: sqlite3.Connection, lock: ProjectLock) -> None:
        super().__init__(conn)
        self.path = path
        self._lock = lock

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def open(cls, path: Path, clock: Clock | None = None) -> ProjectStore:
        """Acquire the single-writer lock, open the database, apply migrations."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = ProjectLock(path.with_name(path.name + ".lock"))
        lock.acquire()
        conn: sqlite3.Connection | None = None
        try:
            conn = open_connection(path)
            migrate(conn, clock or SystemClock())
        except BaseException:
            if conn is not None:
                conn.close()
            lock.release()
            raise
        return cls(path, conn, lock)

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self._lock.release()

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ----------------------------------------------------------- maintenance

    def rebuild_projections(self) -> int:
        """Delete every projection row and replay the whole event log.

        Returns the number of events replayed. Runs in one transaction, so a
        failure leaves the previous projections untouched.
        """
        count = 0
        with write_transaction(self.conn):
            for table in PROJECTION_TABLES:
                self.conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
            for event in self.iter_events():
                apply_event(self.conn, event)
                count += 1
        return count

    @contextmanager
    def replayed(self, through_seq: int | None = None) -> Iterator[RecordReader]:
        """A reader over a scratch in-memory database rebuilt from the event log.

        Events after through_seq are not applied, so the reader shows the
        project exactly as it stood at that event. The live database is not
        changed. The scratch copy holds the events too, so event queries on
        the reader also stop at through_seq.
        """
        latest = self.last_event_seq()
        through = latest if through_seq is None else int(through_seq)
        if not 0 <= through <= latest:
            raise ForgeError(
                ErrorCode.INVALID_REQUEST,
                f"event sequence {through} is outside this project's log (0 to {latest})",
            )
        scratch = sqlite3.connect(":memory:", isolation_level=None)
        scratch.row_factory = sqlite3.Row
        try:
            scratch.execute("PRAGMA foreign_keys = ON")
            migrate(scratch, SystemClock())
            with write_transaction(scratch):
                for row in self.conn.execute(
                    "SELECT * FROM events WHERE event_seq <= ? ORDER BY event_seq", (through,)
                ):
                    scratch.execute(
                        "INSERT INTO events (event_seq, event_id, project_id, command_id, event_type,"
                        " event_schema_version, actor_id, occurred_at, causation_id, payload_json)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        tuple(row),
                    )
                    apply_event(scratch, row_to_event(row))
            yield RecordReader(scratch)
        finally:
            scratch.close()

    def verify_projections(self) -> list[str]:
        """Replay events into a scratch database and compare with the live tables.

        Returns a list of differences (empty means the live projections are
        exactly what the event log implies). The live database is not changed.
        """
        with self.replayed() as replay:
            expected = dump_projections(replay.conn)
        actual = dump_projections(self.conn)
        differences = []
        for table in expected:
            if expected[table] != actual[table]:
                differences.append(
                    f"{table}: live has {len(actual[table])} rows, replay gives "
                    f"{len(expected[table])}, or row contents differ"
                )
        return differences

    def integrity_check(self) -> None:
        result = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ForgeError(ErrorCode.INTERNAL_ERROR, f"database integrity check failed: {result}")
        if self.conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ForgeError(ErrorCode.INTERNAL_ERROR, "database foreign key check failed")


def _common(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "record_version": row["record_version"],
    }


def _snapshot_from_row(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        **_common(row),
        snapshot_id=row["snapshot_id"],
        manifest_digest=row["manifest_digest"],
        manifest_artifact_id=row["manifest_artifact_id"],
        source_root=row["source_root"],
        base_commit=row["base_commit"],
        capture_policy_revision=row["capture_policy_revision"],
        file_count=row["file_count"],
        total_bytes=row["total_bytes"],
        excluded_count=row["excluded_count"],
        dirty_paths=json.loads(row["dirty_paths_json"]),
        parent_snapshot_id=row["parent_snapshot_id"],
        origin=row["origin"],
    )


def _evidence_from_row(row: sqlite3.Row) -> Evidence:
    return Evidence(
        **_common(row),
        evidence_id=row["evidence_id"],
        origin=row["origin"],
        job_id=row["job_id"],
        attempt_number=row["attempt_number"],
        snapshot_id=row["snapshot_id"],
        tool_ref=row["tool_ref"],
        environment_digest=row["environment_digest"],
        evaluator_digest=row["evaluator_digest"],
        subject_ref=None if row["subject_ref_json"] is None else json.loads(row["subject_ref_json"]),
        reviewer_id=row["reviewer_id"],
        observation_type=row["observation_type"],
        observation=json.loads(row["observation_json"]),
        artifact_refs=json.loads(row["artifact_refs_json"]),
    )


def _assessment_from_row(row: sqlite3.Row) -> Assessment:
    return Assessment(
        **_common(row),
        assessment_id=row["assessment_id"],
        criterion_ref={"id": row["criterion_id"], "revision": row["criterion_revision"]},
        snapshot_id=row["snapshot_id"],
        evidence_refs=json.loads(row["evidence_refs_json"]),
        assessment_result=row["assessment_result"],
        assessor_ref=row["assessor_ref"],
        limitations=json.loads(row["limitations_json"]),
        summary=json.loads(row["summary_json"]),
        supersedes_assessment_id=row["supersedes_assessment_id"],
        recorded_seq=row["recorded_seq"],
    )


def _finding_from_row(row: sqlite3.Row) -> Finding:
    return Finding(
        **_common(row),
        finding_id=row["finding_id"],
        rule_id=row["rule_id"],
        claim=row["claim"],
        severity=row["severity"],
        status=row["status"],
        confirmation=row["confirmation"],
        path=row["path"],
        line=row["line"],
        fingerprint=row["fingerprint"],
        snapshot_id=row["snapshot_id"],
        evidence_refs=json.loads(row["evidence_refs_json"]),
    )


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        attempt_number=row["attempt_number"],
        project_id=row["project_id"],
        task_id=row["task_id"],
        host_id=row["host_id"],
        lease_epoch=row["lease_epoch"],
        spec=json.loads(row["spec_json"]),
        job_digest=row["job_digest"],
        effect_class=row["effect_class"],
        retry_class=row["retry_class"],
        execution_status=row["execution_status"],
        host_event_seq=row["host_event_seq"],
        cancel_requested_at=row["cancel_requested_at"],
        reconciliation=row["reconciliation"],
        outcome=None if row["outcome_json"] is None else json.loads(row["outcome_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        record_version=row["record_version"],
    )


def _decision_from_row(row: sqlite3.Row) -> Decision:
    return Decision(
        **_common(row),
        decision_id=row["decision_id"],
        decision_kind=row["decision_kind"],
        subject_ref=json.loads(row["subject_ref_json"]),
        question=row["question"],
        proposal_digest=row["proposal_digest"],
        disposition=row["disposition"],
        reason=row["reason"],
        actor_id=row["actor_id"],
        authority_ref=row["authority_ref"],
        expires_at=row["expires_at"],
        updated_at=row["updated_at"],
    )

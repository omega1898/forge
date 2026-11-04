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
from forge.contracts.records import Criterion, Decision, EventRecord, Project, Requirement, Task
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

"""Ticket F02 (storage layer): migrations, connection settings, locks, append-only log."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from forge.contracts.clock import FixedClock
from forge.contracts.errors import ErrorCode, ForgeError
from forge.record.migrate import bundled_migrations, split_statements
from forge.record.store import ProjectStore
from tests.conftest import World


def test_fresh_database_is_migrated_and_configured(store: ProjectStore) -> None:
    conn = store.conn
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(bundled_migrations())
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    rows = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
    assert [(row["version"], row["name"]) for row in rows] == [
        (migration.version, migration.name) for migration in bundled_migrations()
    ]
    assert rows[0]["checksum"].startswith("sha256:")


def test_reopening_is_idempotent(tmp_path: Path, clock: FixedClock) -> None:
    path = tmp_path / "state.db"
    ProjectStore.open(path, clock).close()
    with ProjectStore.open(path, clock) as store:
        count = store.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == len(bundled_migrations())


def test_older_database_is_upgraded_in_place(tmp_path: Path, clock: FixedClock) -> None:
    """A database created when only migration 0001 existed gains later tables."""
    from forge.record.db import open_connection, write_transaction
    from forge.record.migrate import split_statements

    path = tmp_path / "state.db"
    conn = open_connection(path)
    first = bundled_migrations()[0]
    with write_transaction(conn):
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
        )
        for statement in split_statements(first.sql):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, 'then')",
            (first.version, first.name, first.checksum),
        )
        conn.execute(f"PRAGMA user_version = {first.version}")
    conn.close()

    with ProjectStore.open(path, clock) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == len(bundled_migrations())
        tables = {row[0] for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"artifacts", "snapshots"} <= tables
        store.integrity_check()


def test_database_from_newer_forge_is_refused(tmp_path: Path, clock: FixedClock) -> None:
    path = tmp_path / "state.db"
    ProjectStore.open(path, clock).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(ForgeError) as caught:
        ProjectStore.open(path, clock)
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert "newer FORGE" in caught.value.message


def test_edited_migration_is_detected(tmp_path: Path, clock: FixedClock) -> None:
    path = tmp_path / "state.db"
    ProjectStore.open(path, clock).close()
    raw = sqlite3.connect(path)
    raw.execute("UPDATE schema_migrations SET checksum = 'sha256:' || hex(zeroblob(32))")
    raw.commit()
    raw.close()
    with pytest.raises(ForgeError, match="differs from the version applied"):
        ProjectStore.open(path, clock)


def test_second_writer_is_refused(tmp_path: Path, clock: FixedClock) -> None:
    path = tmp_path / "state.db"
    with ProjectStore.open(path, clock):
        with pytest.raises(ForgeError) as caught:
            ProjectStore.open(path, clock)
        assert caught.value.code == ErrorCode.RESOURCE_UNAVAILABLE
    # Once the first writer closes, the project can be opened again.
    ProjectStore.open(path, clock).close()


def test_events_and_receipts_are_append_only(world: World) -> None:
    conn = world.store.conn
    for statement in (
        "UPDATE events SET event_type = 'forged'",
        "DELETE FROM events",
        "UPDATE command_receipts SET response_json = '{}'",
        "DELETE FROM command_receipts",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(statement)


def test_strict_tables_reject_wrong_types(world: World) -> None:
    world.task()  # an UPDATE that matches zero rows would prove nothing
    with pytest.raises(sqlite3.Error):
        world.store.conn.execute(
            "UPDATE tasks SET record_version = 'two'"  # text into an INTEGER column
        )


def test_statement_splitter_keeps_triggers_and_comments_intact() -> None:
    sql = (
        "-- a comment; with a semicolon\n"
        "CREATE TABLE t (x INTEGER);\n"
        "CREATE TRIGGER no_delete BEFORE DELETE ON t\n"
        "BEGIN\n"
        "    SELECT RAISE(ABORT, 'nope');\n"
        "END;\n"
    )
    statements = split_statements(sql)
    assert len(statements) == 2
    assert statements[1].endswith("END;")


def test_incomplete_migration_is_rejected() -> None:
    with pytest.raises(ForgeError, match="incomplete statement"):
        split_statements("CREATE TABLE t (x INTEGER)")


def test_migrations_have_unix_line_endings() -> None:
    for package in ("forge.record", "forge.authority"):
        for migration in bundled_migrations(package):
            assert "\r" not in migration.sql


def test_integrity_check_passes(world: World) -> None:
    world.store.integrity_check()

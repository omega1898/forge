"""Apply bundled, checksummed SQL migrations in order.

Rules:
- Migrations are trusted FORGE code shipped in the package. They are never
  loaded from a project repository.
- Each migration is numbered (0001_, 0002_, ...) and its predecessor is the
  previous number. Gaps are an installation error.
- The SHA-256 checksum of each applied migration is stored. If a released
  migration file is later edited, opening an existing database fails instead
  of silently diverging.
- PRAGMA user_version is a quick compatibility marker. A database created by
  a newer FORGE (higher user_version than we know) is refused.
- Each migration runs inside one transaction together with its bookkeeping,
  so a failed migration leaves the database at the previous version.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib import resources

from forge.contracts.clock import Clock, format_timestamp
from forge.contracts.digests import sha256_digest
from forge.contracts.errors import ErrorCode, ForgeError
from forge.record.db import write_transaction

_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def bundled_migrations(package: str = "forge.record") -> list[Migration]:
    folder = resources.files(package).joinpath("migrations")
    found: list[Migration] = []
    for entry in folder.iterdir():
        match = _NAME_RE.match(entry.name)
        if match is None:
            continue
        data = entry.read_bytes()
        if b"\r" in data:
            raise ForgeError(
                ErrorCode.INTERNAL_ERROR,
                f"migration {entry.name} contains carriage returns; line endings "
                "were altered, so its checksum would not match other installations",
            )
        found.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                sql=data.decode("utf-8"),
                checksum=sha256_digest(data),
            )
        )
    found.sort(key=lambda migration: migration.version)
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise ForgeError(
                ErrorCode.INTERNAL_ERROR,
                f"migration sequence broken: expected {expected:04d}, found {migration.version:04d}",
            )
    return found


def split_statements(sql: str) -> list[str]:
    """Split a script into complete statements (trigger bodies stay intact).

    We cannot use executescript(): it COMMITs any open transaction first,
    which would break "migration plus bookkeeping in one transaction".
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        # complete_statement understands comments, quotes and trigger bodies,
        # so a ';' inside "-- comment; text" or BEGIN ... END does not split.
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if _has_sql(buffer):
        raise ForgeError(ErrorCode.INTERNAL_ERROR, "migration ends with an incomplete statement")
    return statements


def _has_sql(text: str) -> bool:
    return any(line.strip() and not line.strip().startswith("--") for line in text.splitlines())


def migrate(conn: sqlite3.Connection, clock: Clock, package: str = "forge.record") -> int:
    """Bring the database up to the newest bundled version; return that version."""
    migrations = bundled_migrations(package)
    latest = migrations[-1].version if migrations else 0

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > latest:
        raise ForgeError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"database schema version {current} was created by a newer FORGE; "
            f"this installation supports up to {latest}",
            details={"database_version": current, "supported_version": latest},
        )

    with write_transaction(conn):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
        )
    applied = {
        row["version"]: row["checksum"]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }
    if set(applied) != set(range(1, current + 1)):
        raise ForgeError(
            ErrorCode.INTERNAL_ERROR,
            f"schema_migrations rows {sorted(applied)} disagree with user_version {current}",
        )

    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None:
            if recorded != migration.checksum:
                raise ForgeError(
                    ErrorCode.INTERNAL_ERROR,
                    f"migration {migration.version:04d}_{migration.name} differs from the "
                    "version applied to this database; refusing to continue",
                    details={"recorded": recorded, "bundled": migration.checksum},
                )
            continue
        if migration.version != current + 1:
            raise ForgeError(
                ErrorCode.INTERNAL_ERROR,
                f"cannot apply migration {migration.version:04d}: database is at {current}",
            )
        with write_transaction(conn):
            for statement in split_statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
                " VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, format_timestamp(clock.now())),
            )
            # PRAGMA cannot take a bound parameter; the value is an int we
            # computed from a bundled file name, never external text.
            conn.execute(f"PRAGMA user_version = {int(migration.version)}")
        current = migration.version

    return current

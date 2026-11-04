"""SQLite connection setup and the explicit write transaction.

Settings (each read back and verified, because SQLite silently ignores some
PRAGMAs it cannot honor, for example WAL on an unsupported filesystem):

- foreign_keys=ON: SQLite does NOT enforce foreign keys unless asked, per
  connection.
- busy_timeout: wait a bounded time for a lock instead of failing instantly.
- journal_mode=WAL: readers do not block the single writer. WAL only works for
  processes on the same host; it is not a way to share a database across
  machines.
- synchronous=FULL: a committed transaction survives power loss, at some
  speed cost. Chosen explicitly rather than inherited.

The connection uses isolation_level=None so Python never opens transactions
behind our back; FORGE issues BEGIN IMMEDIATE itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from forge.contracts.errors import ErrorCode, ForgeError

BUSY_TIMEOUT_MS = 5000
_SYNCHRONOUS_FULL = 2


def open_connection(path: Path, *, wal: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        if wal:
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise ForgeError(
                    ErrorCode.RESOURCE_UNAVAILABLE,
                    f"database could not use WAL journaling (got {mode!r}); "
                    "use a local filesystem",
                )
        conn.execute("PRAGMA synchronous = FULL")
        _verify(conn, "foreign_keys", 1)
        _verify(conn, "busy_timeout", BUSY_TIMEOUT_MS)
        _verify(conn, "synchronous", _SYNCHRONOUS_FULL)
    except BaseException:
        conn.close()
        raise
    return conn


def _verify(conn: sqlite3.Connection, pragma: str, expected: int) -> None:
    actual = conn.execute(f"PRAGMA {pragma}").fetchone()[0]
    if actual != expected:
        raise ForgeError(
            ErrorCode.INTERNAL_ERROR,
            f"SQLite setting {pragma} is {actual!r}, expected {expected!r}",
        )


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any exception.

    IMMEDIATE takes the write lock at the start, so two writers cannot both
    read old state and then both try to write (a classic lost update).
    Keep these transactions short: never hold one while waiting on a model,
    a compiler, a person, or hardware.
    """
    if conn.in_transaction:
        raise ForgeError(ErrorCode.INTERNAL_ERROR, "nested write transactions are not allowed")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

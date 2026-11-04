"""Event envelopes and appending them to the authoritative log.

An event records an accepted state change ("task X moved from queued to
blocked because Y"), not merely that something was attempted. Events are
appended in a strictly increasing sequence per project and never modified.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from forge.contracts.digests import canonical_json_bytes
from forge.contracts.ids import new_id
from forge.contracts.records import EventRecord


@dataclass(frozen=True)
class DomainEvent:
    """An event a command handler wants to append (not yet sequenced)."""

    event_type: str
    payload: dict[str, Any]
    event_schema_version: int = 1
    causation_id: str | None = None


def append_event(
    conn: sqlite3.Connection,
    *,
    event: DomainEvent,
    project_id: str,
    command_id: str,
    actor_id: str,
    occurred_at: str,
) -> EventRecord:
    """Insert one event with the next sequence number. Caller holds the write transaction."""
    if not conn.in_transaction:
        raise RuntimeError("append_event requires an open write transaction")
    next_seq = conn.execute("SELECT COALESCE(MAX(event_seq), 0) + 1 FROM events").fetchone()[0]
    record = EventRecord(
        event_seq=next_seq,
        event_id=new_id("event"),
        project_id=project_id,
        command_id=command_id,
        event_type=event.event_type,
        event_schema_version=event.event_schema_version,
        actor_id=actor_id,
        occurred_at=occurred_at,
        causation_id=event.causation_id,
        payload=event.payload,
    )
    conn.execute(
        "INSERT INTO events (event_seq, event_id, project_id, command_id, event_type,"
        " event_schema_version, actor_id, occurred_at, causation_id, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.event_seq,
            record.event_id,
            record.project_id,
            record.command_id,
            record.event_type,
            record.event_schema_version,
            record.actor_id,
            record.occurred_at,
            record.causation_id,
            canonical_json_bytes(record.payload).decode("utf-8"),
        ),
    )
    return record


def row_to_event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_seq=row["event_seq"],
        event_id=row["event_id"],
        project_id=row["project_id"],
        command_id=row["command_id"],
        event_type=row["event_type"],
        event_schema_version=row["event_schema_version"],
        actor_id=row["actor_id"],
        occurred_at=row["occurred_at"],
        causation_id=row["causation_id"],
        payload=json.loads(row["payload_json"]),
    )

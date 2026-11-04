"""The controller: the only writer of a project's authoritative record.

apply_command follows the blueprint's command transaction (Part 3, section 3):

 1. Parse and validate the request shape before opening a write transaction.
 2. Do expensive preparation (hashing) outside the transaction.
 3. BEGIN IMMEDIATE.
 4. Look up the idempotency key. Same request digest -> return the stored
    receipt. Different digest -> reject the key reuse.
 5. Check current versions and authority (the handler).
 6. Append the domain events and update projections.
 7. (Outbox entries for side effects arrive with ticket F07.)
 8. Save the command receipt and COMMIT.
 9. Only after commit, notify subscribers.

If anything fails between 3 and 8, ROLLBACK leaves no partial state.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from forge.authority.grants import TrustStore
from forge.contracts.clock import Clock, SystemClock, format_timestamp
from forge.contracts.commands import ActorContext, CommandReceipt, parse_command
from forge.contracts.digests import canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.records import EventRecord
from forge.contracts.versions import SCHEMA_VERSION
from forge.controller.handlers import ALLOWED_ACTOR_KINDS, HANDLERS, HandlerContext
from forge.record.db import write_transaction
from forge.record.events import append_event
from forge.record.projections import apply_event
from forge.record.store import ProjectStore

Listener = Callable[[list[EventRecord]], None]


class Controller:
    def __init__(
        self,
        store: ProjectStore,
        *,
        clock: Clock | None = None,
        trust_store: TrustStore | None = None,
        controller_id: str | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or SystemClock()
        self.trust_store = trust_store
        self.controller_id = controller_id or new_id("controller")
        self._write_lock = threading.Lock()
        self._listeners: list[Listener] = []
        self.listener_errors: list[str] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def apply_command(self, raw: object, actor: ActorContext) -> CommandReceipt:
        # Steps 1-2: validation and hashing, no database lock held.
        command = parse_command(raw)
        envelope = command.envelope
        if actor.kind not in ALLOWED_ACTOR_KINDS[envelope.method]:
            raise ForgeError(
                ErrorCode.PERMISSION_DENIED, f"{actor.kind} actors may not call {envelope.method}"
            )
        request_digest = digest_json(command.semantic_payload())

        with self._write_lock:
            committed: list[EventRecord] = []
            # Step 3.
            with write_transaction(self.store.conn):
                # Step 4.
                stored = self.store.find_receipt(actor.actor_id, envelope.idempotency_key)
                if stored is not None:
                    if stored["request_digest"] != request_digest:
                        raise ForgeError(
                            ErrorCode.INVALID_REQUEST,
                            "idempotency_key was already used for a different request",
                            related_ids=[stored["command_id"]],
                            details={"reason": "idempotency_key_reused"},
                        )
                    receipt = CommandReceipt.model_validate_json(stored["response_json"])
                    return receipt.model_copy(update={"duplicate_delivery": True})

                project = self.store.project()
                if envelope.method != "project.create":
                    if project is None or project.project_id != envelope.project_id:
                        raise ForgeError(
                            ErrorCode.INVALID_REQUEST,
                            "project_id does not match the project held by this controller",
                            related_ids=[envelope.project_id] if envelope.project_id else [],
                        )

                # Step 5.
                now = self.clock.now()
                context = HandlerContext(
                    store=self.store,
                    actor=actor,
                    now=now,
                    controller_id=self.controller_id,
                    trust_store=self.trust_store,
                    project=project,
                )
                outcome = HANDLERS[envelope.method](context, command)

                # Step 6.
                command_id = new_id("command")
                occurred_at = format_timestamp(now)
                project_id = outcome.project_id or envelope.project_id
                assert project_id is not None
                for event in outcome.events:
                    record = append_event(
                        self.store.conn,
                        event=event,
                        project_id=project_id,
                        command_id=command_id,
                        actor_id=actor.actor_id,
                        occurred_at=occurred_at,
                    )
                    apply_event(self.store.conn, record)
                    committed.append(record)

                # Step 8.
                receipt = CommandReceipt(
                    schema_version=SCHEMA_VERSION,
                    command_id=command_id,
                    method=envelope.method,
                    project_id=project_id,
                    idempotency_key=envelope.idempotency_key,
                    request_digest=request_digest,
                    actor_id=actor.actor_id,
                    accepted_at=occurred_at,
                    event_seqs=[record.event_seq for record in committed],
                    result=outcome.result,
                )
                self.store.conn.execute(
                    "INSERT INTO command_receipts (command_id, actor_id, idempotency_key, method,"
                    " request_digest, accepted_at, response_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        command_id,
                        actor.actor_id,
                        envelope.idempotency_key,
                        envelope.method,
                        request_digest,
                        occurred_at,
                        canonical_json_bytes(json.loads(receipt.model_dump_json())).decode("utf-8"),
                    ),
                )
            # Committed. Step 9.
        self._notify(committed)
        return receipt

    def _notify(self, events: list[EventRecord]) -> None:
        # A failing subscriber must never undo or block a committed command.
        for listener in list(self._listeners):
            try:
                listener(events)
            except Exception as error:  # noqa: BLE001 - isolation boundary
                self.listener_errors.append(f"{type(error).__name__}: {error}"[:200])

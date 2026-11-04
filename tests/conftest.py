"""Shared test fixtures.

Every test gets its own temporary directory, so tests never share a database.
Time is controlled with FixedClock so expiry tests do not sleep.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from forge.authority.grants import TrustStore
from forge.contracts.clock import FixedClock
from forge.contracts.commands import ActorContext, CommandReceipt
from forge.contracts.ids import new_id
from forge.controller.service import Controller
from forge.record.store import ProjectStore

BUDGET = {"max_wall_ms": 60_000, "max_attempts": 2}


def command(
    method: str,
    params: dict[str, Any],
    *,
    project_id: str | None = None,
    key: str | None = None,
    expected: int | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "schema_version": "1.0",
        "method": method,
        "idempotency_key": key or new_id("request"),
        "params": params,
    }
    if project_id is not None:
        raw["project_id"] = project_id
    if expected is not None:
        raw["expected_record_version"] = expected
    return raw


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def human() -> ActorContext:
    return ActorContext(actor_id=new_id("actor"), kind="human", channel="trusted_local")


@pytest.fixture
def model_actor() -> ActorContext:
    return ActorContext(actor_id=new_id("actor"), kind="model", channel="api")


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> Iterator[ProjectStore]:
    opened = ProjectStore.open(tmp_path / "project" / "state.db", clock)
    yield opened
    opened.close()


@pytest.fixture
def trust(tmp_path: Path, clock: FixedClock) -> Iterator[TrustStore]:
    opened = TrustStore.open(tmp_path / "user" / "trust.db", clock)
    yield opened
    opened.close()


@pytest.fixture
def controller(store: ProjectStore, clock: FixedClock, trust: TrustStore) -> Controller:
    return Controller(store, clock=clock, trust_store=trust)


class World:
    """A controller with one created project, plus helpers to drive it."""

    def __init__(self, controller: Controller, actor: ActorContext) -> None:
        self.controller = controller
        self.actor = actor
        receipt = controller.apply_command(
            command("project.create", {"name": "Packet reader", "objective": "Read packets safely"}),
            actor,
        )
        self.project_id = receipt.project_id

    @property
    def store(self) -> ProjectStore:
        return self.controller.store

    def send(
        self,
        method: str,
        params: dict[str, Any],
        *,
        actor: ActorContext | None = None,
        key: str | None = None,
        expected: int | None = None,
    ) -> CommandReceipt:
        return self.controller.apply_command(
            command(method, params, project_id=self.project_id, key=key, expected=expected),
            actor or self.actor,
        )

    def criterion(self, subject: str = "Empty file yields an empty result") -> str:
        receipt = self.send("criterion.create", {"subject": subject, "assessment_method": "test_suite"})
        return receipt.result["criterion_id"]

    def task(self, title: str = "Define the packet interface", task_type: str = "prepare", **extra: Any) -> str:
        params = {"task_type": task_type, "title": title, "budget": BUDGET, **extra}
        return self.send("task.create", params).result["task_id"]

    def counts(self) -> dict[str, int]:
        conn = self.store.conn
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("events", "command_receipts", "tasks", "records", "task_dependencies", "decisions")
        }


@pytest.fixture
def world(controller: Controller, human: ActorContext) -> World:
    return World(controller, human)

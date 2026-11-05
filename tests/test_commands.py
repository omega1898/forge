"""Ticket F02 (command path) and build step C1/C2 acceptance tests.

- Repeating an identical command returns one stored result; the same key with
  different content is rejected.
- Stale version updates do not overwrite newer data.
- A transaction failure leaves no partial state.
- A dependency cycle cannot be committed.
- Replaying the event log rebuilds exactly the live projections.
"""

from __future__ import annotations

import pytest

from forge.contracts.commands import ActorContext
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.record import projections
from tests.conftest import BUDGET, World, command


def _error(excinfo: pytest.ExceptionInfo[ForgeError]) -> ErrorCode:
    return excinfo.value.code


# ------------------------------------------------------------- session 1 ----


def test_session_one_project_criterion_and_task_without_a_model(world: World) -> None:
    criterion_id = world.criterion()
    requirement = world.send(
        "requirement.create",
        {"statement": "Reject incomplete records", "criterion_ids": [criterion_id]},
    )
    task_id = world.task(
        criterion_refs=[{"id": criterion_id, "revision": 1}],
        requirement_refs=[{"id": requirement.result["requirement_id"], "revision": 1}],
    )
    task = world.store.task(task_id)
    assert task is not None
    assert task.status == "queued"
    assert task.record_version == 1
    assert task.criterion_refs[0].id == criterion_id

    events = world.store.events()
    assert [event.event_type for event in events] == [
        "project.created",
        "criterion.created",
        "requirement.created",
        "task.created",
    ]
    assert [event.event_seq for event in events] == [1, 2, 3, 4]
    assert world.store.verify_projections() == []


# ----------------------------------------------------------- idempotency ----


def test_repeated_command_returns_one_stored_result(world: World) -> None:
    params = {"subject": "Multiple packets keep order", "assessment_method": "test_suite"}
    first = world.send("criterion.create", params, key="retry-me")
    before = world.counts()
    second = world.send("criterion.create", params, key="retry-me")

    assert second.duplicate_delivery is True
    assert first.duplicate_delivery is False
    assert second.command_id == first.command_id
    assert second.result == first.result
    assert world.counts() == before  # no new event, record, or receipt


def test_same_key_with_different_content_is_rejected(world: World) -> None:
    world.send("criterion.create", {"subject": "A", "assessment_method": "test_suite"}, key="k")
    before = world.counts()
    with pytest.raises(ForgeError) as caught:
        world.send("criterion.create", {"subject": "B", "assessment_method": "test_suite"}, key="k")
    assert _error(caught) == ErrorCode.INVALID_REQUEST
    assert caught.value.details["reason"] == "idempotency_key_reused"
    assert world.counts() == before


def test_idempotency_keys_are_scoped_per_actor(world: World) -> None:
    other = ActorContext(actor_id=new_id("actor"), kind="human", channel="trusted_local")
    params = {"subject": "A", "assessment_method": "test_suite"}
    mine = world.send("criterion.create", params, key="shared-key")
    theirs = world.send("criterion.create", params, key="shared-key", actor=other)
    assert theirs.command_id != mine.command_id
    assert theirs.duplicate_delivery is False


def test_duplicate_project_creation_returns_the_same_project(controller, human) -> None:  # noqa: ANN001
    raw = command("project.create", {"name": "n", "objective": "o"}, key="create-once")
    first = controller.apply_command(raw, human)
    again = controller.apply_command(raw, human)
    assert again.project_id == first.project_id
    assert again.duplicate_delivery is True
    with pytest.raises(ForgeError, match="already holds"):
        controller.apply_command(command("project.create", {"name": "n2", "objective": "o"}), human)


# --------------------------------------------------------- stale versions ----


def test_stale_write_does_not_overwrite_newer_data(world: World) -> None:
    task_id = world.task()
    seen_by_both = world.store.task(task_id).record_version  # two clients read version 1

    world.send(
        "task.transition",
        {"task_id": task_id, "to_status": "blocked", "reason": "waiting for format spec"},
        expected=seen_by_both,
    )
    with pytest.raises(ForgeError) as caught:
        world.send(
            "task.transition",
            {"task_id": task_id, "to_status": "cancelled", "reason": "no longer needed"},
            expected=seen_by_both,
        )
    assert _error(caught) == ErrorCode.STALE_VERSION
    assert caught.value.details == {"expected": 1, "current": 2}
    task = world.store.task(task_id)
    assert task.status == "blocked"
    assert task.record_version == 2


def test_reference_to_old_revision_is_stale(world: World) -> None:
    criterion_id = world.criterion()
    with pytest.raises(ForgeError) as caught:
        world.task(criterion_refs=[{"id": criterion_id, "revision": 2}])
    assert _error(caught) == ErrorCode.STALE_VERSION


# ------------------------------------------------------- atomic failure ----


def test_transaction_failure_leaves_no_partial_state(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(conn, event):  # noqa: ANN001
        # The event row has already been inserted when this runs.
        raise RuntimeError("simulated crash while updating projections")

    before = world.counts()
    monkeypatch.setitem(projections.PROJECTORS, ("task.created", 1), explode)
    with pytest.raises(RuntimeError, match="simulated crash"):
        world.task()
    assert world.counts() == before
    assert not world.store.conn.in_transaction

    # The controller keeps working after the failure.
    monkeypatch.undo()
    world.task()
    assert world.counts()["tasks"] == before["tasks"] + 1


# ---------------------------------------------------------------- graphs ----


def test_dependency_cycle_cannot_be_committed(world: World) -> None:
    first = world.task("parse header")
    second = world.task("parse payload", depends_on=[first])
    before = world.counts()
    with pytest.raises(ForgeError) as caught:
        world.send(
            "task.add_dependency",
            {"task_id": first, "depends_on_task_id": second},
            expected=1,
        )
    assert _error(caught) == ErrorCode.INVALID_REQUEST
    cycle = caught.value.details["cycle"]
    assert cycle[0] == cycle[-1]  # reported as a closed loop, from either end
    assert sorted(cycle[:-1]) == sorted([first, second])
    assert world.counts() == before


def test_dependency_on_missing_task_is_rejected(world: World) -> None:
    with pytest.raises(ForgeError, match="does not exist"):
        world.task(depends_on=[new_id("task")])


def test_reference_to_missing_criterion_is_rejected(world: World) -> None:
    with pytest.raises(ForgeError, match="does not exist"):
        world.send("requirement.create", {"statement": "s", "criterion_ids": [new_id("criterion")]})


# ------------------------------------------------------------ lifecycle ----


def test_tasks_cannot_be_declared_complete_by_command(world: World) -> None:
    task_id = world.task()
    world.send("task.transition", {"task_id": task_id, "to_status": "running", "reason": "go"}, expected=1)
    with pytest.raises(ForgeError) as caught:
        world.send(
            "task.transition", {"task_id": task_id, "to_status": "completed", "reason": "trust me"}, expected=2
        )
    # Saying "it's done" is not evidence: completion goes through task.complete,
    # where the completion evaluator decides from recorded assessments (F06).
    assert _error(caught) == ErrorCode.INVALID_REQUEST
    assert "task.complete" in caught.value.message


def test_execute_tasks_cannot_run_without_job_admission(world: World) -> None:
    task_id = world.task(task_type="execute")
    with pytest.raises(ForgeError) as caught:
        world.send("task.transition", {"task_id": task_id, "to_status": "running", "reason": "go"}, expected=1)
    assert _error(caught) == ErrorCode.MISSING_CAPABILITY


def test_task_with_unfinished_dependency_is_not_ready(world: World) -> None:
    first = world.task("design")
    second = world.task("implement", depends_on=[first])
    with pytest.raises(ForgeError, match="not ready"):
        world.send("task.transition", {"task_id": second, "to_status": "running", "reason": "go"}, expected=1)


@pytest.mark.parametrize("illegal", ["completed", "failed", "queued"])
def test_illegal_transitions_from_queued_are_rejected(world: World, illegal: str) -> None:
    task_id = world.task()
    with pytest.raises(ForgeError, match="cannot move from queued"):
        world.send("task.transition", {"task_id": task_id, "to_status": illegal, "reason": "r"}, expected=1)


def test_terminal_tasks_stay_terminal(world: World) -> None:
    task_id = world.task()
    world.send("task.transition", {"task_id": task_id, "to_status": "cancelled", "reason": "r"}, expected=1)
    with pytest.raises(ForgeError, match="cannot move from cancelled"):
        world.send("task.transition", {"task_id": task_id, "to_status": "queued", "reason": "r"}, expected=2)


# ----------------------------------------------------------- projections ----


def test_replaying_events_rebuilds_identical_projections(world: World) -> None:
    first = world.task("a")
    second = world.task("b")
    world.send("task.add_dependency", {"task_id": second, "depends_on_task_id": first}, expected=1)
    world.send("task.transition", {"task_id": first, "to_status": "blocked", "reason": "r"}, expected=1)
    live = projections.dump_projections(world.store.conn)

    replayed = world.store.rebuild_projections()

    assert replayed == world.store.last_event_seq()
    assert projections.dump_projections(world.store.conn) == live


def test_tampered_projection_is_detected_and_repaired(world: World) -> None:
    task_id = world.task("original title")
    world.store.conn.execute("UPDATE tasks SET title = 'tampered' WHERE task_id = ?", (task_id,))
    assert world.store.verify_projections() == [
        "tasks: live has 1 rows, replay gives 1, or row contents differ"
    ]
    world.store.rebuild_projections()
    assert world.store.task(task_id).title == "original title"
    assert world.store.verify_projections() == []


# --------------------------------------------------------------- actors ----


def test_host_actors_cannot_edit_plans(world: World) -> None:
    host = ActorContext(actor_id=new_id("actor"), kind="host", channel="api")
    with pytest.raises(ForgeError) as caught:
        world.send("task.create", {"task_type": "prepare", "title": "t", "budget": BUDGET}, actor=host)
    assert _error(caught) == ErrorCode.PERMISSION_DENIED


def test_models_cannot_create_projects(controller, model_actor) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError) as caught:
        controller.apply_command(command("project.create", {"name": "n", "objective": "o"}), model_actor)
    assert _error(caught) == ErrorCode.PERMISSION_DENIED


def test_commands_for_another_project_are_rejected(world: World) -> None:
    raw = command(
        "criterion.create",
        {"subject": "s", "assessment_method": "test_suite"},
        project_id=new_id("project"),
    )
    with pytest.raises(ForgeError, match="does not match"):
        world.controller.apply_command(raw, world.actor)


# ------------------------------------------------------------ listeners ----


def test_subscribers_are_notified_after_commit_and_cannot_break_it(world: World) -> None:
    seen: list[int] = []

    def good(events) -> None:  # noqa: ANN001
        seen.extend(event.event_seq for event in events)

    def bad(events) -> None:  # noqa: ANN001
        raise ValueError("subscriber bug")

    world.controller.subscribe(bad)
    world.controller.subscribe(good)
    world.task()
    assert seen == [world.store.last_event_seq()]
    assert world.controller.listener_errors == ["ValueError: subscriber bug"]

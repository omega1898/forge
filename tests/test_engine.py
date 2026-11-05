"""Ticket F06: task graph, plans, typed transitions, completion evaluator.

Acceptance: cycles are rejected; controller-only tasks need no fake
execution job; completion respects required assessments and dispositions.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from forge.contracts.clock import format_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from tests.conftest import BUDGET, World


def plan(world: World, tasks: list[dict], *, base: int | None = None, deps: list[dict] | None = None, actor=None):  # noqa: ANN001, ANN201
    base = world.store.project().plan_revision if base is None else base
    return world.send(
        "plan.apply",
        {"base_plan_revision": base, "summary": "plan change", "add_tasks": tasks, "add_dependencies": deps or []},
        actor=actor,
    )


def planned(key: str, **extra: object) -> dict:
    return {"key": key, "task_type": "prepare", "title": key.replace("_", " "), "budget": BUDGET, **extra}


def start(world: World, task_id: str) -> None:
    task = world.store.task(task_id)
    world.send("task.transition", {"task_id": task_id, "to_status": "running", "reason": "go"},
               expected=task.record_version)


def complete(world: World, task_id: str, *, snapshot_id: str | None = None, actor=None):  # noqa: ANN001, ANN201
    task = world.store.task(task_id)
    return world.send("task.complete", {"task_id": task_id, "snapshot_id": snapshot_id, "reason": "done"},
                      expected=task.record_version, actor=actor)


def record_assessment(world: World, criterion_id: str, result: str, snapshot_id: str | None = None,
                      limitations: list[str] | None = None) -> str:
    controller = world.controller
    evidence = []
    if result in ("pass", "fail"):
        evidence = [controller.apply_command(
            {"schema_version": "1.0", "method": "evidence.record_review", "project_id": world.project_id,
             "idempotency_key": new_id("request"),
             "params": {"subject_ref": {"id": criterion_id, "revision": 1}, "observation": {"result": result}}},
            world.actor,
        ).result["evidence_id"]]
    return controller.apply_command(
        {"schema_version": "1.0", "method": "assessment.record", "project_id": world.project_id,
         "idempotency_key": new_id("request"),
         "params": {"criterion_ref": {"id": criterion_id, "revision": 1}, "snapshot_id": snapshot_id,
                    "evidence_refs": evidence, "assessment_result": result, "assessor_ref": "test",
                    "limitations": limitations or []}},
        controller.system_actor,
    ).result["assessment_id"]


# ------------------------------------------------------------------ plans --


def test_plan_creates_a_graph_atomically_and_keeps_revisions(world: World) -> None:
    receipt = plan(world, [planned("design"), planned("implement", depends_on=["design"]),
                           planned("review", depends_on=["implement"])])
    ids = receipt.result["task_ids"]
    assert receipt.result["plan_revision"] == 1
    assert world.store.task(ids["implement"]).depends_on == [ids["design"]]
    plan(world, [planned("document", depends_on=[ids["review"]])])
    assert [rev["plan_revision"] for rev in world.store.plan_revisions()] == [1, 2]
    assert world.store.verify_projections() == []


def test_plan_with_a_cycle_is_rejected_entirely(world: World) -> None:
    before = world.counts()
    with pytest.raises(ForgeError) as caught:
        plan(world, [planned("a", depends_on=["b"]), planned("b", depends_on=["a"])])
    assert caught.value.code == ErrorCode.INVALID_REQUEST
    assert "cycle" in caught.value.details["errors"][0]
    assert world.counts() == before  # no part of the plan exists


def test_plan_reports_every_problem_at_once(world: World) -> None:
    with pytest.raises(ForgeError) as caught:
        plan(world, [
            planned("a", depends_on=["ghost"], criterion_refs=[{"id": new_id("criterion"), "revision": 1}]),
            planned("a"),
            planned("b", authority_ref=new_id("grant")),
        ])
    errors = caught.value.details["errors"]
    assert len(errors) >= 4
    assert any("duplicate task keys" in e for e in errors)
    assert any("unknown task 'ghost'" in e for e in errors)
    assert any("unknown criterion" in e for e in errors)
    assert any("not an approved" in e for e in errors)


def test_plan_based_on_an_old_revision_is_stale(world: World) -> None:
    plan(world, [planned("first")])
    with pytest.raises(ForgeError) as caught:
        plan(world, [planned("second")], base=0)
    assert caught.value.code == ErrorCode.STALE_VERSION


def test_excessive_total_budget_is_rejected(world: World) -> None:
    big = {"max_wall_ms": 24 * 3600 * 1000, "max_attempts": 1}
    with pytest.raises(ForgeError, match="total wall-clock budget"):
        plan(world, [planned(f"t{n}", budget=big) for n in range(8)])


def test_dependency_onto_existing_tasks_is_cycle_checked(world: World) -> None:
    ids = plan(world, [planned("a"), planned("b", depends_on=["a"])]).result["task_ids"]
    with pytest.raises(ForgeError, match="cycle"):
        plan(world, [planned("c")], deps=[{"task": ids["a"], "depends_on": ids["b"]}])


def test_superseding_keeps_history(world: World) -> None:
    ids = plan(world, [planned("old_parser")]).result["task_ids"]
    new_ids = plan(world, [planned("new_parser", supersedes_task_id=ids["old_parser"])]).result["task_ids"]
    old = world.store.task(ids["old_parser"])
    assert old.status == "cancelled"
    assert old.superseded_by_task_id == new_ids["new_parser"]
    assert world.store.task(new_ids["new_parser"]).supersedes_task_id == ids["old_parser"]
    with pytest.raises(ForgeError, match="already superseded"):
        plan(world, [planned("again", supersedes_task_id=ids["old_parser"])])


# ------------------------------------------------------------- completion --


def test_controller_only_task_completes_without_a_job(world: World) -> None:
    task_id = world.task("Write the format specification")
    start(world, task_id)
    receipt = complete(world, task_id)
    assert receipt.result["reason"] == "acknowledged_without_criteria"
    task = world.store.task(task_id)
    assert task.status == "completed"
    assert world.store.jobs(task_id=task_id) == []  # no execution job was needed or invented


def test_models_cannot_declare_criterion_free_work_done(world: World, model_actor) -> None:  # noqa: ANN001
    task_id = world.task()
    start(world, task_id)
    with pytest.raises(ForgeError, match="trusted"):
        complete(world, task_id, actor=model_actor)


def test_completion_requires_passing_assessments(world: World) -> None:
    criterion_id = world.criterion()
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    with pytest.raises(ForgeError) as caught:
        complete(world, task_id)
    assert caught.value.details["evaluation"]["missing_criteria"] == [{"id": criterion_id, "revision": 1}]
    record_assessment(world, criterion_id, "fail")
    with pytest.raises(ForgeError, match="assessed fail"):
        complete(world, task_id)
    record_assessment(world, criterion_id, "pass")
    receipt = complete(world, task_id)
    assert receipt.result["reason"] == "criteria_satisfied"
    assert world.store.task(task_id).completion["used_assessments"]


@pytest.mark.parametrize("result", ["error", "not_run", "inconclusive"])
def test_errors_and_missing_measurements_never_count_as_pass(world: World, result: str) -> None:
    criterion_id = world.criterion()
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    record_assessment(world, criterion_id, result, limitations=["measurement incomplete"])
    with pytest.raises(ForgeError, match=f"assessed {result}"):
        complete(world, task_id)


def test_not_applicable_needs_a_recorded_reason(world: World) -> None:
    criterion_id = world.criterion()
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    record_assessment(world, criterion_id, "not_applicable", limitations=[])
    with pytest.raises(ForgeError):
        complete(world, task_id)
    record_assessment(world, criterion_id, "not_applicable", limitations=["no network code in this tool"])
    assert complete(world, task_id).result["reason"] == "criteria_satisfied"


def _waiver(world: World, criterion_id: str, *, expires=None) -> str:  # noqa: ANN001
    params = {"decision_kind": "waiver", "subject_ref": {"id": criterion_id, "revision": 1},
              "question": "Ship with this known limitation?"}
    if expires:
        params["expires_at"] = expires
    decision_id = world.send("decision.request", params).result["decision_id"]
    world.send("decision.record", {"decision_id": decision_id, "disposition": "approved",
                                   "reason": "documented limitation"}, expected=1)
    return decision_id


def test_approved_waiver_completes_visibly_without_changing_the_failure(world: World) -> None:
    receipt = world.send("criterion.create", {"subject": "Throughput above 10 MB/s", "assessment_method": "benchmark",
                                              "waiver_policy": "allowed_with_decision"})
    criterion_id = receipt.result["criterion_id"]
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    failed = record_assessment(world, criterion_id, "fail")
    decision_id = _waiver(world, criterion_id)
    receipt = complete(world, task_id)
    assert receipt.result["reason"] == "completed_with_waiver"
    assert world.store.task(task_id).completion["applicable_waivers"] == [decision_id]
    assert world.store.assessment(failed).assessment_result == "fail"  # history unchanged


def test_waiver_cannot_bypass_a_forbidden_policy(world: World) -> None:
    criterion_id = world.criterion()  # waiver_policy defaults to forbidden
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    record_assessment(world, criterion_id, "fail")
    _waiver(world, criterion_id)
    with pytest.raises(ForgeError) as caught:
        complete(world, task_id)
    assert caught.value.details["evaluation"]["blocking_results"][0]["waiver_forbidden"] is True


def test_expired_waiver_does_not_apply(world: World, clock) -> None:  # noqa: ANN001
    receipt = world.send("criterion.create", {"subject": "s", "assessment_method": "benchmark",
                                              "waiver_policy": "allowed_with_decision"})
    criterion_id = receipt.result["criterion_id"]
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    record_assessment(world, criterion_id, "fail")
    _waiver(world, criterion_id, expires=format_timestamp(clock.now() + timedelta(hours=1)))
    clock.advance(hours=2)
    with pytest.raises(ForgeError):
        complete(world, task_id)


def test_assessment_for_another_snapshot_does_not_count(world: World) -> None:
    from forge.contracts.ids import new_id as nid

    criterion_id = world.criterion()
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    record_assessment(world, criterion_id, "pass")  # recorded without a snapshot
    with pytest.raises(ForgeError):
        complete(world, task_id, snapshot_id=nid("snapshot"))


def test_decide_task_completes_when_its_decision_is_recorded(world: World) -> None:
    task_id = world.task("Choose byte order", task_type="decide")
    start(world, task_id)
    decision_id = world.send("decision.request", {"decision_kind": "architecture",
                                                  "subject_ref": {"id": task_id, "revision": 2},
                                                  "question": "Big or little endian?"}).result["decision_id"]
    with pytest.raises(ForgeError, match="no final decision"):
        complete(world, task_id)
    world.send("decision.record", {"decision_id": decision_id, "disposition": "approved",
                                   "reason": "matches the vendor document"}, expected=1)
    assert complete(world, task_id).result["reason"] == "decision_recorded"


def test_completion_is_replayable(world: World) -> None:
    criterion_id = world.criterion()
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)
    record_assessment(world, criterion_id, "pass")
    complete(world, task_id)
    assert world.store.verify_projections() == []

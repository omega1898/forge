"""Ticket F22 (partial): embedded pack with a simulated board.

Acceptance: host and emulated results are distinct; an approved board
action records target identity and interruption outcome (EX-T11 with a
board that can lose power mid-flash).
"""

from __future__ import annotations

import pytest

from forge.packs.embedded import (
    SimulatedBoard,
    authorize_board_action,
    board_action_digest,
    emulator_available,
    tier_problem,
)
from tests.conftest import command
from tests.test_jobs import Rig  # noqa: F401 - the F07 rig, reused with a board as its target

FIRMWARE = "sha256:" + "f" * 64


@pytest.fixture
def board_rig(tmp_path, controller, human, trust, clock):  # noqa: ANN001, ANN201
    rig = Rig(tmp_path, controller, human, trust, clock)
    rig.target = SimulatedBoard("SN-0042")
    rig.host = rig.new_host()
    rig.jobs.hosts["local"] = rig.host
    return rig


def flash(rig, task_id: str, serial: str = "SN-0042", operation_id: str = "flash-001") -> str:  # noqa: ANN001
    return rig.jobs.submit(
        task_id, rig.snapshot_id, host_id="local", effect_class="external_effect", retry_class="reconcile_before_retry",
        arguments={"kind": "external_effect", "operation_id": operation_id, "target": serial,
                   "payload": {"target_serial": serial, "firmware_digest": FIRMWARE}},
        grant_ids=[rig.grant.grant_id], limits=rig.policy.max_limits)


def test_tiers_never_stand_in_for_each_other() -> None:
    assert tier_problem("hardware", "hardware") is None
    assert tier_problem("hardware", "emulator") == "requires hardware evidence; this result comes from the emulator"
    assert tier_problem("emulator", "host") is not None
    status = emulator_available()
    assert status["available"] or status["reason"] == "no emulator (QEMU or Renode) is installed"


def test_approval_is_bound_to_board_image_and_operation(board_rig) -> None:  # noqa: ANN001
    rig = board_rig
    request = rig.controller.apply_command(command("decision.request", {
        "decision_kind": "apply_change", "subject_ref": {"id": rig.task(), "revision": 1},
        "question": "Flash firmware f... to board SN-0042?",
        "proposal_digest": board_action_digest("SN-0042", FIRMWARE, "flash-001")}, project_id=rig.project_id),
        rig.human).result
    decision_id = request["decision_id"]
    store = rig.controller.store
    assert authorize_board_action(store, decision_id, "SN-0042", FIRMWARE, "flash-001") == "the decision is pending"
    rig.controller.apply_command(command("decision.record", {
        "decision_id": decision_id, "disposition": "approved", "reason": "bench test board",
        "proposal_digest": board_action_digest("SN-0042", FIRMWARE, "flash-001")}, project_id=rig.project_id,
        expected=next(d for d in store.decisions() if d.decision_id == decision_id).record_version), rig.human)
    assert authorize_board_action(store, decision_id, "SN-0042", FIRMWARE, "flash-001") is None
    assert "different board" in authorize_board_action(store, decision_id, "SN-0099", FIRMWARE, "flash-001")
    assert "different board" in authorize_board_action(store, decision_id, "SN-0042", "sha256:" + "e" * 64, "flash-001")


def test_a_clean_flash_records_the_board_identity(board_rig) -> None:  # noqa: ANN001
    job = board_rig.jobs.settle(flash(board_rig, board_rig.task()))
    assert job.execution_status == "succeeded"
    assert board_rig.target.inspect()["active"] == "B"
    assert job.spec["arguments"]["target"] == "SN-0042"


def test_a_swapped_board_refuses_the_image(board_rig) -> None:  # noqa: ANN001
    job = board_rig.jobs.settle(flash(board_rig, board_rig.task(), serial="SN-0099"))
    assert job.execution_status == "failed"
    assert board_rig.target.flash_count == {} and board_rig.target.inspect()["active"] == "A"


@pytest.mark.parametrize("step", ["erase", "write", "verify"])
def test_power_loss_mid_flash_leaves_uncertain_state_for_a_person_ex_t11(board_rig, step) -> None:  # noqa: ANN001
    rig = board_rig
    rig.target.interrupt_at = step
    task_id = rig.task()
    job_id = flash(rig, task_id)
    rig.jobs.pump()
    rig.target.interrupt_at = None          # power is back
    rig.restart_host()
    job = rig.jobs.settle(job_id)
    assert (job.execution_status, job.reconciliation) == ("lost", "required")
    assert rig.target.lookup("flash-001") == "unknown"
    assert rig.target.inspect()["active"] == "A"            # the old image still boots
    assert rig.controller.store.task(task_id).status == "waiting_for_user"
    for _ in range(3):
        rig.jobs.recover()
    assert rig.target.flash_count["flash-001"] == 1           # never re-flashed automatically


def test_power_loss_after_activation_reconciles_as_applied(board_rig) -> None:  # noqa: ANN001
    rig = board_rig
    rig.host = rig.new_host(frozenset({"after_effect"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = flash(rig, rig.task())
    rig.jobs.pump()
    rig.restart_host()
    job = rig.jobs.settle(job_id)
    assert job.execution_status == "succeeded" and rig.target.lookup("flash-001") == "applied"

"""Ticket F07: job dispatch, leases, cancellation, recovery.

Acceptance: a simulated external-effect fixture is deduplicated or held for
reconciliation after restart; an uncertain effect is never blindly repeated.
Covers EX-T01 (duplicate/changed/fenced attempts), EX-T02 (revocation while
queued), EX-T08 (crashes at receipt, launch, effect and completion
boundaries), EX-T09 (wrong host, expired, tampered requests) and EX-T11
(interrupted external effect).
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from forge.authority.authorize import DEFAULT_LIMITS, ControllerPolicy, HostPolicy
from forge.authority.grants import GrantProposal
from forge.contracts.clock import format_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.controller.service import Controller
from forge.hostagent.agent import HostUnreachable, LocalHostAgent
from forge.jobs.service import JobService
from forge.record.store import ProjectStore
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tools.registry import ToolRegistry
from tests.conftest import BUDGET, command

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason)
)


class FakeTarget:
    """A simulated external system (think: a board being flashed)."""

    def __init__(self, visibility: str = "clear") -> None:
        self.applied: Counter[str] = Counter()
        self.visibility = visibility  # "clear": lookups work; "opaque": state cannot be read

    def apply(self, operation_id: str, payload: dict) -> dict:
        self.applied[operation_id] += 1
        return {"flashed": payload.get("image", "?")}

    def lookup(self, operation_id: str) -> str:
        if self.visibility == "opaque":
            return "unknown"
        return "applied" if self.applied[operation_id] else "absent"


class Rig:
    """One project, one approved grant, one host, one dispatcher."""

    def __init__(self, tmp_path: Path, controller: Controller, human, trust, clock) -> None:  # noqa: ANN001
        self.tmp_path, self.controller, self.human, self.trust, self.clock = tmp_path, controller, human, trust, clock
        source = tmp_path / "source"
        source.mkdir()
        (source / "main.py").write_text("print('hello from the job')\n")
        self.project_id = controller.apply_command(
            command("project.create", {"name": "p", "objective": "o", "source_roots": ["main"]}), human
        ).project_id
        trust.approve_source_root(self.project_id, "main", source, human)
        snap = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=self.project_id), human)
        self.snapshot_id = snap.result["snapshot_id"]
        self.grant = trust.approve(
            GrantProposal(project_id=self.project_id, actions=["run_registered_tool"], path_scope=["src"],
                          justification="run jobs", expires_at=format_timestamp(clock.now() + timedelta(days=1))),
            human,
        )
        self.tools = ToolRegistry()
        python = os.path.realpath(sys.executable)
        self.python = self.tools.admit("python", Path(python), version="host")
        self.target = FakeTarget()
        self.policy = HostPolicy(host_id="local", enrolled_projects=[self.project_id],
                                 allowed_actions=["run_registered_tool"], max_limits=DEFAULT_LIMITS,
                                 backend_capabilities=["network_none", "process_tree_kill"])
        self.host = self.new_host()
        self.jobs = self.new_service(controller)

    def new_host(self, crash_points: frozenset[str] = frozenset(), *, inline: bool = True) -> LocalHostAgent:
        return LocalHostAgent(
            "local", self.tmp_path / "host" / "ledger.db", backend=BACKEND, blobs=self.controller.blobs,
            tools=self.tools, work_root=self.tmp_path / "host" / "work", max_limits=DEFAULT_LIMITS,
            allowed_effects=("read_only", "scratch_only", "external_effect"), external=self.target,
            clock=self.clock, crash_points=crash_points, run_inline=inline,
        )

    def new_service(self, controller: Controller) -> JobService:
        return JobService(controller, {"local": self.host}, host_policies={"local": self.policy},
                          controller_policy=ControllerPolicy(allowed_actions=["run_registered_tool"],
                                                             max_limits=DEFAULT_LIMITS), clock=self.clock)

    def restart_host(self) -> None:
        self.host = self.new_host()
        self.jobs.hosts["local"] = self.host

    def task(self, **extra: object) -> str:
        return self.controller.apply_command(
            command("task.create", {"task_type": "execute", "title": "flash the board", "budget":
                                    {"max_wall_ms": 60_000, "max_attempts": 3},
                                    "authority_ref": self.grant.grant_id, **extra},
                    project_id=self.project_id),
            self.human,
        ).result["task_id"]

    def submit_effect(self, task_id: str, *, operation_id: str = "flash-001", retry_class: str = "reconcile_before_retry") -> str:
        return self.jobs.submit(
            task_id, self.snapshot_id, host_id="local", effect_class="external_effect", retry_class=retry_class,
            arguments={"kind": "external_effect", "operation_id": operation_id, "target": "board-7",
                       "payload": {"image": "firmware-1.2"}},
            grant_ids=[self.grant.grant_id], limits=DEFAULT_LIMITS,
        )

    def submit_command(self, task_id: str, code: str, *, retry_class: str = "safe_to_repeat") -> str:
        return self.jobs.submit(
            task_id, self.snapshot_id, host_id="local", effect_class="scratch_only", retry_class=retry_class,
            arguments={"kind": "sandbox_command", "tool_id": "python", "args": ["-I", "-c", code]},
            grant_ids=[self.grant.grant_id], limits=DEFAULT_LIMITS.model_copy(update={"wall_ms": 30_000}),
            tool_digest=self.python.executable_digest,
        )


@pytest.fixture
def rig(tmp_path, controller, human, trust, clock):  # noqa: ANN001, ANN201
    return Rig(tmp_path, controller, human, trust, clock)


# --------------------------------------------------------- normal running --


def test_external_effect_runs_once_and_job_success_is_not_task_completion(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)
    job = rig.jobs.settle(job_id)
    assert job.execution_status == "succeeded"
    assert rig.target.applied == {"flash-001": 1}
    assert rig.controller.store.task(task_id).status == "running"  # assessment decides completion
    assert rig.controller.store.verify_projections() == []


@needs_sandbox
def test_sandbox_command_job_records_its_observation(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job = rig.jobs.settle(rig.submit_command(task_id, "print('built')"))
    assert job.execution_status == "succeeded"
    assert job.outcome["exit_code"] == 0
    assert job.outcome["processes_remaining"] == 0
    assert rig.controller.blobs.read_bytes(job.outcome["stdout_digest"], max_bytes=100) == b"built\n"


# ------------------------------------------------------------------ EX-T01 --


def test_duplicate_submission_runs_once_and_changed_spec_is_rejected_ex_t01(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)
    rig.jobs.settle(job_id)
    job = rig.controller.store.job(job_id)
    again = rig.host.submit_job(job.spec, job.job_digest)  # transport retry
    assert again.accepted and again.duplicate
    changed = {**job.spec, "arguments": {**job.spec["arguments"], "operation_id": "flash-999"}}
    from forge.contracts.jobs import JobSpec

    rejected = rig.host.submit_job(changed, JobSpec.model_validate(changed).digest())
    assert not rejected.accepted and "different content" in rejected.reason
    assert rig.target.applied == {"flash-001": 1}


def test_late_report_from_a_superseded_attempt_is_fenced_ex_t01(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    rig.host = rig.new_host(frozenset({"after_start"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = rig.submit_command(task_id, "print(1)")
    rig.jobs.pump()
    rig.restart_host()  # attempt 1 is recorded lost by the host
    job = rig.jobs.settle(job_id)
    assert job.attempt_number == 2 and job.lease_epoch == 2  # a new identity, not a reuse
    with pytest.raises(ForgeError) as caught:
        rig.controller.apply_command(
            command("job.observe", {"job_id": job_id, "attempt_number": 1, "lease_epoch": 1, "sequence": 99,
                                    "kind": "finished", "state": "succeeded"}, project_id=rig.project_id),
            rig.controller.system_actor,
        )
    assert caught.value.details["reason"] == "fenced"


# ------------------------------------------------------------------ EX-T02 --


def test_grant_revoked_while_queued_never_starts_ex_t02(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)       # recorded, waiting in the outbox
    rig.trust.revoke(rig.grant.grant_id, rig.human)
    rig.jobs.pump()
    job = rig.controller.store.job(job_id)
    assert job.execution_status == "failed"
    assert rig.grant.grant_id in job.outcome["rejected_by_host"]
    assert rig.host.get_job(job_id, 1) is None  # the host never received it
    assert rig.target.applied == {}
    with pytest.raises(ForgeError) as caught:      # new submissions are refused up front
        rig.submit_effect(task_id)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert caught.value.details["reasons"] == ["grant_revoked"]


# ------------------------------------------------------------------ EX-T08 --


def test_controller_restart_before_dispatch_sends_exactly_once_ex_t08(rig, tmp_path, clock, trust) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)  # committed; the controller "dies" before pump()
    store_path = rig.controller.store.path
    rig.controller.store.close()
    store = ProjectStore.open(store_path, clock)
    restarted = Controller(store, clock=clock, trust_store=trust, controller_id=rig.controller.controller_id)
    service = rig.new_service(restarted)
    service.recover()
    job = service.settle(job_id)
    assert job.execution_status == "succeeded"
    assert rig.target.applied == {"flash-001": 1}
    store.close()


def test_controller_crash_after_host_receipt_does_not_duplicate_ex_t08(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)
    job = rig.controller.store.job(job_id)
    rig.host.submit_job(job.spec, job.job_digest)  # host accepted and ran it; the receipt was lost
    rig.jobs.recover()                              # resend: the host ledger answers "duplicate"
    assert rig.jobs.settle(job_id).execution_status == "succeeded"
    assert rig.target.applied == {"flash-001": 1}


def test_host_crash_before_reply_is_resent_safely_ex_t08(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    rig.host = rig.new_host(frozenset({"after_receipt"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = rig.submit_effect(task_id)
    rig.jobs.pump()  # the host recorded the request, then died before replying
    assert rig.controller.store.job(job_id).execution_status == "queued"
    rig.restart_host()
    job = rig.jobs.settle(job_id)
    # The host recorded the request but never recorded an intent: the effect was
    # never attempted, so a fenced retry is safe and the effect happens once.
    assert job.execution_status == "succeeded"
    assert rig.target.applied == {"flash-001": 1}


def test_host_crash_after_effect_reconciles_as_succeeded_ex_t08(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    rig.host = rig.new_host(frozenset({"after_effect"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = rig.submit_effect(task_id)
    rig.jobs.pump()
    rig.restart_host()  # the effect happened; the completion report did not
    job = rig.jobs.settle(job_id)
    assert job.execution_status == "succeeded"
    assert job.outcome["reconciled"] is True
    assert job.attempt_number == 1
    assert rig.target.applied == {"flash-001": 1}  # not repeated


def test_confirmed_absent_effect_is_retried_once(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    rig.host = rig.new_host(frozenset({"after_intent"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = rig.submit_effect(task_id)
    rig.jobs.pump()
    rig.restart_host()  # intent recorded, effect not applied, target confirms absence
    job = rig.jobs.settle(job_id)
    assert (job.attempt_number, job.execution_status) == (2, "succeeded")
    assert rig.target.applied == {"flash-001": 1}


# ------------------------------------------------------------------ EX-T11 --


def test_interrupted_flash_with_unknown_state_requires_reconciliation_ex_t11(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    rig.target.visibility = "opaque"
    rig.host = rig.new_host(frozenset({"after_intent"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = rig.submit_effect(task_id)
    rig.jobs.pump()
    rig.restart_host()
    job = rig.jobs.settle(job_id)
    assert (job.execution_status, job.reconciliation, job.attempt_number) == ("lost", "required", 1)
    task = rig.controller.store.task(task_id)
    assert task.status == "blocked"
    for _ in range(3):  # further recovery passes never repeat the effect
        rig.jobs.recover()
    assert rig.controller.store.job(job_id).attempt_number == 1
    assert sum(rig.target.applied.values()) == 0


def test_never_automatic_effects_are_not_retried_even_when_absent(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    rig.host = rig.new_host(frozenset({"after_intent"}))
    rig.jobs.hosts["local"] = rig.host
    job_id = rig.submit_effect(task_id, retry_class="never_automatic")
    rig.jobs.pump()
    rig.restart_host()
    job = rig.jobs.settle(job_id)
    assert (job.reconciliation, job.attempt_number) == ("required", 1)
    assert rig.target.applied == {}


# ------------------------------------------------------------------ EX-T09 --


def test_host_rejects_wrong_recipient_expired_and_tampered_requests_ex_t09(rig) -> None:  # noqa: ANN001
    from forge.contracts.jobs import JobSpec

    task_id = rig.task()
    job_id = rig.submit_effect(task_id)
    job = rig.controller.store.job(job_id)
    wrong_host = {**job.spec, "host_id": "other"}
    assert "addressed to other" in rig.host.submit_job(wrong_host, JobSpec.model_validate(wrong_host).digest()).reason
    assert "digest does not match" in rig.host.submit_job(job.spec, "sha256:" + "1" * 64).reason
    rig.clock.advance(hours=2)
    assert "expired" in rig.host.submit_job(job.spec, job.job_digest).reason
    assert rig.target.applied == {}


# ------------------------------------------------------------ cancellation --


@needs_sandbox
def test_cancel_stops_a_running_job_and_its_children(rig) -> None:  # noqa: ANN001
    rig.host = rig.new_host(inline=False)
    rig.jobs.hosts["local"] = rig.host
    task_id = rig.task()
    marker = f"forge-cancel-{os.getpid()}"
    job_id = rig.submit_command(
        task_id, f"import subprocess,time; subprocess.Popen(['/bin/sh','-c','exec -a {marker} sleep 300']); time.sleep(300)"
    )
    import subprocess

    rig.jobs.pump()
    # Wait until the grandchild process really exists, so this tests
    # cancelling live work rather than a job that had not started yet.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        rig.jobs.poll()
        if subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split():
            break
        time.sleep(0.05)
    else:
        pytest.fail("the job never started its child process")
    assert rig.controller.store.job(job_id).execution_status == "running"
    receipt = rig.jobs.cancel_task(task_id, "no longer needed", actor=rig.human)
    assert receipt.result["status"] == "cancellation_pending"
    job = rig.jobs.settle(job_id, timeout=30)
    assert job.execution_status == "cancelled"
    assert job.outcome["processes_remaining"] == 0
    assert rig.controller.store.task(task_id).status == "cancelled"
    assert subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split() == []
    rig.host.close()


def test_cancel_before_dispatch_never_runs_the_effect(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)  # recorded and waiting in the outbox
    rig.jobs.cancel_task(task_id, "stop before flashing")
    job = rig.jobs.settle(job_id)
    assert job.execution_status == "cancelled"
    assert job.outcome == {"cancelled_before_dispatch": True}
    assert rig.host.get_job(job_id, 1) is None  # the host never received it
    assert rig.target.applied == {}             # the board was never touched
    assert rig.controller.store.task(task_id).status == "cancelled"
    assert rig.controller.store.pending_outbox() == []


def test_jobs_only_start_ready_execute_tasks(rig) -> None:  # noqa: ANN001
    prepare = rig.controller.apply_command(
        command("task.create", {"task_type": "prepare", "title": "design", "budget": BUDGET},
                project_id=rig.project_id), rig.human).result["task_id"]
    with pytest.raises(ForgeError, match="runs without jobs"):
        rig.submit_effect(prepare)
    blocked = rig.task(depends_on=[prepare])
    with pytest.raises(ForgeError, match="not ready"):
        rig.submit_effect(blocked)


def test_clients_and_models_cannot_drive_jobs(rig, model_actor) -> None:  # noqa: ANN001
    for method in ("job.observe", "job.receipt", "job.create"):
        with pytest.raises(ForgeError) as caught:
            rig.controller.apply_command(command(method, {}, project_id=rig.project_id), model_actor)
        assert caught.value.code in (ErrorCode.PERMISSION_DENIED, ErrorCode.INVALID_REQUEST)


def test_unreachable_host_leaves_work_pending(rig) -> None:  # noqa: ANN001
    task_id = rig.task()
    job_id = rig.submit_effect(task_id)
    rig.host.alive = False
    assert rig.jobs.pump() == 0
    assert rig.controller.store.pending_outbox()[0]["job_id"] == job_id
    with pytest.raises(HostUnreachable):
        rig.host.get_capabilities()

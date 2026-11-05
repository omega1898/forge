"""Ticket F17: truthful platform status (EX-T10).

A Linux test run through a Windows or Mac interface records Linux; native
checks for other platforms remain not_run and block completion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.assessment.service import AssessmentService
from forge.contracts.errors import ForgeError
from forge.contracts.ids import new_id
from forge.platforms import (
    environment_matches,
    native_problem,
    platform_status,
    render_platform_status,
    select_host,
)
from forge.sandbox.base import BackendCapabilities
from forge.sandbox.linux_ns import LinuxNamespaceBackend, detect_virtualization
from tests.conftest import command
from tests.test_engine import complete, start
from tests.test_evaluator import CORRECT, suite_document

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason)
)


def caps(os_name: str = "linux", arch: str = "x86_64", **extra) -> BackendCapabilities:  # noqa: ANN003
    return BackendCapabilities(backend_id="b", available=True, os=os_name, arch=arch, capabilities=[],
                               suitable_for_hostile_code=False, **extra)


class ReachedThrough:
    """A Linux backend reached from another OS (WSL on Windows, a Linux VM on a Mac)."""

    def __init__(self, inner, *, interface_os: str, virtualization: str) -> None:  # noqa: ANN001
        self.inner, self.interface_os, self.virtualization = inner, interface_os, virtualization
        self.runs = 0

    def capabilities(self) -> BackendCapabilities:
        return self.inner.capabilities().model_copy(
            update={"interface_os": self.interface_os, "virtualization": self.virtualization})

    def run(self, spec, work_dir):  # noqa: ANN001, ANN201
        self.runs += 1
        return self.inner.run(spec, work_dir)

    def destroy(self, work_dir) -> None:  # noqa: ANN001
        self.inner.destroy(work_dir)


# --------------------------------------------------------------------- units


def test_a_linux_backend_reached_through_windows_is_linux() -> None:
    wsl = caps(virtualization="wsl", interface_os="windows")
    assert native_problem("linux", wsl) is None
    problem = native_problem("windows", wsl)
    assert problem == "requires native windows; this host executes linux x86_64 (in WSL, reached through windows)"
    mac_vm = caps(arch="arm64", virtualization="vm", interface_os="macos")
    assert "linux aarch64 (in a virtual machine, reached through macos)" in native_problem("macos", mac_vm)
    assert native_problem("linux_aarch64", mac_vm) is None
    assert "linux_x86_64" in native_problem("linux_x86_64", mac_vm)


def test_emulation_and_unavailable_backends_are_never_native() -> None:
    assert "emulated" in native_problem("linux", caps(virtualization="emulator"))
    down = caps().model_copy(update={"available": False, "unavailable_reason": "no root"})
    assert "unavailable" in native_problem("linux", down)
    with pytest.raises(ValueError):
        native_problem("solaris", caps())


def test_jobs_route_only_to_compatible_hosts() -> None:
    hosts = {"laptop_wsl": caps(virtualization="wsl", interface_os="windows"),
             "mac_mini": caps("macos", "arm64", virtualization="none"),
             "build_box": caps(virtualization="none")}
    assert select_host("linux", hosts)[0] == "build_box"
    chosen, reasons = select_host("macos_aarch64", hosts)
    assert chosen == "mac_mini" and "build_box" in reasons
    chosen, reasons = select_host("windows", hosts)
    assert chosen is None and set(reasons) == set(hosts)
    assert "in WSL, reached through windows" in reasons["laptop_wsl"]


def test_environment_names_match_by_os_and_optional_arch() -> None:
    assert environment_matches("linux", "linux_x86_64")
    assert environment_matches("linux_x86_64", "linux_x86_64")
    assert not environment_matches("linux_x86_64", "linux_aarch64")
    assert not environment_matches("windows", "linux_x86_64")
    assert not environment_matches("linux", None) and not environment_matches("linux", "bogus")


@pytest.mark.parametrize(("version", "flags", "expected"), [
    ("Linux version 5.15.153.1-microsoft-standard-WSL2", "flags : fpu", "wsl"),
    ("Linux version 6.8.0", "flags : fpu vme hypervisor", "vm"),
    ("Linux version 6.8.0", "flags : fpu vme", "none"),
])
def test_virtualization_is_detected_from_the_kernel_view(tmp_path: Path, version: str, flags: str,
                                                         expected: str) -> None:
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "version").write_text(version)
    (tmp_path / "proc" / "cpuinfo").write_text(f"processor : 0\n{flags}\n")
    result = detect_virtualization(str(tmp_path / "proc"), str(tmp_path / "sys"))
    # A container marker on the real host would mask the fixture; accept only that.
    assert result in (expected, "container")
    assert detect_virtualization(str(tmp_path / "missing"), str(tmp_path / "sys")) in ("unknown", "container")


# -------------------------------------------------- completion per platform


def test_each_required_platform_needs_its_own_pass(world) -> None:  # noqa: ANN001
    criterion_id = world.send("criterion.create", {"subject": "Runs on Linux and macOS",
                                                   "assessment_method": "test_suite",
                                                   "required_environments": ["linux", "macos"]}
                              ).result["criterion_id"]
    task_id = world.task(criterion_refs=[{"id": criterion_id, "revision": 1}])
    start(world, task_id)

    def assess(result: str, environment: str, limitations: list[str] | None = None) -> None:
        evidence = [] if result != "pass" else [world.send("evidence.record_review", {
            "subject_ref": {"id": criterion_id, "revision": 1},
            "observation": {"ran_on": environment}}).result["evidence_id"]]
        world.controller.apply_command(
            {"schema_version": "1.0", "method": "assessment.record", "project_id": world.project_id,
             "idempotency_key": new_id("request"),
             "params": {"criterion_ref": {"id": criterion_id, "revision": 1}, "snapshot_id": None,
                        "assessment_result": result,
                        "evidence_refs": evidence,
                        "assessor_ref": "test", "limitations": limitations or ["platform evidence"],
                        "summary": {"environment": environment}}},
            world.controller.system_actor)

    assess("pass", "linux_x86_64")
    with pytest.raises(ForgeError) as caught:
        complete(world, task_id)
    assert caught.value.details["evaluation"]["missing_environments"] == [f"{criterion_id}:macos"]
    assess("not_run", "macos", ["requires native macos; this host executes linux x86_64"])
    with pytest.raises(ForgeError, match="assessed not_run") as caught:
        complete(world, task_id)
    [blocking] = caught.value.details["evaluation"]["blocking_results"]
    assert blocking["environment"] == "macos"   # the not_run names the platform it blocks
    assess("pass", "macos_aarch64")
    assert complete(world, task_id).result["reason"] == "criteria_satisfied"
    assert world.store.task(task_id).status == "completed"


# ---------------------------------------------------- end to end (EX-T10)


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Packet reader", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)
    suite = controller.apply_command(command("suite.admit", {"suite": suite_document()}, project_id=project_id),
                                     human).result

    def criterion(environments: list[str]) -> str:
        return controller.apply_command(command("criterion.create", {
            "subject": f"The reader handles the boundary cases on {', '.join(environments)}",
            "assessment_method": "black_box_cases", "required_environments": environments,
            "parameters": {"suite_artifact_id": suite["suite_artifact_id"], "suite_digest": suite["suite_digest"]}},
            project_id=project_id), human).result["criterion_id"]

    (source / "reader.py").write_text(CORRECT)
    snapshot_id = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                   project_id=project_id), human).result["snapshot_id"]
    backend = ReachedThrough(BACKEND, interface_os="windows", virtualization="wsl")
    return {"criterion": criterion, "snapshot_id": snapshot_id, "backend": backend, "controller": controller,
            "service": AssessmentService(controller, backend, tmp_path / "work")}


@needs_sandbox
def test_linux_run_through_a_windows_interface_states_linux_and_leaves_windows_unrun(rig) -> None:
    criterion_id = rig["criterion"](["linux", "windows"])
    outcome = rig["service"].assess_black_box(criterion_id, rig["snapshot_id"])
    store = rig["controller"].store
    assert outcome["result"] == "pass"
    platform = store.evidence(outcome["evidence_id"]).observation["execution_platform"]
    assert (platform["os"], platform["virtualization"], platform["interface_os"]) == ("linux", "wsl", "windows")
    linux = store.assessment(outcome["assessment_id"])
    assert linux.summary["environment"].startswith("linux_")
    assert "reached through windows" in linux.summary["executed_on"]
    [windows_id] = outcome["not_run_ids"]
    windows = store.assessment(windows_id)
    assert windows.assessment_result == "not_run" and windows.summary["environment"] == "windows"
    assert windows.evidence_refs == []
    assert "requires native windows; this host executes linux" in windows.limitations[0]

    rows = platform_status(store, rig["snapshot_id"])
    assert [(row.environment, row.result) for row in rows] == [("linux", "pass"), ("windows", "not_run")]
    table = render_platform_status(rows)
    assert "**not_run**" in table and "**pass**" in table


@needs_sandbox
def test_a_windows_only_check_is_not_run_at_all_on_a_linux_host(rig) -> None:
    criterion_id = rig["criterion"](["windows_x86_64"])
    outcome = rig["service"].assess_black_box(criterion_id, rig["snapshot_id"])
    assert outcome["result"] == "not_run" and outcome["evidence_id"] is None
    assert rig["backend"].runs == 0
    [row] = platform_status(rig["controller"].store, rig["snapshot_id"])
    assert (row.environment, row.result, row.executed_on) == ("windows_x86_64", "not_run", None)


def test_a_check_with_no_result_at_all_shows_as_not_run(world) -> None:  # noqa: ANN001
    world.send("criterion.create", {"subject": "Installs on macOS", "assessment_method": "test_suite",
                                    "required_environments": ["macos"]})
    [row] = platform_status(world.store, new_id("snapshot"))
    assert (row.result, row.reason) == ("not_run", "no compatible host has run this check")
    assert "| Installs on macOS | macos | **not_run** |" in render_platform_status([row])

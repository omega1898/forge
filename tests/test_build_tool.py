"""Ticket F11: the build_tool workflow end to end (the walking skeleton).

Acceptance: a bounded CLI utility is planned, built, independently assessed,
packaged, and reviewed through one record. The model is the fake adapter
(blueprint Session 5: "connect the fake model's patch proposal through the
full path"); swapping in a live model changes only the adapter.
"""

from __future__ import annotations

import glob
import json
import shutil
from pathlib import Path

import pytest

from forge.assessment.evaluator import encode_files
from forge.assessment.service import AssessmentService
from forge.gateway.adapters import FakeModelAdapter
from forge.gateway.contracts import ModelConfig
from forge.gateway.service import ModelGateway
from forge.knowledge.context import ContextBuilder
from forge.knowledge.index import SnapshotIndex
from forge.reports.summary import build_summary
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tools.registry import ToolRegistry
from forge.workflows.build_tool import BuildToolSpec, BuildToolWorkflow
from forge.workspace.snapshot import load_manifest
from tests.conftest import command

BACKEND = LinuxNamespaceBackend()
SETUPTOOLS = sorted(glob.glob("/root/.cache/uv/archive-v0/*/virtualenv/seed/wheels/embed/setuptools-8*.whl")
                    + glob.glob("/usr/lib/python3*/ensurepip/_bundled/setuptools-7*.whl"))
pytestmark = [
    pytest.mark.skipif(not BACKEND.capabilities().available, reason="needs the Linux sandbox"),
    pytest.mark.skipif(not SETUPTOOLS, reason="needs a clean setuptools wheel to admit as build backend"),
]

PYPROJECT = """[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "packet-reader"
version = "0.1.0"

[project.scripts]
packet-reader = "packet_reader.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
"""

CLI_CORRECT = '''import json
import sys


def parse(data):
    packets, offset = [], 0
    while offset < len(data):
        if len(data) - offset < 3:
            return packets, {"error": "truncated_header", "offset": offset}
        kind, length = data[offset], int.from_bytes(data[offset + 1:offset + 3], "big")
        if len(data) - offset - 3 < length:
            return packets, {"error": "truncated_payload", "offset": offset}
        packets.append({"type": kind, "length": length})
        offset += 3 + length
    return packets, None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: packet-reader FILE")
        return 0
    with open(argv[0], "rb") as handle:
        packets, error = parse(handle.read())
    if error:
        print(json.dumps(error))
        return 2
    print(json.dumps(packets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
CLI_BUGGY = CLI_CORRECT.replace(
    '''        if len(data) - offset - 3 < length:
            return packets, {"error": "truncated_payload", "offset": offset}
''', "")


def proposal(cli: str) -> dict:
    return {"files": [{"path": "pyproject.toml", "content": PYPROJECT},
                      {"path": "src/packet_reader/__init__.py", "content": ""},
                      {"path": "src/packet_reader/cli.py", "content": cli}],
            "rationale": "length-prefixed parser with structured errors", "record_ids": []}


def suite() -> dict:
    def case(case_id: str, data: bytes, **expect: object) -> dict:
        return {"case_id": case_id, "description": case_id, "args": ["{case}/packets.bin"],
                "files": encode_files({"packets.bin": data}), "expect": expect}

    return {"suite_id": "packet_reader", "revision": 1, "description": "acceptance cases",
            "entrypoint": ["{python}", "-I", "src/packet_reader/cli.py"],
            "cases": [case("empty", b"", exit_code=0, stdout_json=[]),
                      case("one", b"\x01\x00\x02hi", exit_code=0, stdout_json=[{"type": 1, "length": 2}]),
                      case("truncated_payload", b"\x01\x00\x05ab", exit_code="nonzero",
                           stdout_json={"error": "truncated_payload", "offset": 0})]}


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Packet reader\nReads length-prefixed packet files.\n")
    project_id = controller.apply_command(command("project.create", {
        "name": "Packet reader", "objective": "Read packets; reject incomplete ones", "source_roots": ["main"]}),
        human).project_id
    trust.approve_source_root(project_id, "main", source, human)
    base = controller.apply_command(command("workspace.capture", {"source_root": "main"}, project_id=project_id),
                                    human).result["snapshot_id"]
    fake = FakeModelAdapter([{"kind": "text", "text": "ready"}, {"answer": "yes"}])
    gateway = ModelGateway(controller, adapters={"fake": fake})
    gateway.register(ModelConfig(model_config_id="local_small", provider_type="fake", model_name="small",
                                 runtime_version="test", context_capacity=32_768, max_output_tokens=4096))
    gateway.probe("local_small")
    assessment = AssessmentService(controller, BACKEND, tmp_path / "work")
    assessment.admit_build_dependency(Path(SETUPTOOLS[-1]))
    ruff_path = shutil.which("ruff")
    ruff = None
    if ruff_path:
        real = Path(ruff_path).resolve()
        ruff = ToolRegistry().admit("ruff", real, version="host", toolchain_paths=[str(real.parent.parent)])
    workflow = BuildToolWorkflow(controller, human=human, assessment=assessment, gateway=gateway,
                                 model_config_id="local_small",
                                 context=ContextBuilder(controller.store, controller.blobs,
                                                        SnapshotIndex(tmp_path / "index"), trust_store=trust),
                                 ruff=ruff)
    spec = BuildToolSpec(name="packet-reader", console_script="packet-reader", suite=suite(),
                         request_text="Build a utility that reads a file of packets (1-byte type, 2-byte big-endian "
                                      "length, payload), prints them as JSON, and rejects incomplete records.")
    return {"workflow": workflow, "fake": fake, "base": base, "spec": spec, "controller": controller,
            "human": human, "project_id": project_id}


def test_walking_skeleton_from_request_to_released_package(rig) -> None:  # noqa: ANN001
    controller, fake = rig["controller"], rig["fake"]
    fake.script(proposal(CLI_BUGGY), proposal(CLI_CORRECT))
    report = rig["workflow"].run(rig["spec"], rig["base"])

    assert report.status == "awaiting_review", report.blocker
    # Two candidates from the same base, with different identities.
    assert len(report.candidates) == 2
    first, second = (controller.store.snapshot(s) for s in report.candidates)
    assert first.manifest_digest != second.manifest_digest
    assert first.parent_snapshot_id == second.parent_snapshot_id == rig["base"]
    behavior = [a["result"] for a in report.assessments if a["criterion"] == "behavior"]
    assert behavior == ["fail", "pass"]
    # The repair prompt located the failure without revealing the expected answer.
    [repair] = report.repair_prompts
    assert "case truncated_payload" in repair
    assert '"offset": 0' not in repair and "truncated_payload\", \"offset" not in repair
    assert report.package["result"] == "pass", report.package
    assert controller.store.artifact(report.package["wheel_artifact_ids"][0]).artifact_kind == "python_wheel"

    # Only the user can release it.
    decision = controller.store.decision(report.decision_id)
    assert decision.decision_kind == "release" and decision.disposition == "pending"
    controller.apply_command(command("decision.record", {"decision_id": report.decision_id,
                                                         "disposition": "approved", "reason": "tests pass"},
                                     project_id=rig["project_id"], expected=1), rig["human"])
    rig["workflow"].finish_review(report)

    statuses = {name: controller.store.task(task_id).status for name, task_id in report.task_ids.items()}
    assert statuses == {name: "completed" for name in statuses}
    implement = controller.store.task(report.task_ids["implement"])
    assert implement.completion["snapshot_id"] == report.final_snapshot_id
    # The base snapshot (the user's files) was never modified.
    base = load_manifest(controller.blobs, controller.store.snapshot(rig["base"]).manifest_digest)
    assert [e.path for e in base.entries] == ["README.md"]
    assert len(controller.store.model_calls(task_id=report.task_ids["implement"])) == 2
    assert controller.store.verify_projections() == []
    summary = build_summary(controller.store, now=controller.clock.now())
    assert summary.status_counts["completed"] == 5


def test_repair_budget_exhaustion_blocks_without_lowering_the_bar(rig) -> None:  # noqa: ANN001
    spec = rig["spec"]
    spec.max_repairs = 1
    different_but_still_wrong = "# second attempt: same bug, different bytes\n" + CLI_BUGGY
    assert different_but_still_wrong != CLI_BUGGY
    rig["fake"].script(proposal(CLI_BUGGY), proposal(different_but_still_wrong))
    report = rig["workflow"].run(spec, rig["base"])
    assert report.status == "blocked"
    assert "repair budget exhausted" in report.blocker
    task = rig["controller"].store.task(report.task_ids["implement"])
    assert task.status == "waiting_for_user"
    assert rig["controller"].store.task(report.task_ids["package"]).status == "queued"  # never reached


def test_identical_repair_is_no_progress(rig) -> None:  # noqa: ANN001
    rig["fake"].script(proposal(CLI_BUGGY), proposal(CLI_BUGGY))
    report = rig["workflow"].run(rig["spec"], rig["base"])
    assert report.status == "blocked" and report.blocker.startswith("no_progress")


def test_out_of_scope_or_broken_proposals_never_become_candidates(rig) -> None:  # noqa: ANN001
    escape = {"files": [{"path": ".github/workflows/x.yml", "content": "on: push"}], "rationale": "r", "record_ids": []}
    broken = {"files": [{"path": "src/packet_reader/cli.py", "content": "def broken(:"}], "rationale": "r",
              "record_ids": []}
    rig["fake"].script(escape, broken)
    report = rig["workflow"].run(rig["spec"], rig["base"])
    assert report.status == "blocked"
    assert report.candidates == []  # nothing was built from invalid proposals
    calls = rig["controller"].store.model_calls()
    assert [c["status"] for c in calls[-2:]] == ["valid", "valid"]  # valid JSON, rejected by slot validators

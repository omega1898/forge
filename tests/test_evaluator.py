"""Ticket F05: protected evaluator, evidence and assessments (+D02, D03).

Acceptance: the candidate cannot alter the evaluator configuration; results
distinguish a tool error, a failed criterion and a pass (EX-T06: forged
success output is not accepted).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from forge.assessment.analyzers import NormalizedFinding, classify, fingerprint, verdict
from forge.assessment.evaluator import compare, encode_files, first_difference
from forge.assessment.service import AssessmentService
from forge.assessment.suite import EvaluatorSuite
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.sandbox.base import ExecutionObservation
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.sandbox.unavailable import PodmanBackend
from forge.tools.registry import ToolRegistry
from tests.conftest import command

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason)
)

CORRECT = '''import json, sys
data = open(sys.argv[1], "rb").read()
packets, offset = [], 0
while offset < len(data):
    if len(data) - offset < 3:
        print(json.dumps({"error": "truncated_header", "offset": offset}))
        sys.exit(2)
    kind, length = data[offset], int.from_bytes(data[offset + 1:offset + 3], "big")
    if len(data) - offset - 3 < length:
        print(json.dumps({"error": "truncated_payload", "offset": offset}))
        sys.exit(2)
    packets.append({"type": kind, "length": length})
    offset += 3 + length
print(json.dumps(packets))
'''
# Bug: never checks that the payload is complete.
BUGGY = CORRECT.replace(
    '''    if len(data) - offset - 3 < length:
        print(json.dumps({"error": "truncated_payload", "offset": offset}))
        sys.exit(2)
''', ""
)
# Tries to win without being correct (EX-T06).
CHEATER = '''import json, os, sys
print("ALL TESTS PASSED")
for path in ("/output/results.json", "/scratch/results.json"):
    try:
        with open(path, "w") as handle:
            json.dump({"passed": 999, "failed": 0}, handle)
    except OSError:
        pass
for path in ("/input/tests/expected.json", "/case/expected.json"):
    try:
        with open(path, "w") as handle:
            handle.write("{}")
    except OSError:
        pass
sys.exit(0)
'''


def suite_document() -> dict:
    def case(case_id: str, data: bytes, **expect: object) -> dict:
        return {
            "case_id": case_id,
            "description": case_id.replace("_", " "),
            "args": ["{case}/packets.bin"],
            "files": encode_files({"packets.bin": data}),
            "expect": expect,
        }

    return {
        "suite_id": "packet_reader",
        "revision": 1,
        "description": "black-box acceptance cases for the packet reader",
        "entrypoint": ["{python}", "-I", "reader.py"],
        "cases": [
            case("empty_file", b"", exit_code=0, stdout_json=[]),
            case("one_packet", b"\x01\x00\x02hi", exit_code=0, stdout_json=[{"type": 1, "length": 2}]),
            case(
                "two_packets", b"\x01\x00\x01a\x07\x00\x00", exit_code=0,
                stdout_json=[{"type": 1, "length": 1}, {"type": 7, "length": 0}],
            ),
            case("truncated_header", b"\x01\x00", exit_code="nonzero",
                 stdout_json={"error": "truncated_header", "offset": 0}),
            case("truncated_payload", b"\x01\x00\x05ab", exit_code="nonzero",
                 stdout_json={"error": "truncated_payload", "offset": 0}),
        ],
    }


@pytest.fixture
def setup(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "tests" / "expected.json").write_text('{"note": "candidate-owned copy, not the evaluator"}')
    project_id = controller.apply_command(
        command("project.create", {"name": "Packet reader", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)
    admitted = controller.apply_command(
        command("suite.admit", {"suite": suite_document()}, project_id=project_id), human
    ).result
    criterion_id = controller.apply_command(
        command(
            "criterion.create",
            {
                "subject": "The reader handles the five boundary cases",
                "assessment_method": "black_box_cases",
                "parameters": {"suite_artifact_id": admitted["suite_artifact_id"],
                               "suite_digest": admitted["suite_digest"]},
            },
            project_id=project_id,
        ),
        human,
    ).result["criterion_id"]

    def snapshot_of(program: str) -> str:
        (source / "reader.py").write_text(program)
        return controller.apply_command(
            command("workspace.capture", {"source_root": "main"}, project_id=project_id), human
        ).result["snapshot_id"]

    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    return {"project_id": project_id, "criterion_id": criterion_id, "snapshot_of": snapshot_of,
            "service": service, "suite": admitted, "source": source}


# ------------------------------------------------------ end to end (sandbox) --


@needs_sandbox
def test_correct_candidate_passes(setup, controller) -> None:  # noqa: ANN001
    outcome = setup["service"].assess_black_box(setup["criterion_id"], setup["snapshot_of"](CORRECT))
    assert outcome["result"] == "pass", outcome["run"].cases
    assessment = controller.store.assessment(outcome["assessment_id"])
    assert assessment.assessment_result == "pass"
    assert assessment.evidence_refs == [outcome["evidence_id"]]
    evidence = controller.store.evidence(outcome["evidence_id"])
    assert evidence.origin == "execution" and evidence.evaluator_digest.startswith("sha256:")
    assert any("finite cases" in text for text in assessment.limitations)


@needs_sandbox
def test_buggy_candidate_fails_with_located_reason(setup) -> None:  # noqa: ANN001
    outcome = setup["service"].assess_black_box(setup["criterion_id"], setup["snapshot_of"](BUGGY))
    assert outcome["result"] == "fail"
    failed = {case.case_id: case for case in outcome["run"].cases if case.outcome == "fail"}
    assert set(failed) == {"truncated_payload"}
    # The reason locates the failure without revealing the expected answer.
    assert failed["truncated_payload"].reasons == [
        "exited 0 but a failure exit was required",
        "stdout JSON differs at $",
    ]


@needs_sandbox
def test_forged_success_is_not_accepted_ex_t06(setup, controller) -> None:  # noqa: ANN001
    outcome = setup["service"].assess_black_box(setup["criterion_id"], setup["snapshot_of"](CHEATER))
    assert outcome["result"] == "fail"
    assert outcome["run"].counts == {"pass": 0, "fail": 5, "error": 0}
    # The candidate's own "expected" file was untouched in the source, and the
    # protected suite is byte-identical to what was admitted.
    assert "candidate-owned" in (setup["source"] / "tests" / "expected.json").read_text()
    suite = setup["service"].load_suite(setup["suite"]["suite_artifact_id"], setup["suite"]["suite_digest"])
    assert suite.digest() == setup["suite"]["suite_digest"]


@needs_sandbox
def test_tampered_suite_blob_is_refused(setup, controller) -> None:  # noqa: ANN001
    path = controller.blobs._path(setup["suite"]["suite_digest"])
    os.chmod(path, 0o644)
    path.write_bytes(path.read_bytes() + b" ")  # someone edits the stored expected answers
    with pytest.raises(ForgeError) as caught:
        setup["service"].assess_black_box(setup["criterion_id"], setup["snapshot_of"](CORRECT))
    assert caught.value.code == ErrorCode.ARTIFACT_MISSING


@needs_sandbox
def test_nondeterministic_output_is_inconclusive_d03(setup) -> None:  # noqa: ANN001
    # Valid JSON every time, but trailing whitespace varies from run to run, so
    # each run passes while the output bytes differ. (The process ID cannot be
    # used for this: in a fresh PID namespace it is the same on every run.)
    flaky = CORRECT.replace(
        "print(json.dumps(packets))",
        "import os\nprint(json.dumps(packets) + ' ' * (1 + os.urandom(1)[0] % 50))",
    )
    outcome = setup["service"].assess_black_box(setup["criterion_id"], setup["snapshot_of"](flaky), repeat=4)
    assert outcome["result"] == "inconclusive"
    assert any("different results" in text for text in outcome["run"].limitations)


@needs_sandbox
def test_suite_that_cannot_fail_is_detected_d03(setup) -> None:  # noqa: ANN001
    service = setup["service"]
    full = service.load_suite(setup["suite"]["suite_artifact_id"], setup["suite"]["suite_digest"])
    weak = EvaluatorSuite.model_validate(
        {**full.model_dump(mode="json"), "suite_id": "weak", "cases": full.model_dump(mode="json")["cases"][:3]}
    )
    bad = setup["snapshot_of"](BUGGY)
    assert service.suite_can_fail(full, bad) is True
    assert service.suite_can_fail(weak, bad) is False  # never exercises the truncation bug


def test_unavailable_backend_records_not_run(setup, controller) -> None:  # noqa: ANN001
    service = AssessmentService(controller, PodmanBackend(), setup["service"].work_root)
    outcome = service.assess_black_box(setup["criterion_id"], setup["snapshot_of"](CORRECT))
    assert outcome["result"] == "not_run"
    assessment = controller.store.assessment(outcome["assessment_id"])
    assert "unavailable" in assessment.limitations[0]
    assert assessment.evidence_refs == []


# ------------------------------------------------------- comparison (pure) --


def _observation(**fields: object) -> ExecutionObservation:
    base = dict(backend_id="test", exit_code=0, signal=None, terminated_by="exited", wall_ms=5,
                stdout=b"[]", stderr=b"", stdout_total_bytes=2, stderr_total_bytes=0,
                stdout_truncated=False, stderr_truncated=False, processes_remaining=0, output_dir="/x")
    return ExecutionObservation(**{**base, **fields})


def _case(**expect: object):  # noqa: ANN201
    return EvaluatorSuite.model_validate(
        {**suite_document(), "cases": [{"case_id": "c", "description": "d", "expect": expect}]}
    ).cases[0]


def test_tool_error_failed_criterion_and_pass_are_distinct() -> None:
    case = _case(exit_code=0, stdout_json=[])
    assert compare(case, _observation()).outcome == "pass"
    assert compare(case, _observation(stdout=b"[1]")).outcome == "fail"
    assert compare(case, _observation(terminated_by="launch_failed", exit_code=None)).outcome == "error"
    assert compare(case, _observation(terminated_by="cancelled", exit_code=None)).outcome == "error"
    assert compare(case, _observation(terminated_by="timeout", exit_code=None)).outcome == "fail"
    assert compare(case, _observation(exit_code=None, signal=11)).outcome == "fail"


def test_first_difference_locates_without_values() -> None:
    assert first_difference({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == "$.a[1].b"
    assert first_difference([1, 2], [1]) == "$ (length)"
    assert first_difference({"a": 1}, {"a": 1}) is None
    assert first_difference(1, True) == "$"  # booleans are not numbers


def test_suite_rejects_unknown_placeholders_and_escaping_files() -> None:
    doc = suite_document()
    with pytest.raises(ValueError):
        EvaluatorSuite.model_validate({**doc, "entrypoint": ["{python}", "{env.HOME}"]})
    bad_case = {**doc["cases"][0], "files": {"../escape": ""}}
    with pytest.raises(ValueError):
        EvaluatorSuite.model_validate({**doc, "cases": [bad_case]})


# ---------------------------------------------------------- trust rules --


def test_models_and_clients_cannot_record_measurements(setup, controller, human, model_actor) -> None:  # noqa: ANN001
    forged = {
        "criterion_ref": {"id": setup["criterion_id"], "revision": 1},
        "snapshot_id": None,
        "assessment_result": "not_applicable",
        "assessor_ref": "me",
    }
    for actor in (model_actor, human):
        with pytest.raises(ForgeError) as caught:
            controller.apply_command(
                command("assessment.record", forged, project_id=setup["project_id"]), actor
            )
        assert caught.value.code == ErrorCode.PERMISSION_DENIED


def test_models_cannot_admit_their_own_tests(setup, controller, model_actor) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError) as caught:
        controller.apply_command(
            command("suite.admit", {"suite": suite_document()}, project_id=setup["project_id"]), model_actor
        )
    assert caught.value.code == ErrorCode.PERMISSION_DENIED


def test_pass_requires_evidence(setup, controller) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError, match="must cite evidence"):
        controller.apply_command(
            command(
                "assessment.record",
                {"criterion_ref": {"id": setup["criterion_id"], "revision": 1}, "snapshot_id": None,
                 "assessment_result": "pass", "assessor_ref": "x"},
                project_id=setup["project_id"],
            ),
            controller.system_actor,
        )


def test_criterion_must_pin_an_admitted_suite(setup, controller, human) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError, match="suite_digest does not match"):
        controller.apply_command(
            command(
                "criterion.create",
                {"subject": "s", "assessment_method": "black_box_cases",
                 "parameters": {"suite_artifact_id": setup["suite"]["suite_artifact_id"],
                                "suite_digest": "sha256:" + "0" * 64}},
                project_id=setup["project_id"],
            ),
            human,
        )


def test_review_evidence_has_no_invented_job(setup, controller, human) -> None:  # noqa: ANN001
    receipt = controller.apply_command(
        command(
            "evidence.record_review",
            {"subject_ref": {"id": setup["criterion_id"], "revision": 1},
             "observation": {"checklist": "boundary cases cover truncation", "result": "ok"}},
            project_id=setup["project_id"],
        ),
        human,
    )
    evidence = controller.store.evidence(receipt.result["evidence_id"])
    assert evidence.origin == "review" and evidence.reviewer_id == human.actor_id
    assert evidence.job_id is None and evidence.snapshot_id is None
    # The database itself refuses review evidence that claims an execution job.
    with pytest.raises(sqlite3.IntegrityError):
        controller.store.conn.execute(
            "UPDATE evidence SET job_id = ? WHERE evidence_id = ?", (new_id("job"), evidence.evidence_id)
        )


# ------------------------------------------------------------- D02 rules --


def _finding(rule: str, text: str = "x") -> NormalizedFinding:
    dimension, severity = classify(rule)
    return NormalizedFinding(rule_id=rule, level="error", message="m", path="a.py", line=1, column=1,
                             dimension=dimension, severity=severity, fingerprint=fingerprint(rule, "a.py", text))


def test_rule_table_and_verdict() -> None:
    assert classify("S608") == ("security", "high")
    assert classify("S999") == ("security", "medium")
    assert classify("E999") == ("correctness", "critical")
    assert verdict([_finding("F401")]) == "approve"
    assert verdict([_finding("S307")]) == "needs_discussion"
    high = _finding("S307")
    assert verdict([high], confirmed={high.fingerprint}) == "request_changes"
    critical = _finding("E999")
    assert verdict([critical]) == "request_changes"
    assert verdict([critical], waived={critical.fingerprint}) == "approve"


def test_fingerprint_survives_moving_lines_but_not_new_text() -> None:
    assert fingerprint("S307", "a.py", "    eval(x)\n") == fingerprint("S307", "a.py", "eval(x)")
    assert fingerprint("S307", "a.py", "eval(x)") != fingerprint("S307", "a.py", "eval(y)")


@needs_sandbox
def test_ruff_runs_isolated_and_findings_are_recorded_d02(setup, controller, tmp_path) -> None:  # noqa: ANN001
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff is not installed")
    real = Path(os.path.realpath(ruff))
    registry = ToolRegistry()
    tool = registry.admit("ruff", real, version="host", toolchain_paths=[str(real.parent.parent)])
    source = setup["source"]
    (source / "pyproject.toml").write_text('[tool.ruff.lint]\nignore = ["S", "E9", "F"]\n')  # hostile config
    snapshot_id = setup["snapshot_of"]("import os\nresult = eval(input())\nunused = os\n")
    outcome = setup["service"].analyze(snapshot_id, registry.resolve("ruff"))
    assert outcome["status"] == "ok"
    rules = {item.rule_id for item in outcome["findings"]}
    assert "S307" in rules  # the project config tried to switch security rules off
    assert outcome["verdict"] == "needs_discussion"
    assert len(controller.store.findings(snapshot_id=snapshot_id)) == len(outcome["findings"])


@needs_sandbox
def test_analyzer_crash_is_an_error_not_a_clean_pass_d02(setup, tmp_path) -> None:  # noqa: ANN001
    fake = tmp_path / "fake-analyzer"
    fake.write_text("#!/bin/sh\necho 'not json'\n")
    fake.chmod(0o755)
    tool = ToolRegistry().admit("ruff", fake, version="fake", toolchain_paths=[str(tmp_path)])
    outcome = setup["service"].analyze(setup["snapshot_of"]("x = 1\n"), tool)
    assert outcome["status"] == "error"
    assert outcome["verdict"] is None


def test_changed_tool_binary_is_refused(tmp_path) -> None:  # noqa: ANN001
    fake = tmp_path / "tool"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    registry = ToolRegistry()
    registry.admit("tool", fake, version="1")
    fake.write_text("#!/bin/sh\necho swapped\n")
    with pytest.raises(ForgeError, match="changed since admission"):
        registry.resolve("tool")

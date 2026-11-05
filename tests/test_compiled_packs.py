"""COBOL (F19/F20), OCaml (F23) and the emulator tier (F22) on the shared compiled-language assessor.

Acceptance covered here: each pack builds and runs its bounded fixture; a
bounded COBOL-to-Java step matches approved behavior and a floating-point
port's differences are exposed; the OCaml fixture passes reference and
property checks; emulated results are recorded as emulator evidence and
never satisfy a hardware requirement.
"""

from __future__ import annotations

import base64
import random
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from forge.assessment.service import AssessmentService
from forge.assessment.suite import EvaluatorSuite
from forge.packs.cobol_layout import compare_records
from forge.packs.compiled import CompiledAssessor, admit_compiled_tools
from forge.packs.jvm import JavaAssessor, admit_java_tools
from forge.packs.native import malformed_variants, oracle_cases
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tools.registry import ToolRegistry
from tests.conftest import command
from tests.test_jvm_cobol import ACCOUNTS, parse_output, reference_output
from tests.test_jvm_cobol import suite_document as java_suite
from tests.test_native_pack import FULL_LENGTH_FIELDS, SEED_FULL, reference

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND = LinuxNamespaceBackend()


def needs(*tools: str):  # noqa: ANN201
    missing = [tool for tool in tools if shutil.which(tool) is None]
    return pytest.mark.skipif(not BACKEND.capabilities().available or bool(missing),
                              reason=str(BACKEND.capabilities().unavailable_reason or f"not installed: {missing}"))


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Legacy", "objective": "o", "source_roots": ["main"]}), human).project_id
    trust.approve_source_root(project_id, "main", source, human)
    registry = ToolRegistry()
    admit_compiled_tools(registry)
    service = AssessmentService(controller, BACKEND, tmp_path / "work")

    def send(method: str, params: dict) -> dict:
        return controller.apply_command(command(method, params, project_id=project_id), human).result

    def criterion(suite: dict, method: str = "compiled_cases", **parameters) -> str:  # noqa: ANN003
        admitted = send("suite.admit", {"suite": suite})
        return send("criterion.create", {"subject": suite["description"], "assessment_method": method,
                                         "parameters": {"suite_artifact_id": admitted["suite_artifact_id"],
                                                        "suite_digest": admitted["suite_digest"], **parameters}}
                    )["criterion_id"]

    def snapshot(files: dict[str, str]) -> str:
        for child in source.iterdir():
            child.unlink()
        for name, text in files.items():
            (source / name).write_text(text)
        return send("workspace.capture", {"source_root": "main"})["snapshot_id"]

    return {"criterion": criterion, "snapshot": snapshot, "registry": registry, "service": service,
            "assessor": CompiledAssessor(service, registry), "store": controller.store}


def fixed_width(key: str, amount: str, rate: str) -> str:
    value, factor = Decimal(amount), Decimal(rate)
    digits = f"{abs(value).scaleb(3).to_integral_value():012.0f}"
    return f"{key:<6}{'-' if value < 0 else '+'}{digits}{factor.scaleb(4).to_integral_value():05.0f}\n"


def cobol_suite() -> dict:
    data = "".join(fixed_width(*row) for row in ACCOUNTS).encode()
    return EvaluatorSuite(suite_id="cobol_interest", revision=1, description="COBOL interest job",
                          entrypoint=["/input/program"], case_wall_ms=20_000, cases=[
                              {"case_id": "accounts", "description": "five accounts", "args": ["{case}/accounts.dat"],
                               "files": {"accounts.dat": base64.b64encode(data).decode()},
                               "expect": {"exit_code": 0, "stdout_text": reference_output()}}]).model_dump(mode="json")


# ------------------------------------------------------------------- COBOL


@needs("cobc")
def test_cobol_job_builds_and_matches_the_approved_reference(rig) -> None:
    criterion = rig["criterion"](cobol_suite(), build={"toolchain": "cobol", "sources": ["INTEREST.cbl"]})
    snapshot = rig["snapshot"]({"INTEREST.cbl": (FIXTURES / "cobol_interest" / "INTEREST.cbl").read_text()})
    outcome = rig["assessor"].assess(criterion, snapshot)
    assert outcome["result"] == "pass", [c.reasons for c in outcome["run"].cases] if "run" in outcome else outcome
    assert outcome["tier"] == "host"


@needs("cobc", "javac")
def test_cobol_to_java_step_matches_and_a_float_port_is_exposed(rig) -> None:
    """F20: the live COBOL job is the approved behavior; the Java ports are compared record by record."""
    outputs: dict[str, str] = {}
    cobol = rig["criterion"](cobol_suite(), build={"toolchain": "cobol", "sources": ["INTEREST.cbl"]})
    rig["assessor"].assess(cobol, rig["snapshot"]({"INTEREST.cbl": (FIXTURES / "cobol_interest" / "INTEREST.cbl")
                                                   .read_text()}),
                           on_case=lambda case, obs: outputs.__setitem__("cobol", obs.stdout.decode()))
    admit_java_tools(rig["registry"])
    java = JavaAssessor(rig["service"], rig["registry"])
    java_criterion = rig["criterion"](java_suite(), method="java_cases",
                                      build={"sources": ["Interest.java"], "release": 17})
    for variant in ("decimal", "double"):
        snapshot = rig["snapshot"]({"Interest.java": (FIXTURES / "java_interest" / f"Interest_{variant}.java").read_text()})
        java.assess(java_criterion, snapshot,
                    on_case=lambda case, obs, v=variant: outputs.__setitem__(v, obs.stdout.decode())
                    if case == "accounts" else None)
    legacy = parse_output(outputs["cobol"])
    assert compare_records(legacy, parse_output(outputs["decimal"]), key="id")["equivalent"]
    report = compare_records(legacy, parse_output(outputs["double"]), key="id")
    assert {d.key for d in report["unaccepted"]} >= {"A2", "A4"}
    assert all(d.field == "interest" for d in report["unaccepted"])


# ------------------------------------------------------------------- OCaml


def rle_encode(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        j = i
        while j < len(text) and text[j] == text[i]:
            j += 1
        out.append(f"{j - i}{text[i]}")
        i = j
    return "".join(out)


def ocaml_suite() -> dict:
    generator = random.Random(7)
    samples = ["", "a", "aaab  c", *("".join(generator.choice("ab .") for _ in range(generator.randint(1, 40)))
                                     for _ in range(12))]
    cases = []
    for index, text in enumerate(samples):
        encoded = rle_encode(text)
        stdin = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
        cases.append({"case_id": f"encode_{index}", "description": "matches the reference encoder",
                      "args": ["encode"], "stdin_b64": stdin(text), "expect": {"exit_code": 0, "stdout_text": encoded}})
        cases.append({"case_id": f"roundtrip_{index}", "description": "decode(encode(x)) == x",
                      "args": ["decode"], "stdin_b64": stdin(encoded), "expect": {"exit_code": 0, "stdout_text": text}})
    for index, bad in enumerate(["a", "0a", "3", "12", "99999999999999999999a", "2a3"]):
        cases.append({"case_id": f"malformed_{index}", "description": f"rejects {bad!r}", "args": ["decode"],
                      "stdin_b64": base64.b64encode(bad.encode()).decode(),
                      "expect": {"exit_code": 2, "stderr_contains": "malformed: "}})
    cases.append({"case_id": "digits", "description": "digits cannot be encoded", "args": ["encode"],
                  "stdin_b64": base64.b64encode(b"a1").decode(), "expect": {"exit_code": 2}})
    cases.append({"case_id": "usage", "description": "usage", "expect": {"exit_code": 64}})
    return EvaluatorSuite(suite_id="ocaml_rle", revision=1, description="run-length encoding",
                          entrypoint=["/input/program"], cases=cases).model_dump(mode="json")


@needs("ocamlfind")
def test_ocaml_fixture_passes_reference_and_property_checks_and_a_bug_does_not(rig) -> None:
    criterion = rig["criterion"](ocaml_suite(), build={"toolchain": "ocaml", "sources": ["rle.ml"]})
    good = (FIXTURES / "ocaml_rle" / "rle.ml").read_text()
    outcome = rig["assessor"].assess(criterion, rig["snapshot"]({"rle.ml": good}))
    assert outcome["result"] == "pass", [(c.case_id, c.reasons) for c in outcome["run"].cases if c.outcome != "pass"]
    assert outcome["run"].counts["pass"] == len(outcome["run"].cases) > 30
    off_by_one = good.replace("String.make count s.[!i]", "String.make (count - 1) s.[!i]")
    broken = rig["assessor"].assess(criterion, rig["snapshot"]({"rle.ml": off_by_one}))
    assert broken["result"] == "fail"
    failing = {c.case_id for c in broken["run"].cases if c.outcome == "fail"}
    assert failing and all(case.startswith("roundtrip_") for case in failing)   # the property catches it
    syntax = rig["assessor"].assess(criterion, rig["snapshot"]({"rle.ml": good + "\nlet broken = (\n"}))
    assert syntax["result"] == "fail" and syntax["outcome"] == "compile_failed"


# ---------------------------------------------------------------- emulator


def packet_suite() -> dict:
    cases = oracle_cases("valid", [("full", SEED_FULL)], reference, file_name="packet.bin")
    cases += oracle_cases("full", malformed_variants(SEED_FULL, FULL_LENGTH_FIELDS)[:25], reference,
                          file_name="packet.bin")
    return EvaluatorSuite(suite_id="packet_arm", revision=1, description="packet parser on ARM",
                          entrypoint=["/input/program"], cases=cases, case_wall_ms=20_000).model_dump(mode="json")


def packet_sources() -> dict[str, str]:
    base = FIXTURES / "c_packet"
    return {"main.c": (base / "main.c").read_text(), "parser.c": (base / "parser_increment.c").read_text(),
            "tags.c": (base / "tags_fixed.c").read_text(), "parser.h": (base / "parser.h").read_text(),
            "tags.h": (base / "tags.h").read_text()}


@needs("arm-linux-gnueabihf-gcc")
def test_emulated_results_are_emulator_evidence_and_never_hardware(rig) -> None:
    if shutil.which("qemu-arm-static") is None and shutil.which("qemu-arm") is None:
        pytest.skip("no QEMU ARM user-mode emulator installed")
    build = {"toolchain": "c_arm", "sources": ["main.c", "parser.c", "tags.c"]}
    emulated = rig["criterion"](packet_suite(), build=build)
    snapshot = rig["snapshot"](packet_sources())
    outcome = rig["assessor"].assess(emulated, snapshot)
    assert outcome["result"] == "pass", [(c.case_id, c.reasons) for c in outcome["run"].cases if c.outcome != "pass"][:3]
    assert outcome["tier"] == "emulator"
    assessment = rig["store"].assessment(outcome["assessment_id"])
    assert assessment.summary["tier"] == "emulator"
    assert any("emulation" in item for item in assessment.limitations)
    on_board = rig["criterion"](packet_suite(), build=build, required_tier="hardware")
    refused = rig["assessor"].assess(on_board, snapshot)
    assert refused["result"] == "not_run" and "requires hardware evidence" in refused["reason"]


def test_a_compiled_suite_may_only_run_the_built_program(rig) -> None:
    from forge.contracts.errors import ForgeError

    other = EvaluatorSuite(suite_id="runs_python", revision=1, description="runs something else",
                           entrypoint=["{python}", "-c", "print(1)"],
                           cases=[{"case_id": "c", "description": "d", "expect": {"exit_code": 0}}]).model_dump(mode="json")
    criterion = rig["criterion"](other, build={"toolchain": "ocaml", "sources": ["rle.ml"]})
    with pytest.raises(ForgeError, match="runs /input/program"):
        rig["assessor"].assess(criterion, rig["snapshot"]({"rle.ml": "let () = ()\n"}))

"""Ticket F19: Java and COBOL/JCL minimum packs.

Acceptance: each pack can build/run its bounded fixture (COBOL: reported
unavailable, its layout tooling works without a compiler); comparison
exposes an intentional decimal or encoding mismatch.
"""

from __future__ import annotations

import base64
import math
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from forge.assessment.service import AssessmentService
from forge.assessment.suite import EvaluatorSuite
from forge.contracts.errors import ForgeError
from forge.packs.cobol_layout import (
    compare_records,
    cobol_round,
    decode_file,
    decode_record,
    encode_packed,
    parse_copybook,
)
from forge.packs.jvm import JavaAssessor, admit_java_tools, pack_status
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tools.registry import ToolRegistry
from tests.conftest import command

FIXTURE = Path(__file__).parent / "fixtures" / "java_interest"
BACKEND = LinuxNamespaceBackend()
needs_java = pytest.mark.skipif(
    not BACKEND.capabilities().available or shutil.which("javac") is None,
    reason=str(BACKEND.capabilities().unavailable_reason or "no JDK installed"))

ACCOUNTS = [("A1", "1000.00", "0.0525"), ("A2", "1.005", "1"), ("A3", "2.675", "1"), ("A4", "-10.125", "1"),
            ("A5", "250.40", "0.031")]


def reference_output() -> str:
    """The approved behavior: exact decimal arithmetic with COBOL ROUNDED."""
    return "".join(f"{key},{cobol_round(Decimal(amount) * Decimal(rate), 2)}\n" for key, amount, rate in ACCOUNTS)


def parse_output(text: str) -> list[dict]:
    return [{"id": key, "interest": Decimal(value)} for key, value in
            (line.split(",") for line in text.splitlines() if line)]


# ------------------------------------------------------------------ COBOL


COPYBOOK = """
      * Customer master record
       01  CUSTOMER-REC.
           05  CUST-ID            PIC X(6).
           05  CUST-NAME          PIC X(12).
           05  BALANCE            PIC S9(7)V99 COMP-3.
           05  RATE               PIC 9V9999.
           05  OVERDUE-DAYS       PIC S9(3).
"""


def zoned(text: str, negative: bool = False) -> bytes:
    data = bytearray(text.encode("cp037"))
    if negative:
        data[-1] = 0xD0 | (data[-1] & 0x0F)
    return bytes(data)


def customer(cust_id: str, name: str, balance: str, rate: str, overdue: int) -> bytes:
    return (cust_id.encode("cp037") + name.ljust(12).encode("cp037") + encode_packed(Decimal(balance), 9, 2)
            + zoned(rate) + zoned(f"{abs(overdue):03d}", overdue < 0))


def test_copybook_layout_and_exact_decoding() -> None:
    layout = parse_copybook(COPYBOOK)
    assert layout.name == "CUSTOMER-REC" and layout.record_length == 6 + 12 + 5 + 5 + 3
    assert [(f.name, f.kind, f.length) for f in layout.fields] == [
        ("CUST-ID", "text", 6), ("CUST-NAME", "text", 12), ("BALANCE", "packed", 5), ("RATE", "zoned", 5),
        ("OVERDUE-DAYS", "zoned", 3)]
    record = decode_record(customer("C00001", "ACME [EU]!", "-1234.56", "05250", -12), layout)
    assert record == {"CUST-ID": "C00001", "CUST-NAME": "ACME [EU]!", "BALANCE": Decimal("-1234.56"),
                      "RATE": Decimal("0.5250"), "OVERDUE-DAYS": Decimal("-12")}
    assert isinstance(record["BALANCE"], Decimal)


def test_malformed_and_unsupported_layouts_are_refused() -> None:
    layout = parse_copybook(COPYBOOK)
    with pytest.raises(ValueError, match="needs"):
        decode_record(b"\x00" * 5, layout)
    bad_packed = bytearray(customer("C00001", "X", "1.00", "00000", 0))
    bad_packed[18 + 4] = 0x1A                         # sign nibble A is not a sign
    with pytest.raises(ValueError, match="packed"):
        decode_record(bytes(bad_packed), layout)
    for feature in ("05 ITEMS PIC X(4) OCCURS 3 TIMES.", "05 ALT REDEFINES CUST-ID PIC 9(6).",
                    "05 COUNT PIC 9(4) COMP."):
        with pytest.raises(ForgeError, match="unsupported"):
            parse_copybook("01 R.\n" + feature)
    with pytest.raises(ValueError):
        decode_file(b"\x00" * 7, layout)


def test_cobol_rounding_differs_from_bankers_rounding() -> None:
    assert cobol_round(Decimal("2.5"), 0) == 3 and round(Decimal("2.5")) == 2
    assert cobol_round(Decimal("-1.005"), 2) == Decimal("-1.01")
    assert cobol_round(Decimal("1.004"), 2) == Decimal("1.00")


def test_comparison_exposes_every_seeded_migration_defect() -> None:
    layout = parse_copybook(COPYBOOK)
    source = [customer("C00001", "ACME [EU]!", "100.00", "05250", 0),
              customer("C00002", "BETA|CO", "200.005", "01000", 3),
              customer("C00003", "GAMMA", "300.00", "00500", -1)]
    reference = decode_file(b"".join(source), layout)
    migrated = decode_file(b"".join(source), layout, encoding="cp500")   # the wrong EBCDIC table
    migrated = [dict(r) for r in migrated[:2]]                            # a dropped record
    migrated[1]["BALANCE"] = migrated[1]["BALANCE"] + Decimal("0.01")     # a one-cent rounding change
    del migrated[1]["RATE"]                                               # a field the target lost
    report = compare_records(reference, migrated, key="CUST-ID")
    assert not report["equivalent"]
    assert report["missing_records"] == ["C00003"]
    found = {(d.key, d.field) for d in report["differences"]}
    assert ("C00001", "CUST-NAME") in found                              # [ ] ! differ between cp037 and cp500
    assert ("C00002", "BALANCE") in found and ("C00002", "RATE") in found
    # An accepted difference is still reported, as accepted, and needs a reason.
    accepted = compare_records(reference[:1], migrated[:1], key="CUST-ID",
                               accepted={("C00001", "CUST-NAME"): "target system has no brackets; approved"})
    assert accepted["equivalent"] and accepted["differences"][0].accepted
    unused = compare_records(reference[:1], reference[:1], key="CUST-ID", accepted={("C00001", "RATE"): "old"})
    assert unused["unused_acceptances"] == ["C00001:RATE"]


def test_pack_status_is_measured_not_assumed() -> None:
    rows = {row["pack_id"]: row for row in pack_status()}
    assert rows["cobol"]["available"] is (shutil.which("cobc") is not None)
    assert rows["powershell"]["available"] is (shutil.which("pwsh") is not None)
    if not rows["cobol"]["available"]:
        assert rows["cobol"]["reason"] == "not installed: cobc"


# ------------------------------------------------------------------- Java


def suite_document() -> dict:
    data = "".join(f"{key},{amount},{rate}\n" for key, amount, rate in ACCOUNTS)
    cases = [{"case_id": "accounts", "description": "interest for five accounts, including exact halves",
              "args": ["{case}/accounts.csv"], "files": {"accounts.csv": base64.b64encode(data.encode()).decode()},
              "expect": {"exit_code": 0, "stdout_text": reference_output()}},
             {"case_id": "usage", "description": "missing argument", "expect": {"exit_code": 64}}]
    return EvaluatorSuite(suite_id="interest", revision=1, description="interest calculation",
                          entrypoint=["{java}", "-cp", "/input/classes", "Interest"], cases=cases,
                          case_wall_ms=30_000).model_dump(mode="json")


@pytest.fixture
def java_rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Interest", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)
    suite = controller.apply_command(command("suite.admit", {"suite": suite_document()}, project_id=project_id),
                                     human).result
    criterion = controller.apply_command(command("criterion.create", {
        "subject": "Interest matches the approved COBOL behavior", "assessment_method": "java_cases",
        "parameters": {"suite_artifact_id": suite["suite_artifact_id"], "suite_digest": suite["suite_digest"],
                       "build": {"sources": ["Interest.java"], "release": 17}}}, project_id=project_id),
        human).result["criterion_id"]

    def snapshot(variant: str) -> str:
        shutil.copy(FIXTURE / f"Interest_{variant}.java", source / "Interest.java")
        return controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]

    registry = ToolRegistry()
    assert admit_java_tools(registry) == ["javac", "java"]
    return {"criterion": criterion, "snapshot": snapshot, "registry": registry, "store": controller.store,
            "assessor": JavaAssessor(AssessmentService(controller, BACKEND, tmp_path / "work"), registry)}


@needs_java
def test_java_decimal_port_builds_runs_and_matches(java_rig) -> None:
    outcome = java_rig["assessor"].assess(java_rig["criterion"], java_rig["snapshot"]("decimal"))
    assert outcome["result"] == "pass", [c.reasons for c in outcome["run"].cases]
    assert "/etc/java" in " ".join(java_rig["registry"].resolve("java").toolchain_paths) or \
        not Path("/etc").joinpath("java-21-openjdk").exists()


@needs_java
def test_floating_point_port_is_exposed_record_by_record(java_rig) -> None:
    captured = {}
    outcome = java_rig["assessor"].assess(
        java_rig["criterion"], java_rig["snapshot"]("double"),
        on_case=lambda case_id, observation: captured.__setitem__(case_id, observation.stdout.decode()))
    assert outcome["result"] == "fail"
    report = compare_records(parse_output(reference_output()), parse_output(captured["accounts"]), key="id")
    wrong = {(d.key, str(d.reference), str(d.candidate)) for d in report["unaccepted"]}
    # Expected from the same IEEE-754 doubles Java uses: Math.round(x) is floor(x + 0.5).
    doubles = {key: Decimal(f"{math.floor(float(amount) * float(rate) * 100 + 0.5) / 100:.2f}")
               for key, amount, rate in ACCOUNTS}
    exact = {row["id"]: row["interest"] for row in parse_output(reference_output())}
    expected = {(key, str(exact[key]), str(doubles[key])) for key in exact if exact[key] != doubles[key]}
    assert wrong == expected and {"A2", "A4"} <= {key for key, _, _ in wrong}


@needs_java
def test_a_java_compile_error_fails_with_diagnostics(java_rig, tmp_path) -> None:
    broken = (FIXTURE / "Interest_decimal.java").read_text().replace("out.append(field[0])", "out.append(fieldd[0])")
    variant = FIXTURE.parent / "java_interest" / "Interest_broken.java"
    variant.write_text(broken)
    try:
        outcome = java_rig["assessor"].assess(java_rig["criterion"], java_rig["snapshot"]("broken"))
    finally:
        variant.unlink()
    assert outcome["result"] == "fail"
    assert any(d["path"] == "Interest.java" and "cannot find symbol" in d["message"] for d in outcome["diagnostics"])

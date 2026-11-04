"""Ticket F16: C/C++ pack.

Acceptance: a packet parser increment has an exact build context,
malformed-input tests and recorded sanitizer results.
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest

from forge.assessment.service import AssessmentService
from forge.assessment.suite import BlackBoxCase, EvaluatorSuite, ExpectedResult
from forge.contracts.errors import ForgeError
from forge.packs.native import (
    NativeAssessor,
    NativeBuildSpec,
    admit_native_tools,
    instrumentation_problems,
    malformed_variants,
    oracle_cases,
    parse_diagnostics,
    parse_sanitizer_output,
)
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tools.registry import ToolRegistry
from tests.conftest import command

FIXTURE = Path(__file__).parent / "fixtures" / "c_packet"
BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available or shutil.which("gcc") is None,
    reason=str(BACKEND.capabilities().unavailable_reason or "gcc is not installed"),
)


# ------------------------------------------------- the approved reference oracle


def reference(data: bytes) -> ExpectedResult:
    """The approved behavior of the packet format, written independently of the C code."""
    def malformed(problem: str) -> ExpectedResult:
        return ExpectedResult(exit_code=2, stderr_contains=f"malformed: {problem}\n")

    if len(data) < 4:
        return malformed("header is truncated")
    if data[:2] != b"FP":
        return malformed("bad magic")
    if data[2] != 1:
        return malformed("unsupported version")
    packet = {"id": None, "name": None, "tags": []}
    offset = 4
    for _ in range(data[3]):
        if len(data) - offset < 3:
            return malformed("record header is truncated")
        kind, length = data[offset], int.from_bytes(data[offset + 1:offset + 3], "big")
        offset += 3
        if length > len(data) - offset:
            return malformed("record runs past the packet")
        value = data[offset:offset + length]
        if kind == 1:
            if length != 4:
                return malformed("id record must be 4 bytes")
            packet["id"] = int.from_bytes(value, "big")
        elif kind == 2:
            if length > 64:
                return malformed("name is too long")
            if any(b < 0x20 or b > 0x7E for b in value):
                return malformed("name is not printable")
            packet["name"] = value.decode()
        elif kind == 3:
            if length < 1:
                return malformed("tags record is empty")
            count, position, tags = value[0], 1, []
            if count > 8:
                return malformed("too many tags")
            for _ in range(count):
                if position >= length:
                    return malformed("tag length is missing")
                tag_length = value[position]
                position += 1
                if tag_length > 32:
                    return malformed("tag is too long")
                if tag_length > length - position:
                    return malformed("tag runs past its record")
                tag = value[position:position + tag_length]
                if any(b < 0x20 or b > 0x7E for b in tag):
                    return malformed("tag is not printable")
                tags.append(tag.decode())
                position += tag_length
            if position != length:
                return malformed("tags record has trailing bytes")
            packet["tags"] = tags
        else:
            return malformed("unknown record type")
        offset += length
    if offset != len(data):
        return malformed("trailing bytes after the last record")
    return ExpectedResult(exit_code=0, stdout_json=packet)


def record(kind: int, value: bytes) -> bytes:
    return bytes([kind]) + len(value).to_bytes(2, "big") + value


def tags_value(*tags: bytes) -> bytes:
    return bytes([len(tags)]) + b"".join(bytes([len(tag)]) + tag for tag in tags)


SEED_FULL = b"FP\x01\x03" + record(1, b"\x00\x00\x01\x00") + record(2, b"abc") + record(3, tags_value(b"hi", b"yo"))
# Length fields of SEED_FULL: three record lengths, the tag count and the two tag lengths.
FULL_LENGTH_FIELDS = [(5, 2), (12, 2), (18, 2), (20, 1), (21, 1), (24, 1)]
SEED_TAGS_LAST = b"FP\x01\x01" + record(3, tags_value(b"edge"))


def suite_document(entry: str = "/input/program") -> dict:
    cases = oracle_cases("valid", [("full", SEED_FULL), ("tags-last", SEED_TAGS_LAST),
                                   ("id-only", b"FP\x01\x01" + record(1, b"\xff\xff\xff\xff"))],
                         reference, file_name="packet.bin")
    cases += oracle_cases("full", malformed_variants(SEED_FULL, FULL_LENGTH_FIELDS), reference, file_name="packet.bin")
    cases += oracle_cases("last", malformed_variants(SEED_TAGS_LAST, [(5, 2), (7, 1), (8, 1)]), reference,
                          file_name="packet.bin")
    cases.append(BlackBoxCase(case_id="no_args", description="usage error", expect=ExpectedResult(exit_code=64)))
    return EvaluatorSuite(suite_id="packet_parser_tags", revision=1, description="Packet parser with tags",
                          entrypoint=[entry], cases=cases, case_wall_ms=20_000).model_dump(mode="json")


# --------------------------------------------------------------- unit checks


def test_reference_oracle_agrees_with_the_examples() -> None:
    assert reference(SEED_FULL).stdout_json == {"id": 256, "name": "abc", "tags": ["hi", "yo"]}
    assert reference(SEED_FULL[:-1]).stderr_contains == "malformed: record runs past the packet\n"
    assert reference(b"FP\x01\x01" + record(3, b"\x01\x05ab")).stderr_contains == "malformed: tag runs past its record\n"


def test_malformed_variants_are_deterministic_unique_and_exclude_the_seed() -> None:
    first = malformed_variants(SEED_FULL, FULL_LENGTH_FIELDS)
    assert first == malformed_variants(SEED_FULL, FULL_LENGTH_FIELDS)
    data = [item for _, item in first]
    assert SEED_FULL not in data and len(data) == len(set(data))
    names = {name for name, _ in first}
    assert {"empty", "truncated_1", f"truncated_{len(SEED_FULL) - 1}", "trailing_byte", "len20_max"} <= names
    # Every generated input is malformed according to the approved oracle, except
    # mutations the oracle says are still valid; nothing is assumed.
    verdicts = {name: reference(item).exit_code for name, item in first}
    assert all(code in (0, 2) for code in verdicts.values())
    with pytest.raises(ValueError):
        malformed_variants(b"FP", [(1, 2)])


def test_build_spec_rejects_option_injection_and_odd_files() -> None:
    base = {"language": "c", "compiler": "gcc", "standard": "c11", "sources": ["main.c"]}
    for bad in ({"sources": ["-fplugin=evil.so"]}, {"sources": ["../main.c"]}, {"include_dirs": ["-I/etc"]},
                {"defines": ["X;rm -rf /"]}, {"defines": ["A=$(id)"]}, {"sanitizers": ["thread"]}):
        with pytest.raises(ValueError):
            NativeBuildSpec.model_validate({**base, **bad})
    with pytest.raises(ForgeError):
        NativeBuildSpec.model_validate({**base, "sources": ["prebuilt.o"]}).check_sources()
    args = NativeBuildSpec.model_validate({**base, "defines": ["NDEBUG", "LEVEL=2"]}).arguments()
    assert args[-2:] == ["-o", "/output/program"] and "-Werror" in args
    assert "-fsanitize=address,undefined" in args and "-fno-sanitize-recover=all" in args


GCC_OUTPUT = """In file included from parser.c:2:
tags.c: In function 'parse_tags':
tags.c:12:9: warning: unused variable 'spare' [-Wunused-variable]
tags.c:13:9: error: unused variable 'other' [-Werror=unused-variable]
parser.c:40:5: error: 'missing' undeclared (first use in this function)
main.c:3:10: fatal error: nothere.h: No such file or directory
compilation terminated.
"""


def test_compiler_diagnostics_are_structured() -> None:
    found = parse_diagnostics(GCC_OUTPUT)
    assert [(d.path, d.line, d.severity, d.flag) for d in found] == [
        ("tags.c", 12, "warning", "-Wunused-variable"), ("tags.c", 13, "error", "-Wunused-variable"),
        ("parser.c", 40, "error", None), ("main.c", 3, "error", None)]


ASAN_OUTPUT = """=================================================================
==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x50200000001b at pc 0x7f bp 0x7ffc sp 0x7ffc
READ of size 32 at 0x50200000001b thread T0
    #0 0x7f722b6fb42d in memcpy ../../../../src/libsanitizer/sanitizer_common/sanitizer_common_interceptors_memintrinsics.inc:115
    #1 0x561a02861d03 in parse_tags /input/tags.c:17
    #2 0x561a028619be in parse_packet /input/parser.c:45
SUMMARY: AddressSanitizer: heap-buffer-overflow /input/tags.c:17 in parse_tags
"""
UBSAN_OUTPUT = "parser.c:7:22: runtime error: left shift of 128 by 24 places cannot be represented in type 'int'\n"


def test_sanitizer_reports_are_structured() -> None:
    parsed = parse_sanitizer_output(ASAN_OUTPUT)
    assert parsed.runtime_failure is None
    [report] = parsed.reports
    assert (report.sanitizer, report.kind, report.path, report.line, report.function) == (
        "address", "heap-buffer-overflow", "tags.c", 17, "parse_tags")
    [ub] = parse_sanitizer_output(UBSAN_OUTPUT).reports
    assert (ub.sanitizer, ub.kind, ub.path, ub.line) == ("undefined", "shift", "parser.c", 7)
    leak = parse_sanitizer_output("==9==ERROR: LeakSanitizer: detected memory leaks\n\nDirect leak of 8 byte(s)\n"
                                  "    #0 0x1 in malloc ../../src/libsanitizer/asan/asan_malloc_linux.cpp:69\n"
                                  "    #1 0x2 in main /input/main.c:12\n")
    assert [(r.kind, r.path, r.line) for r in leak.reports] == [("memory-leak", "main.c", 12)]
    failed = parse_sanitizer_output("==1==LeakSanitizer has encountered a fatal error.\n")
    assert failed.reports == [] and failed.runtime_failure
    assert parse_sanitizer_output("malformed: tag runs past its record\n").reports == []


def test_instrumentation_bypass_is_detected_in_project_code_only() -> None:
    clean = '# 1 "/usr/include/stdio.h"\nextern int f(void) __asm__ ("g");\n# 1 "main.c"\nint main(void){return 0;}\n'
    assert instrumentation_problems(clean, clean) == []
    attribute = clean + '__attribute__((__no_sanitize__("address"))) int peek(char *p){return p[9];}\n'
    assert any("no_sanitize" in p for p in instrumentation_problems(attribute, attribute))
    options = clean + 'const char *__asan_default_options(void){return "replace_intrin=0";}\n'
    assert any("__asan_default_options" in p for p in instrumentation_problems(options, options))
    assembly = clean + 'int r(int *p){int v; asm("mov (%1), %0" : "=r"(v) : "r"(p)); return v;}\n'
    assert any("'asm'" in p for p in instrumentation_problems(assembly, assembly))
    aware = clean + "int safe = 1;\n"
    assert any("differently" in p for p in instrumentation_problems(aware, clean))


# --------------------------------------------------------- end to end (sandbox)


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Packet parser", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)
    admitted = controller.apply_command(command("suite.admit", {"suite": suite_document()}, project_id=project_id),
                                        human).result
    registry = ToolRegistry()
    assert "gcc" in admit_native_tools(registry)

    def criterion(suite: dict | None = None, **build) -> str:  # noqa: ANN003
        chosen = admitted if suite is None else controller.apply_command(
            command("suite.admit", {"suite": suite}, project_id=project_id), human).result
        spec = {"language": "c", "compiler": "gcc", "standard": "c11",
                "sources": ["main.c", "parser.c", "tags.c"], **build}
        return controller.apply_command(command("criterion.create", {
            "subject": "The parser handles valid and malformed packets",
            "assessment_method": "native_cases",
            "parameters": {"suite_artifact_id": chosen["suite_artifact_id"],
                           "suite_digest": chosen["suite_digest"], "build": spec}}, project_id=project_id),
            human).result["criterion_id"]

    def snapshot(files: dict[str, str]) -> str:
        for existing in source.iterdir():
            existing.unlink()
        for name, text in files.items():
            (source / name).write_text(text)
        return controller.apply_command(command("workspace.capture", {"source_root": "main"}, project_id=project_id),
                                        human).result["snapshot_id"]

    def fixture(*names: str) -> str:
        return (FIXTURE / names[0]).read_text()

    common = {"main.c": fixture("main.c"), "parser.h": fixture("parser.h"), "tags.h": fixture("tags.h"),
              "parser.c": fixture("parser_increment.c")}
    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    return {"criterion": criterion, "snapshot": snapshot, "common": common, "fixture": fixture,
            "assessor": NativeAssessor(service, registry), "controller": controller}


@needs_sandbox
def test_fixed_increment_passes_with_exact_context_and_clean_sanitizers(rig) -> None:
    criterion_id = rig["criterion"]()
    snapshot_id = rig["snapshot"]({**rig["common"], "tags.c": rig["fixture"]("tags_fixed.c")})
    outcome = rig["assessor"].assess(criterion_id, snapshot_id)
    assert outcome["result"] == "pass", [c.reasons for c in outcome["run"].cases if c.outcome != "pass"][:3]
    assert outcome["reports"] == [] and outcome["finding_ids"] == []
    run = outcome["run"]
    assert run.counts["pass"] == len(run.cases) > 60
    context = outcome["context"]
    assert context.compiler["digest"].startswith("sha256:") and context.compiler["target"]
    assert {"cc1", "as", "ld"} <= set(context.helpers)
    assert set(context.sources) == {"main.c", "parser.c", "tags.c", "parser.h", "tags.h"}
    assert "-fsanitize=address,undefined" in context.arguments
    commands = context.compile_commands()
    assert [entry["file"] for entry in commands] == ["main.c", "parser.c", "tags.c"]
    assert all("-o" not in entry["arguments"] and entry["arguments"][-2:] == ["-c", entry["file"]]
               for entry in commands)
    store = rig["controller"].store
    build_evidence = store.evidence(outcome["build_evidence_id"])
    assert build_evidence.observation["build_context_digest"] == context.digest()
    assert build_evidence.observation["instrumentation_problems"] == []
    assert store.assessment(outcome["assessment_id"]).assessment_result == "pass"


@needs_sandbox
def test_buggy_increment_passes_black_box_but_the_sanitizer_records_a_confirmed_finding(rig) -> None:
    snapshot_id = rig["snapshot"]({**rig["common"], "tags.c": rig["fixture"]("tags_buggy.c")})
    # Without sanitizers, every observable output is right: the bug is invisible.
    plain = rig["assessor"].assess(rig["criterion"](sanitizers=[]), snapshot_id)
    assert plain["result"] == "pass"
    assert any("no sanitizers" in item for item in
               rig["controller"].store.assessment(plain["assessment_id"]).limitations)
    sanitized = rig["assessor"].assess(rig["criterion"](), snapshot_id)
    assert sanitized["result"] == "fail"
    kinds = {(r.sanitizer, r.kind, r.path, r.function) for _, r in sanitized["reports"]}
    assert ("address", "heap-buffer-overflow", "tags.c", "parse_tags") in kinds
    finding = rig["controller"].store.finding(sanitized["finding_ids"][0])
    assert finding.confirmation == "confirmed" and finding.rule_id.startswith("address.")
    assert "reproduced by case" in finding.claim


@needs_sandbox
def test_a_sanitizer_report_fails_the_criterion_even_when_a_weak_suite_passes(rig) -> None:
    # A weak oracle: malformed input only has to exit nonzero. An ASan abort does that.
    weak = suite_document()
    weak["suite_id"], weak["cases"] = "weak_oracle", [
        {**case, "expect": {"exit_code": "nonzero"}} for case in weak["cases"] if case["expect"].get("exit_code") == 2]
    snapshot_id = rig["snapshot"]({**rig["common"], "tags.c": rig["fixture"]("tags_buggy.c")})
    outcome = rig["assessor"].assess(rig["criterion"](suite=weak), snapshot_id)
    assert outcome["run"].result == "pass"          # every case "passed"
    assert outcome["reports"] and outcome["result"] == "fail"


@needs_sandbox
def test_base_version_fails_the_increment_suite(rig) -> None:
    files = {**rig["common"], "parser.c": rig["fixture"]("parser_base.c"), "tags.c": rig["fixture"]("tags_fixed.c")}
    outcome = rig["assessor"].assess(rig["criterion"](), rig["snapshot"](files))
    assert outcome["result"] == "fail"
    failed = [c for c in outcome["run"].cases if c.outcome == "fail"]
    assert failed and all(c.case_id.startswith(("valid_", "full_", "last_")) for c in failed)


@needs_sandbox
def test_compile_errors_and_warnings_fail_with_structured_diagnostics(rig) -> None:
    broken = rig["fixture"]("tags_fixed.c").replace("size_t offset = 0;", "size_t offset = 0;\n    int spare;")
    outcome = rig["assessor"].assess(rig["criterion"](), rig["snapshot"]({**rig["common"], "tags.c": broken}))
    assert outcome["result"] == "fail"
    assert any(d.path == "tags.c" and d.flag == "-Wunused-variable" for d in outcome["diagnostics"])


@needs_sandbox
def test_candidate_cannot_switch_the_sanitizer_off_from_its_source(rig) -> None:
    sneaky = rig["fixture"]("tags_buggy.c").replace(
        "const char *parse_tags(", '__attribute__((no_sanitize("address")))\nconst char *parse_tags(', 1)
    sneaky += '\nconst char *__asan_default_options(void) { return "replace_intrin=0"; }\n'
    outcome = rig["assessor"].assess(rig["criterion"](), rig["snapshot"]({**rig["common"], "tags.c": sneaky}))
    assert outcome["result"] in ("fail", "inconclusive")
    assert outcome["result"] != "pass"
    rules = {rig["controller"].store.finding(fid).rule_id for fid in outcome["finding_ids"]}
    assert "native.sanitizer_bypass" in rules


@needs_sandbox
def test_sanitizer_aware_code_is_not_trusted(rig) -> None:
    # Takes the safe path only when it detects the sanitizer: the classic way to game a checker.
    def body(name: str) -> str:
        return rig["fixture"](name).split("#include <string.h>\n", 1)[1]

    aware = ('#include "tags.h"\n#include <string.h>\n#if defined(__SANITIZE_ADDRESS__)\n'
             + body("tags_fixed.c") + "#else\n" + body("tags_buggy.c") + "#endif\n")
    outcome = rig["assessor"].assess(rig["criterion"](), rig["snapshot"]({**rig["common"], "tags.c": aware}))
    # Under the sanitizer the safe code ran, so every case passed; the result still is not a pass.
    assert outcome["run"].counts["fail"] == 0 and outcome["reports"] == []
    assert outcome["result"] == "inconclusive"
    claims = [rig["controller"].store.finding(fid).claim for fid in outcome["finding_ids"]]
    assert any("differently when sanitizers are enabled" in claim for claim in claims)


def test_suite_cases_are_base64_bound_to_the_oracle() -> None:
    document = suite_document()
    case = next(c for c in document["cases"] if c["case_id"] == "valid_full")
    assert base64.b64decode(case["files"]["packet.bin"]) == SEED_FULL
    assert case["expect"]["stdout_json"] == {"id": 256, "name": "abc", "tags": ["hi", "yo"]}

"""`forge check`: forge.toml, protected tests, structural coverage (MC/DC), analysis, sanitizers.

The sandboxed tests build and run real programs (Ada with GNAT 14, Fortran
and C with GCC 14) and skip with the reason when the isolation backend or a
toolchain is missing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.check import coverage as cov
from forge.check.project import detect, parse_project, starter
from forge.check.service import CheckService
from forge.compliance.checks import CHECKS
from forge.compliance.service import ComplianceService
from forge.contracts.errors import ForgeError
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from tests.conftest import command

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND = LinuxNamespaceBackend()


def needs(*programs: str):  # noqa: ANN201
    missing = [p for p in programs if shutil.which(p) is None]
    return pytest.mark.skipif(not BACKEND.capabilities().available or bool(missing),
                              reason=str(BACKEND.capabilities().unavailable_reason or f"not installed: {missing}"))


# ----------------------------------------------------------------- forge.toml

MINIMAL = b'[build]\ntoolchain = "c"\nsources = ["a.c"]\n\n[[tests]]\nid = "one"\nargs = ["1"]\nstdout = "x\\n"\n'


def test_project_file_becomes_a_protected_suite() -> None:
    spec = parse_project(b'[build]\ntoolchain = "ada"\nsources = ["main.adb"]\n\n'
                         b'[[tests]]\nid = "warn"\nargs = ["20", "{case}/in.txt"]\nstdin = "hi"\n'
                         b'files = { "in.txt" = "data" }\nstdout = "WARN\\n"\nverifies = ["HLR-1"]\n\n'
                         b'[[tests]]\nid = "bad"\nexit_code = "nonzero"\nstderr_contains = "malformed"\n')
    suite = spec.suite()
    assert suite["entrypoint"] == ["/input/program"] and [c["case_id"] for c in suite["cases"]] == ["warn", "bad"]
    first, second = suite["cases"]

    def stated(expect: dict) -> dict:
        return {k: v for k, v in expect.items() if v is not None}

    assert stated(first["expect"]) == {"exit_code": 0, "stdout_text": "WARN\n"} and first["stdin_b64"] == "aGk="
    assert first["files"] == {"in.txt": "ZGF0YQ=="} and first["args"] == ["20", "{case}/in.txt"]
    assert stated(second["expect"]) == {"exit_code": "nonzero", "stderr_contains": "malformed"}
    assert spec.verifies() == {"HLR-1": ["forge.toml:test warn"]}
    python = parse_project(b'[build]\ntoolchain = "python"\nentry = "cli.py"\n[[tests]]\nid = "t"\n')
    assert python.suite()["entrypoint"] == ["{python}", "-I", "/input/cli.py"]


@pytest.mark.parametrize(("text", "message"), [
    (b"[build\n", "not valid TOML"),
    (b'[build]\ntoolchain = "rust"\nsources = ["a"]\n[[tests]]\nid = "t"\n', "build.toolchain"),
    (b'[build]\ntoolchain = "ada"\n[[tests]]\nid = "t"\n', "lists its sources"),
    (b'[build]\ntoolchain = "python"\n[[tests]]\nid = "t"\n', "names its script"),
    (b'[build]\ntoolchain = "c"\nsources = ["-o/etc/x"]\n[[tests]]\nid = "t"\n', "compiler option"),
    (b'[build]\ntoolchain = "c"\nsources = ["../a.c"]\n[[tests]]\nid = "t"\n', "build.sources"),
    (b'[build]\ntoolchain = "c"\nsources = ["a.c"]\n[[tests]]\nid = "High-AoA"\n', "lowercase"),
    (b'[build]\ntoolchain = "c"\nsources = ["a.c"]\n[[tests]]\nid = "t"\n[[tests]]\nid = "t"\n', "repeated"),
    (b'[build]\ntoolchain = "c"\nsources = ["a.c"]\n', "tests"),
])
def test_bad_project_files_say_what_to_change(text: bytes, message: str) -> None:
    with pytest.raises(ForgeError, match="forge.toml") as error:
        parse_project(text)
    assert message in str(error.value)


def test_the_accepted_identity_covers_build_and_tests() -> None:
    base = parse_project(MINIMAL)
    assert parse_project(MINIMAL.replace(b'"x\\n"', b'"y\\n"')).digest() != base.digest()
    assert parse_project(MINIMAL.replace(b'"a.c"', b'"b.c"')).digest() != base.digest()
    assert parse_project(b"# a comment\n" + MINIMAL).digest() == base.digest()


def test_init_detects_the_toolchain_and_writes_a_file_that_parses(tmp_path: Path) -> None:
    (tmp_path / "main.adb").write_text("procedure Main is begin null; end Main;\n")
    (tmp_path / "util.adb").write_text("")
    (tmp_path / "notes.py").write_text("")
    toolchain, sources = detect(tmp_path)
    assert (toolchain, sources) == ("ada", ["main.adb"])
    assert parse_project(starter(toolchain, sources).encode()).build.sources == ["main.adb"]
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ForgeError, match="no source files"):
        detect(empty)


# ------------------------------------------------------------------- coverage

REPORT = {"current_working_directory": "/scratch/build", "files": [
    {"file": "stall.adb", "lines": [
        {"line_number": 5, "count": 4, "branches": [{"count": 2, "throw": False}, {"count": 0, "throw": False},
                                                    {"count": 0, "throw": True}],
         "conditions": [{"count": 4, "covered": 3, "not_covered_true": [], "not_covered_false": [1]}]},
        {"line_number": 6, "count": 0, "branches": []}]},
    {"file": "/scratch/build/b~main.adb", "lines": [{"line_number": 1, "count": 0, "branches": []}]}]}


def test_coverage_counts_only_the_snapshot_files_and_merges_units() -> None:
    other_unit = {"current_working_directory": "/scratch/build", "files": [{"file": "stall.adb", "lines": [
        {"line_number": 6, "count": 2, "branches": [],
         "conditions": []},
        {"line_number": 5, "count": 0, "branches": [{"count": 0, "throw": False}, {"count": 1, "throw": False}],
         "conditions": [{"count": 4, "covered": 1, "not_covered_true": [0, 1], "not_covered_false": [0]}]}]}]}
    summary = cov.summarize(cov.parse_reports(json.dumps(REPORT) + "\n" + json.dumps(other_unit)),
                            {"stall.adb", "stall.ads"})
    assert summary["excluded_generated"] == ["b~main.adb"]
    assert summary["totals"] == {"statements": [2, 2], "branches": [2, 2], "conditions": [3, 4]}
    assert summary["mcdc_percent"] == 75.0 and summary["statement_percent"] == 100.0
    assert summary["uncovered_conditions"] == [{"path": "stall.adb", "line": 5, "not_covered_true": [],
                                                "not_covered_false": [1]}]
    assert cov.summarize([], set())["mcdc_percent"] == 100.0       # nothing to cover


def test_source_that_touches_coverage_internals_is_flagged() -> None:
    assert cov.integrity_problems({"a.c": b"extern void __gcov_dump(void);\n", "b.adb": b"-- measured by gcov\n",
                                   "notes.txt": b"__gcov"}) == ["a.c:1 refers to coverage internals"]


# ----------------------------------------------------------- sandboxed checks


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    project_id = controller.apply_command(command("project.create", {
        "name": "Checked", "objective": "o", "source_roots": ["main"]}), human).project_id

    def capture(fixture: str | None = None, files: dict[str, str] | None = None) -> str:
        if source.exists():
            shutil.rmtree(source)
        if fixture:
            shutil.copytree(FIXTURES / fixture, source)
        else:
            source.mkdir()
        for name, text in (files or {}).items():
            (source / name).write_text(text)
        trust.approve_source_root(project_id, "main", source, human)
        return controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]

    return {"service": CheckService(controller, BACKEND, tmp_path / "work"), "capture": capture, "human": human,
            "controller": controller, "store": controller.store, "project_id": project_id}


def test_the_coverage_check_refuses_untrusted_or_missing_measurements(rig) -> None:  # noqa: ANN001
    snapshot = rig["capture"](None, {"a.c": "int main(void){return 0;}\n"})

    def record(observation: dict) -> None:
        rig["controller"].apply_command(command("evidence.record_execution", {
            "job_id": "job_00000000-0000-4000-8000-000000000001", "attempt_number": 1, "snapshot_id": snapshot,
            "tool_ref": "t@1", "environment_digest": "sha256:" + "0" * 64, "observation_type": "structural_coverage",
            "observation": observation, "artifact_refs": []}, project_id=rig["project_id"]),
            rig["controller"].system_actor)

    check = CHECKS["structural_coverage"]
    record({"mcdc_percent": 100.0, "integrity_problems": ["a.c:1 refers to coverage internals"]})
    assert check.run(rig["store"], snapshot, {"coverage": "mcdc"}).status == "not_met"
    record({"statement_percent": 100.0})
    assert "not measured" in check.run(rig["store"], snapshot, {"coverage": "mcdc"}).detail
    record({"mcdc_percent": 90.0, "uncovered_conditions": [{"path": "a.c", "line": 3}, {"path": "a.c", "line": 3}]})
    result = check.run(rig["store"], snapshot, {"coverage": "mcdc"})
    assert result.status == "not_met" and result.detail.endswith("uncovered conditions at a.c:3")


@needs("gnatmake-14", "gcov-14")
def test_ada_is_tested_analyzed_and_measured_for_mcdc(rig) -> None:  # noqa: ANN001
    service, store = rig["service"], rig["store"]
    ComplianceService(rig["controller"]).set_domain(rig["human"], "avionics", {"level": "A"})
    snapshot = rig["capture"]("ada_stall")
    outcome = service.run(rig["human"], "all", snapshot)
    tests, analyze, coverage = outcome["steps"]
    assert tests["result"] == "pass" and len(tests["cases"]) == 8 and "accepted 8 test case" in tests["notices"][0]
    assert analyze["result"] == "analyzed" and analyze["finding_count"] == 0
    assert coverage["result"] == "measured" and coverage["statement_percent"] == 100.0
    assert coverage["excluded_generated"] == ["b~main.adb"]          # the binder's file is not the project's code
    evidence = [e for e in _evidence(store, snapshot, "structural_coverage")]
    assert evidence[-1]["files"]["stall.adb"]["conditions"] == [8, 8]   # the warning logic: full MC/DC
    uncovered = {(c["path"], c["line"]) for c in coverage["uncovered_conditions"]}
    assert uncovered == {("main.adb", 19), ("main.adb", 21)}         # string "=" expands into conditions
    checks = outcome["compliance"]["checks"]
    assert checks["tests_pass"]["status"] == "met" and checks["requirements_traceability"]["status"] == "met"
    assert checks["static_analysis"]["status"] == "met"
    assert checks["structural_coverage"]["status"] == "not_met" and "main.adb:19" in checks["structural_coverage"][
        "detail"]
    assert outcome["compliance"]["gate_status"] == "fail" and _evidence(store, snapshot, "ada_build")


def _evidence(store, snapshot: str, kind: str) -> list[dict]:  # noqa: ANN001
    rows = store.conn.execute("SELECT observation_json FROM evidence WHERE snapshot_id = ? AND observation_type = ? "
                              "ORDER BY rowid", (snapshot, kind)).fetchall()
    return [json.loads(r[0]) for r in rows]


@needs("gnatmake-14")
def test_edited_tests_are_not_used_until_a_person_accepts_them(rig, model_actor) -> None:  # noqa: ANN001
    service = rig["service"]
    first = rig["capture"]("ada_stall")
    assert service.tests(rig["human"], first)["result"] == "pass"
    weakened = (FIXTURES / "ada_stall" / "forge.toml").read_text().replace('stdout = "WARN\\n"', 'stdout = "OK\\n"', 1)
    edited = rig["capture"]("ada_stall", {"forge.toml": weakened})
    kept = service.tests(model_actor, edited)
    assert kept["result"] == "pass" and "accepted tests ran" in kept["notices"][0]
    with pytest.raises(ForgeError, match="only a trusted person accepts"):
        service.tests(model_actor, edited, accept=True)
    changed = service.tests(rig["human"], edited, accept=True)
    assert changed["result"] == "fail" and "replacing the earlier" in changed["notices"][0]
    assert [c["case_id"] for c in changed["cases"] if c["outcome"] != "pass"] == ["high_aoa_slow"]


@needs("gfortran-14", "gcov-14")
def test_fortran_coverage_finds_the_untested_condition(rig) -> None:  # noqa: ANN001
    snapshot = rig["capture"]("fortran_limiter")
    service = rig["service"]
    assert service.tests(rig["human"], snapshot)["result"] == "pass"
    measured = service.coverage(rig["human"], snapshot)
    assert measured["result"] == "measured" and measured["statement_percent"] == 100.0
    assert {c["line"] for c in measured["uncovered_conditions"]} == {15, 18, 21}   # no non-numeric input tested
    assert list(service.assessment.work_root.glob("job_*")) == []                 # sandboxes are cleaned up


BUGGY_C = """#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    int table[3] = {1, 2, 3};
    int index = atoi(argv[1]);
    printf("%d\\n", table[index]);
    return 0;
}
"""
NULL_C = """#include <stdlib.h>
int main(void) {
    int *p = malloc(sizeof *p);
    *p = 1;
    free(p);
    return 0;
}
"""


@needs("gcc", "gcc-14", "gcov-14")
def test_c_runs_under_sanitizers_and_mcdc_names_the_missing_case(rig) -> None:  # noqa: ANN001
    service = rig["service"]
    snapshot = rig["capture"]("c_band")
    sanitized = service.sanitize(rig["human"], snapshot)
    assert sanitized["result"] == "pass" and sanitized["sanitizers"] == ["address", "undefined"]
    measured = service.coverage(rig["human"], snapshot)
    assert measured["uncovered_conditions"] == [{"path": "band.c", "line": 7, "not_covered_true": [],
                                                 "not_covered_false": [1]}]   # no value above the band was tried
    toml = ('[build]\ntoolchain = "c"\nsources = ["band.c"]\n[[tests]]\nid = "past_end"\nargs = ["5"]\n'
            'stdout = "0\\n"\n')
    overflow = rig["capture"](None, {"band.c": BUGGY_C, "forge.toml": toml})
    found = service.sanitize(rig["human"], overflow, accept=True)
    assert found["result"] == "fail"
    assert found["sanitizer_reports"] == ["past_end: undefined.out-of-bounds-index at band.c:6"]


@needs("gcc-14")
def test_the_c_analyzer_records_findings(rig) -> None:  # noqa: ANN001
    toml = '[build]\ntoolchain = "c"\nsources = ["leak.c"]\n[[tests]]\nid = "runs"\n'
    snapshot = rig["capture"](None, {"leak.c": NULL_C, "forge.toml": toml})
    analyzed = rig["service"].analyze(rig["human"], snapshot)
    assert analyzed["result"] == "analyzed" and analyzed["finding_count"] >= 1
    assert any("-Wanalyzer-possible-null-dereference" in f for f in analyzed["findings"])
    assert CHECKS["static_analysis"].run(rig["store"], snapshot, {}).status == "not_met"


@needs("gcc-14", "gcov-14")
def test_forged_counters_make_coverage_inconclusive(rig) -> None:  # noqa: ANN001
    source = (FIXTURES / "c_band" / "band.c").read_text() + "\nextern void __gcov_reset(void);\n"
    snapshot = rig["capture"]("c_band", {"band.c": source})
    measured = rig["service"].coverage(rig["human"], snapshot)
    assert measured["result"] == "inconclusive" and measured["integrity_problems"]
    assert CHECKS["structural_coverage"].run(rig["store"], snapshot, {"coverage": "statement"}).status == "not_met"


def test_without_a_project_file_the_check_says_how_to_start(rig) -> None:  # noqa: ANN001
    snapshot = rig["capture"](None, {"main.c": "int main(void){return 0;}\n"})
    with pytest.raises(ForgeError, match="forge check init"):
        rig["service"].tests(rig["human"], snapshot)
    outcome = rig["service"].run(rig["human"], "all", snapshot)
    assert outcome["steps"] == [{"step": "tests", "result": "error", "reason": outcome["steps"][0]["reason"]}]
    assert outcome["compliance"]["profile"] == "General software (NIST SSDF)"


# ------------------------------------------------------------------- the CLI


@needs("gnatmake-14", "gcov-14")
def test_cli_init_then_check(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    from forge.api.client import ForgeClient
    from forge.api.launcher import launch
    from forge.client_cli import main as cli

    source = tmp_path / "src"
    shutil.copytree(FIXTURES / "ada_stall", source)
    (source / "forge.toml").unlink()
    assert cli(["check", "init", "--source", str(source)]) == 0
    assert "toolchain ada, sources main.adb" in capsys.readouterr().out
    assert cli(["check", "init", "--source", str(source)]) == 1           # never overwrites without --force
    shutil.copy(FIXTURES / "ada_stall" / "forge.toml", source / "forge.toml")
    root = tmp_path / "root"
    launched = launch(root)
    try:
        client = ForgeClient(launched.server.url, launched.token)
        client.call("project.create", {"idempotency_key": "c", "params": {"name": "Stall", "objective": "o",
                                                                         "source_roots": ["main"]}})
        client.call("authority.approve_source_root", {"label": "main", "path": str(source)})
        assert cli(["check", "--root", str(root)]) == 0
        out = capsys.readouterr().out
        assert "tests     pass          8/8 cases passed (ada)" in out
        assert "MC/DC 85.7%" in out and "main.adb:19" in out and "compliance gate PASS" in out
        assert cli(["check", "coverage", "--no-capture", "--json", "--root", str(root)]) == 0
        assert json.loads(capsys.readouterr().out)["steps"][0]["mcdc_percent"] == 85.71
    finally:
        launched.close()

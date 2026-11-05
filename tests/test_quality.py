"""Coding standards as code: fingerprints, knowledge packs, adapters, classification, Power of Ten, gate.

Unit tests use small recorded tool outputs; the sandboxed tests run the real
checkers (GCC 14 analyzer, clang-tidy, cppcheck, ruff, ShellCheck, GNAT) and
skip with the reason when a tool or the isolation backend is missing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.check.project import parse_project
from forge.check.service import CheckService
from forge.compliance.checks import CHECKS
from forge.contracts.errors import ForgeError
from forge.quality import adapters, knowledge, power_of_ten
from forge.quality import registry as standards
from forge.quality.findings import QualityFinding, dumps, from_sarif, merge, to_sarif
from forge.quality.fingerprint import line_hashes
from forge.quality.guard import QualityGate
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from tests.conftest import command

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND = LinuxNamespaceBackend()


def needs(*programs: str):  # noqa: ANN201
    missing = [p for p in programs if shutil.which(p) is None]
    return pytest.mark.skipif(not BACKEND.capabilities().available or bool(missing),
                              reason=str(BACKEND.capabilities().unavailable_reason or f"not installed: {missing}"))


# ----------------------------------------------------------- fingerprints

# The expected values are github/codeql-action's own test vectors (src/fingerprints.test.ts).
@pytest.mark.parametrize(("text", "expected"), [
    ("", ["c129715d7a2bc9a3:1"]),
    (" a\nb\n  \t\tc\n d", ["271789c17abda88f:1", "54703d4cd895b18:1", "180aee12dab6264:1", "a23a3dc5e078b07b:1"]),
    (" hello; \t\r\nworld!!!\r\n\r\n\r\n  \t\tGreetings\r\n End\r\n",
     ["e9496ae3ebfced30:1", "fb7c023a8b9ccb3f:1", "ce8ba1a563dcdaca:1", "e20e36e16fcb0cc8:1", "b3edc88f2938467e:1",
      "c8e28b0b4002a3a0:1", "c129715d7a2bc9a3:1"]),
    (" hello; \t\nworld!!!\r\n\n\r  \t\tGreetings\r End\r\n",
     ["e9496ae3ebfced30:1", "fb7c023a8b9ccb3f:1", "ce8ba1a563dcdaca:1", "e20e36e16fcb0cc8:1", "b3edc88f2938467e:1",
      "c8e28b0b4002a3a0:1", "c129715d7a2bc9a3:1"]),
    ("Lorem ipsum dolor sit amet.\n" * 10,
     ["a7f2ff13bc495cf2:1", "a7f2ff13bc495cf2:2", "a7f2ff13bc495cf2:3", "a7f2ff13bc495cf2:4", "a7f2ff13bc495cf2:5",
      "a7f2ff13bc495cf2:6", "a7f2ff1481e87703:1", "a9cf91f7bbf1862b:1", "55ec222b86bcae53:1", "cc97dc7b1d7d8f7b:1",
      "c129715d7a2bc9a3:1"]),
    ("x = 2\nx = 1\nprint(x)\nx = 3\nprint(x)\nx = 4\nprint(x)\n",
     ["e54938cc54b302f1:1", "bb609acbe9138d60:1", "1131fd5871777f34:1", "5c482a0f8b35ea28:1", "54517377da7028d2:1",
      "2c644846cb18d53e:1", "f1b89f20de0d133:1", "c129715d7a2bc9a3:1"]),
])
def test_line_hashes_match_github_code_scanning(text: str, expected: list[str]) -> None:
    assert line_hashes(text) == expected


# ------------------------------------------------------------ knowledge packs


def test_cwe_pack_is_the_pinned_mitre_release_with_its_notice() -> None:
    pack = knowledge.cwe_pack()
    assert (pack["version"], pack["date"]) == ("5.0", "2026-10-28")
    assert pack["source_sha256"] == "sha256:bcda44c9abf61ff977f50c3c6649eea5a97a7b0626b0396c2c3ec4bfb973a3dc"
    assert "reproduce MITRE's copyright designation and this license" in pack["notice"]
    assert len(pack["weaknesses"]) == 984
    assert sorted(knowledge.view_members("1435")) == [20, 22, 77, 78, 79, 89, 94, 120, 121, 122, 125, 200, 284, 306,
                                                     352, 416, 434, 476, 502, 639, 770, 787, 862, 863, 918]
    groups = {g: len(v["members"]) for g, v in pack["views"]["1305"]["groups"].items()}
    assert groups == {"1306": 34, "1307": 28, "1308": 34, "1309": 15}
    assert knowledge.cert_for_cwe(476) == [("EXP34-C", "Exact")]


def test_cert_pack_carries_rules_risk_detectors_and_attribution() -> None:
    pack = knowledge.cert_pack()
    rules = pack["rules"]
    assert sum(r["standard"] == "cert-c" for r in rules.values()) == 122
    assert sum(r["standard"] == "cert-cpp" for r in rules.values()) == 83
    exp34 = rules["EXP34-C"]
    assert exp34["title"] == "Do not dereference null pointers" and 476 in exp34["cwe"]
    assert {"tool": "cppcheck", "checker": "nullPointer", "note": ""} in exp34["detectors"]
    err33 = rules["ERR33-C"]
    assert err33["risk"]["detectable"] and err33["risk"]["repairable"] and "setlocale" in err33["compliant_example"]
    assert err33["url"].endswith("/sei-cert-c-coding-standard/rules/error-handling-err/err33-c")
    assert "Creative Commons Attribution 4.0" in pack["notice"] and "MIT" in pack["notice"]
    assert knowledge.bandit_cwe()["B602"] == [78] and len(knowledge.bandit_cwe()) == 75


def test_labels_name_top25_cisq_owasp_and_exact_cert_equivalents() -> None:
    assert knowledge.labels([476]) == ["CWE Top 25 (2025)", "CISQ reliability", "OWASP A10:2025", "CERT EXP34-C"]
    assert knowledge.in_top25(121) and not knowledge.in_top25(401)
    assert knowledge.in_top25(123) and not knowledge.in_top25(123, roll_up=False)   # write-what-where -> CWE-787


# ------------------------------------------------------------------- adapters

GCC_SARIF = json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "GNU C17", "rules": [
    {"id": "-Wanalyzer-possible-null-dereference"}]}}, "results": [{
        "ruleId": "-Wanalyzer-possible-null-dereference", "level": "warning",
        "taxa": [{"id": "690", "toolComponent": {"name": "cwe"}}], "message": {"text": "dereference of possibly-NULL 'p'"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "/scratch/build/t.c"},
                                            "region": {"startLine": 3, "startColumn": 5}}}]}]}]})


def test_adapters_read_each_tool_and_keep_cwe() -> None:
    [gcc] = adapters.sarif_stream(GCC_SARIF + "\n" + json.dumps({"version": "2.1.0", "runs": []}), "gcc")
    assert (gcc.path, gcc.line, gcc.cwe, gcc.rule) == ("t.c", 3, [690], "gcc:-Wanalyzer-possible-null-dereference")
    [tidy] = adapters.clang_tidy("/scratch/build/band.c:12:9: warning: the value returned by this function should "
                                 "not be disregarded [cert-err33-c]\n12 | fputs(...)\n")
    assert (tidy.path, tidy.rule_id) == ("band.c", "cert-err33-c")
    [leak] = adapters.cppcheck_xml('<?xml version="1.0"?><results version="2"><errors>'
                                   '<error id="missingIncludeSystem" severity="information" msg="x"/>'
                                   '<error id="memleak" severity="error" msg="Memory leak: p" cwe="401">'
                                   '<location file="t.c" line="3" column="84"/></error></errors></results>')
    assert (leak.rule_id, leak.cwe, leak.level) == ("memleak", [401], "error")
    [ruff] = adapters.ruff_json('[{"code": "S602", "message": "shell", "filename": "/scratch/build/a.py",'
                                ' "location": {"row": 2, "column": 1}}]', knowledge.bandit_cwe())
    assert (ruff.path, ruff.cwe) == ("a.py", [78])
    [shell] = adapters.shellcheck_json('{"comments": [{"file": "s.sh", "line": 2, "column": 1, "level": "error",'
                                       ' "code": 2115, "message": "m"}]}')
    assert (shell.rule_id, shell.level) == ("SC2115", "error")
    with pytest.raises(adapters.AdapterError):
        adapters.cppcheck_xml("cppcheck: error: unrecognized command line option")
    with pytest.raises(adapters.AdapterError):
        adapters.sarif_stream("{not json", "gcc")


def test_sarif_cwe_comes_from_tags_relationships_and_text_but_must_exist() -> None:
    document = {"runs": [{"tool": {"driver": {"name": "CodeQL", "rules": [
        {"id": "cpp/a", "properties": {"tags": ["security", "external/cwe/cwe-787"]},
         "relationships": [{"target": {"id": "120", "toolComponent": {"name": "CWE"}}}]}]}},
        "results": [{"ruleId": "cpp/a", "message": {"text": "see CWE-121 and CWE-9999"},
                     "locations": [{"physicalLocation": {"artifactLocation": {"uri": "file:///input/x.c"},
                                                         "region": {"startLine": 7}}}]}]}]}
    [finding] = from_sarif(document)
    assert finding.cwe == [120, 121, 787] and finding.path == "x.c"     # CWE-9999 does not exist: dropped


def test_findings_merge_across_tools_and_sarif_output_is_byte_stable() -> None:
    def make(tool: str, rule: str, cwe: list[int], severity: str = "medium") -> QualityFinding:
        return QualityFinding(tool=tool, rule_id=rule, level="warning", message=rule, path="a.c", line=4,
                              cwe=cwe, line_hash="abc:1", severity=severity)
    merged = merge([make("cppcheck", "nullPointer", [476]), make("gcc", "-Wanalyzer-null-dereference", [476], "high"),
                    make("clang-tidy", "misc-x", [])])
    assert len(merged) == 2
    lead = next(f for f in merged if f.cwe == [476])
    assert lead.tool == "gcc" and lead.also_reported_by == ["cppcheck:nullPointer"]
    first, second = dumps(to_sarif(merged, tools={"gcc": "14"})), dumps(to_sarif(list(reversed(merged)),
                                                                                tools={"gcc": "14"}))
    assert first == second
    result = json.loads(first)["runs"][0]["results"][0]
    assert result["partialFingerprints"] == {"primaryLocationLineHash": "abc:1"} and len(result["guid"]) == 36


# ------------------------------------------------------------- classification


def finding(tool: str, rule: str, *, cwe: list[int] | None = None, level: str = "warning",
            message: str = "m") -> QualityFinding:
    return QualityFinding(tool=tool, rule_id=rule, level=level, message=message, path="a.c", line=1, cwe=cwe or [])


def test_findings_are_tied_to_cert_and_cwe_and_block_only_under_their_standard() -> None:
    cert_c = standards.resolve("c", ["cert-c"])
    err33 = standards.classify(finding("clang-tidy", "cert-err33-c"), cert_c)
    assert err33.blocking and err33.severity == "high" and "CERT ERR33-C" in err33.standards
    null = standards.classify(finding("cppcheck", "nullPointer", cwe=[476]), cert_c)
    assert null.blocking and {"CERT EXP34-C", "CWE Top 25 (2025)"} <= set(null.standards)
    advisory = standards.resolve("c", ["shell"])
    assert not standards.classify(finding("clang-tidy", "cert-err33-c"), advisory).blocking
    top25 = standards.resolve("python", ["cwe-top25"])
    assert standards.classify(finding("ruff", "S602", cwe=[78]), top25).blocking
    pep8 = standards.resolve("python", ["pep8"])
    assert not standards.classify(finding("ruff", "E501"), pep8).blocking
    ada = standards.classify(finding("ada", "compiler-error", level="error",
                                     message='violation of restriction "No_Recursion" at forge.adc:1'),
                             standards.resolve("ada", ["ada-restricted"]))
    assert ada.blocking and "Ada pragma Restrictions (No_Recursion)" in ada.standards
    guideline = standards.classify(finding("clang-tidy", "cppcoreguidelines-pro-bounds-pointer-arithmetic",
                                           cwe=[787]), standards.resolve("cpp", ["cwe-top25", "cpp-core-guidelines"]))
    assert not guideline.blocking and "C++ Core Guidelines (cppcoreguidelines-pro-bounds-pointer-arithmetic)" in \
        guideline.standards                      # the guidelines profile advises; it never blocks
    assert standards.cert_rules(finding("clang-tidy", "clang-diagnostic-delete-incomplete")) == ["EXP57-CPP"]
    assert standards.cert_rules(finding("gcc", "-Wanalyzer-possible-null-dereference", cwe=[690])) == ["EXP34-C"]
    assert all(knowledge.cert_rule(rule) for rules in standards.FORGE_CERT_MAP.values() for rule in rules)
    with pytest.raises(ValueError, match="unknown standard"):
        standards.resolve("c", ["misra-c-2099"])
    assert [p.profile_id for p in standards.resolve("python", None)] == ["python-secure", "cwe-top25", "shell"]


def test_the_repair_brief_hands_the_model_the_rule_and_cert_compliant_example() -> None:
    brief = standards.repair_brief(standards.classify(finding("clang-tidy", "cert-err33-c"),
                                                      standards.resolve("c", ["cert-c"])))
    assert "CERT ERR33-C Detect and handle standard library errors" in brief
    assert "compliant pattern from CERT ERR33-C" in brief and "setlocale" in brief


def test_project_file_standards_are_validated_and_keep_old_identities() -> None:
    plain = b'[build]\ntoolchain = "c"\nsources = ["a.c"]\n[[tests]]\nid = "t"\n'
    chosen = parse_project(plain + b'[quality]\nstandards = ["cert-c", "power-of-ten"]\n')
    assert chosen.quality.standards == ["cert-c", "power-of-ten"]
    assert chosen.digest() != parse_project(plain).digest()
    assert "quality" not in json.dumps(parse_project(plain).model_dump(mode="json", exclude_none=True))
    with pytest.raises(ForgeError, match="unknown standard"):
        parse_project(plain + b'[quality]\nstandards = ["misra-c-2099"]\n')


# --------------------------------------------------------------- Power of Ten

POWER_OF_TEN_C = b'''#include <assert.h>
#include <stdlib.h>
#define CAT(a, b) a##b
#define LOG(fmt, ...) printf(fmt, __VA_ARGS__)
typedef char *string_t;
int fact(int n) { if (n <= 1) return 1; return n * fact(n - 1); }
int ping(int n); int pong(int n) { return n ? ping(n - 1) : 0; }
int ping(int n) { return n ? pong(n - 1) : 1; }
void handler(void (*cb)(int)) { cb(1); }
int main(int argc, char **argv) {
    char **names = 0;
    const char *s = "goto in a string; for (;;)";
    int *p = malloc(4);
    assert(argc > 0); assert(p != 0);
    for (;;) { goto done; }
done:
    free(p);
    return argc * *argv[0];
}
void buffers_init(void) { void *b = malloc(64); free(b); }
'''


def test_power_of_ten_finds_each_rule_and_avoids_its_traps() -> None:
    found = {(f.rule_id, f.line, f.blocking) for f in power_of_ten.analyze({"m.c": POWER_OF_TEN_C})}
    assert found == {
        ("P10-1", 15, True),                       # goto
        ("P10-1", 6, True), ("P10-1", 7, True), ("P10-1", 8, True),   # fact; pong <-> ping
        ("P10-2", 15, False),                      # for (;;): a person confirms
        ("P10-3", 13, False), ("P10-3", 17, False),  # malloc/free outside *init*
        ("P10-5", 6, True),                        # 2 asserts across 6 functions
        ("P10-8", 3, True), ("P10-8", 4, True),    # ## and variadic macro
        ("P10-9", 5, True), ("P10-9", 9, True), ("P10-9", 11, True),  # typedef pointer, fn pointer, char **
    }
    clean = b'#include <assert.h>\nint add(int a, int b) {\n  assert(a > 0);\n  assert(b > 0);\n  return a + b;\n}\n'
    assert power_of_ten.analyze({"ok.c": clean}) == []


def test_comment_and_string_stripping_keeps_line_numbers() -> None:
    text = 'a /* goto\n x */ b // goto\n"goto\\"s" c\n'
    stripped = power_of_ten.strip(text)
    assert stripped.count("\n") == text.count("\n") and "goto" not in stripped and stripped.split()[0] == "a"


# ---------------------------------------------------------------------- gate


class FakeScanner:
    def __init__(self, scans: dict[str, list[QualityFinding]], status: str = "ok") -> None:
        self.scans, self.status, self.calls = scans, status, []

    def scan(self, snapshot_id: str, build: dict, chosen: list[str] | None):  # noqa: ANN201
        self.calls.append(snapshot_id)
        from forge.quality.runner import ScanResult

        result = ScanResult(findings=self.scans[snapshot_id], profiles=["cert-c"])
        if self.status == "error":
            result.runs.append({"tool": "cppcheck", "status": "error", "problem": "cppcheck timed_out"})
        return result


def blocking(rule: str, line: int, snippet: str, line_hash: str) -> QualityFinding:
    item = finding("clang-tidy", rule)
    return standards.classify(item, standards.resolve("c", ["cert-c"])).model_copy(
        update={"line": line, "snippet": snippet, "line_hash": line_hash})


def test_the_gate_blames_only_new_violations_and_says_how_to_fix_them() -> None:
    old = blocking("cert-err33-c", 12, "fputs(msg, stderr);", "aaa:1")
    moved = old.model_copy(update={"line": 14, "line_hash": "bbb:1"})          # same code, shifted by an edit
    new = blocking("cert-err33-c", 30, "fclose(f);", "ccc:1")
    gate = QualityGate(FakeScanner({"base": [old], "cand": [moved, new], "clean": [moved]}), {"toolchain": "c"}, None)
    verdict = gate.review("base", "cand")
    assert not verdict.passed and verdict.new == [new]
    assert "CERT ERR33-C" in verdict.feedback[0] and "compliant pattern" in verdict.feedback[0]
    assert gate.review("base", "clean").passed
    assert gate.scanner.calls.count("base") == 1                               # the baseline is scanned once
    broken = QualityGate(FakeScanner({"base": [], "cand": []}, status="error"), {"toolchain": "c"}, None)
    failed = broken.review("base", "cand")
    assert not failed.passed and "could not analyze" in failed.feedback[0]


# ---------------------------------------------------------- sandboxed checkers


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    project_id = controller.apply_command(command("project.create", {
        "name": "Q", "objective": "o", "source_roots": ["main"]}), human).project_id

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
            "controller": controller, "store": controller.store}


@needs("gcc-14", "clang-tidy", "cppcheck")
def test_c_is_checked_against_cert_and_the_sarif_is_recorded(rig) -> None:  # noqa: ANN001
    snapshot = rig["capture"]("c_band")
    analyzed = rig["service"].analyze(rig["human"], snapshot)
    assert analyzed["result"] == "analyzed" and analyzed["standards"] == ["cert-c", "cwe-top25", "shell"]
    assert analyzed["blocking_count"] >= 1
    assert any("band.c:12: BLOCKING" in line and "CERT ERR33-C" in line for line in analyzed["findings"])
    sarif = json.loads(rig["controller"].blobs.read_bytes(
        rig["store"].artifact(analyzed["sarif_artifact"]).digest, max_bytes=1 << 20))
    assert sarif["version"] == "2.1.0" and "MITRE" in " ".join(sarif["runs"][0]["properties"]["notices"])
    assert CHECKS["static_analysis"].run(rig["store"], snapshot, {}).status == "not_met"


@needs("gcc-14")
def test_power_of_ten_is_chosen_in_forge_toml(rig) -> None:  # noqa: ANN001
    toml = ('[build]\ntoolchain = "c"\nsources = ["m.c"]\n[quality]\nstandards = ["power-of-ten"]\n'
            '[[tests]]\nid = "runs"\n')
    snapshot = rig["capture"](None, {"m.c": POWER_OF_TEN_C.decode(), "forge.toml": toml})
    analyzed = rig["service"].analyze(rig["human"], snapshot)
    assert analyzed["standards"] == ["power-of-ten"]
    assert any("Power of Ten P10-1" in line for line in analyzed["findings"])
    assert analyzed["attestations"] and analyzed["blocking_count"] >= 10


@needs("gnatmake-14")
def test_ada_restrictions_are_enforced_by_the_compiler(rig) -> None:  # noqa: ANN001
    fact = "function Fact (N : Natural) return Natural is\nbegin\n   if N <= 1 then\n      return 1;\n   end if;\n" \
           "   return N * Fact (N - 1);\nend Fact;\n"
    main = "with Fact;\nwith Ada.Text_IO;\nprocedure Main is\nbegin\n   Ada.Text_IO.Put_Line (Natural'Image (Fact (4)));\n" \
           "end Main;\n"
    toml = '[build]\ntoolchain = "ada"\nsources = ["main.adb"]\n[quality]\nstandards = ["ada-restricted"]\n' \
           '[[tests]]\nid = "runs"\nstdout = " 24\\n"\n'
    snapshot = rig["capture"](None, {"fact.adb": fact, "main.adb": main, "forge.toml": toml})
    analyzed = rig["service"].analyze(rig["human"], snapshot)
    assert analyzed["blocking_count"] == 1
    assert any("fact.adb:6: BLOCKING" in line and "No_Recursion" in line for line in analyzed["findings"])


def test_missing_checkers_never_count_as_a_clean_analysis(rig) -> None:  # noqa: ANN001
    snapshot = rig["capture"](None, {"a.c": "int main(void) { return 0; }\n"})
    rig["controller"].apply_command(command("evidence.record_execution", {
        "job_id": "job_00000000-0000-4000-8000-000000000002", "attempt_number": 1, "snapshot_id": snapshot,
        "tool_ref": "forge.check@1", "environment_digest": "sha256:" + "0" * 64,
        "observation_type": "analyzer_findings", "artifact_refs": [],
        "observation": {"status": "ok", "finding_count": 0, "missing_tools": ["cppcheck"]}},
        project_id=rig["controller"].store.project().project_id), rig["controller"].system_actor)
    result = CHECKS["static_analysis"].run(rig["store"], snapshot, {})
    assert result.status == "not_run" and "cppcheck" in result.detail

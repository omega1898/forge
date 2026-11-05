"""Standards as data: which checkers each profile runs, and how each finding is tied to a standard.

A profile is chosen in forge.toml (`[quality] standards = ["cert-c", "power-of-ten"]`) or defaults by
language. It decides which tools run, with which checks, and which findings block (a blocking finding
fails `forge check analyze` and therefore the compliance gate). Classification is a table lookup:

- `clang-tidy:cert-xxxNN-c` checks name their CERT rule directly;
- CERT's own "Automated Detection" tables (bundled) name the open-source checkers for each rule;
- a finding's CWE IDs give exact CERT equivalents, Top 25, CISQ and OWASP memberships (bundled CWE 5.0);
- ruff's S rules carry bandit's CWE IDs.

Rule text that may not be redistributed (MISRA, AUTOSAR, C++ Core Guidelines text) is never needed here:
profiles key on rule IDs and check names only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

from forge.quality import knowledge
from forge.quality.findings import QualityFinding


@dataclass(frozen=True)
class Profile:
    profile_id: str
    title: str
    toolchains: tuple[str, ...]                     # which forge.toml toolchains it applies to
    tools: tuple[str, ...]                          # scanners the runner starts
    clang_tidy_checks: tuple[str, ...] = ()
    ruff_select: tuple[str, ...] = ()
    compiler_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)   # toolchain -> extra flags
    source: str = ""                                # where the standard comes from, and what text FORGE ships
    attestations: tuple[str, ...] = ()              # rules a person must state are met


PROFILES: dict[str, Profile] = {p.profile_id: p for p in (
    Profile("cert-c", "SEI CERT C Coding Standard (rules)", ("c", "c_arm"),
            ("gcc-analyzer", "clang-tidy", "cppcheck"), ("cert-*", "bugprone-*", "clang-analyzer-*"),
            source="SEI CERT C (CC BY 4.0, rule text and examples bundled)"),
    Profile("cert-cpp", "SEI CERT C++ Coding Standard (rules)", ("cpp",), ("clang-tidy", "cppcheck"),
            ("cert-*", "bugprone-*", "clang-analyzer-*"), source="SEI CERT C++ (CC BY 4.0, bundled)"),
    Profile("cwe-top25", "2025 CWE Top 25 most dangerous weaknesses", ("c", "cpp", "c_arm", "python"),
            ("gcc-analyzer", "clang-tidy", "cppcheck", "ruff"), ("clang-analyzer-*", "bugprone-*"), ("S",),
            source="MITRE CWE 5.0, view 1435 (bundled under MITRE's terms)"),
    Profile("power-of-ten", "NASA/JPL Power of Ten rules for safety-critical C", ("c", "c_arm"),
            ("power-of-ten", "compiler-warnings"),
            compiler_flags={"c": ("-Wall", "-Wextra", "-Wpedantic"), "c_arm": ("-Wall", "-Wextra", "-Wpedantic")},
            source="Holzmann 2006; FORGE paraphrases, paper text not bundled",
            attestations=("P10-2 every loop has a provable fixed upper bound",
                          "P10-3 no dynamic allocation after initialization",
                          "P10-6 data objects declared at the smallest possible scope")),
    Profile("cpp-core-guidelines", "C++ Core Guidelines (advisory)", ("cpp",), ("clang-tidy",),
            ("cppcoreguidelines-*",), source="isocpp C++ Core Guidelines; check names only, text not bundled"),
    Profile("python-secure", "Python correctness and security (pyflakes, bugbear, bandit rules)", ("python",),
            ("ruff",), ruff_select=("E9", "F", "B", "S"), source="ruff (MIT) with bandit's CWE table (Apache-2.0)"),
    Profile("pep8", "PEP 8 style (advisory)", ("python",), ("ruff",), ruff_select=("E", "W"),
            source="PEP 8 (public domain)"),
    Profile("ada-warnings", "GNAT: every warning as a finding", ("ada",), ("compiler-warnings",)),
    Profile("ada-restricted", "Ada high-integrity restrictions (compiler-enforced)", ("ada",), ("compiler-warnings",),
            source="Ada RM pragma Restrictions; enforced by GNAT itself"),
    Profile("fortran-2018", "Fortran 2018 conformance, implicit none", ("fortran",), ("compiler-warnings",),
            compiler_flags={"fortran": ("-std=f2018", "-fimplicit-none", "-Wall", "-Wextra")}),
    Profile("fortran-warnings", "gfortran: every warning as a finding", ("fortran",), ("compiler-warnings",)),
    Profile("cobol-warnings", "GnuCOBOL: every warning as a finding", ("cobol",), ("compiler-warnings",)),
    Profile("shell", "ShellCheck for every shell script", ("*",), ("shellcheck",)),
)}

DEFAULTS: dict[str, tuple[str, ...]] = {
    "c": ("cert-c", "cwe-top25", "shell"), "cpp": ("cert-cpp", "cwe-top25", "shell"),
    "c_arm": ("cert-c", "shell"), "python": ("python-secure", "cwe-top25", "shell"), "ada": ("ada-warnings", "shell"),
    "fortran": ("fortran-warnings", "shell"), "cobol": ("cobol-warnings", "shell"), "ocaml": ("shell",),
}
# A gnat.adc that turns these into compile errors (Ada Reference Manual, pragma Restrictions).
ADA_RESTRICTIONS = ("pragma Restrictions (No_Recursion);\npragma Restrictions (No_Allocators);\n"
                    "pragma Restrictions (No_Implicit_Heap_Allocations);\npragma Restrictions (No_Abort_Statements);\n"
                    "pragma Restrictions (No_Implementation_Pragmas);\n")


def resolve(toolchain: str, chosen: list[str] | None) -> list[Profile]:
    names = list(chosen) if chosen else list(DEFAULTS.get(toolchain, ("shell",)))
    unknown = [n for n in names if n not in PROFILES]
    if unknown:
        raise ValueError(f"unknown standard {unknown}; choose from {sorted(PROFILES)}")
    return [PROFILES[n] for n in names if "*" in PROFILES[n].toolchains or toolchain in PROFILES[n].toolchains]


# FORGE's own mapping of GCC analyzer warnings to the CERT C rule each one is evidence against, for checks
# CERT's tables do not list (they name only three GCC checks). This is FORGE's judgement, labelled as such
# wherever it is used; each CERT rule named here exists in the bundled pack (tests check it).
FORGE_CERT_MAP: dict[tuple[str, str], tuple[str, ...]] = {
    ("gcc", "-Wanalyzer-null-dereference"): ("EXP34-C",),
    ("gcc", "-Wanalyzer-possible-null-dereference"): ("EXP34-C",),
    ("gcc", "-Wanalyzer-null-argument"): ("EXP34-C",),
    ("gcc", "-Wanalyzer-possible-null-argument"): ("EXP34-C",),
    ("gcc", "-Wanalyzer-deref-before-check"): ("EXP34-C",),
    ("gcc", "-Wanalyzer-use-after-free"): ("MEM30-C",),
    ("gcc", "-Wanalyzer-double-free"): ("MEM30-C",),
    ("gcc", "-Wanalyzer-malloc-leak"): ("MEM31-C",),
    ("gcc", "-Wanalyzer-free-of-non-heap"): ("MEM34-C",),
    ("gcc", "-Wanalyzer-mismatching-deallocation"): ("MEM34-C",),
    ("gcc", "-Wanalyzer-use-of-uninitialized-value"): ("EXP33-C",),
    ("gcc", "-Wanalyzer-out-of-bounds"): ("ARR30-C",),
    ("gcc", "-Wanalyzer-tainted-array-index"): ("ARR30-C",),
    ("gcc", "-Wanalyzer-shift-count-negative"): ("INT34-C",),
    ("gcc", "-Wanalyzer-shift-count-overflow"): ("INT34-C",),
    ("gcc", "-Wanalyzer-exposure-through-uninit-copy"): ("DCL39-C",),
    ("gcc", "-Wanalyzer-double-fclose"): ("FIO46-C",),
    ("gcc", "-Wanalyzer-file-leak"): ("FIO42-C",),
    ("gcc", "-Wanalyzer-unsafe-call-within-signal-handler"): ("SIG30-C",),
}


@cache
def _cert_detectors() -> dict[tuple[str, str], tuple[str, ...]]:
    """(tool, checker) -> CERT rules, from CERT's own Automated Detection tables (open-source tools only)."""
    index: dict[tuple[str, str], list[str]] = {}
    for rule_id, rule in knowledge.cert_pack()["rules"].items():
        for detector in rule["detectors"]:
            for checker in detector["checker"].split():          # some cells name two checkers
                if detector["tool"] == "clang":
                    # clang-tidy reports compiler warnings as clang-diagnostic-<name>.
                    name = "clang-diagnostic-" + checker[2:] if checker.startswith("-W") else checker
                    index.setdefault(("clang-tidy", name), []).append(rule_id)
                else:
                    index.setdefault((detector["tool"], checker), []).append(rule_id)
    return {key: tuple(sorted(set(value))) for key, value in index.items()}


def cert_rules(finding: QualityFinding) -> list[str]:
    rules: list[str] = []
    match = re.fullmatch(r"cert-([a-z]{3}\d{2})-(c|cpp)", finding.rule_id)
    if finding.tool == "clang-tidy" and match:
        rules.append(f"{match.group(1).upper()}-{match.group(2).upper()}")
    rules.extend(_cert_detectors().get((finding.tool, finding.rule_id), ()))
    rules.extend(FORGE_CERT_MAP.get((finding.tool, finding.rule_id), ()))
    for cwe in finding.cwe:
        rules.extend(rule for rule, fit in knowledge.cert_for_cwe(cwe) if fit == "Exact")
    return [r for r in dict.fromkeys(rules) if knowledge.cert_rule(r) is not None]


def classify(finding: QualityFinding, profiles: list[Profile]) -> QualityFinding:
    """Standards labels, severity and whether it blocks, under the chosen profiles."""
    names = {p.profile_id for p in profiles}
    certs = cert_rules(finding)
    standards = list(finding.standards)
    standards += [f"CERT {rule}" for rule in certs]
    standards += knowledge.labels(finding.cwe)
    top25 = any(knowledge.in_top25(c) for c in finding.cwe)
    if finding.tool == "clang-tidy" and finding.rule_id.startswith("cppcoreguidelines-"):
        standards.append(f"C++ Core Guidelines ({finding.rule_id})")
    restriction = re.search(r'violation of restriction "([A-Za-z_]+)"', finding.message)
    if finding.tool == "ada" and restriction:
        standards.append(f"Ada pragma Restrictions ({restriction.group(1)})")
    blocking = finding.blocking
    if finding.tool == "power-of-ten":
        blocking = finding.decidable and "power-of-ten" in names
    elif finding.level == "error" and finding.tool in ("ada", "fortran", "cobol", "gcc", "shellcheck"):
        blocking = True
    elif ({"cert-c", "cert-cpp"} & names) and certs and any(r.endswith(("-C", "-CPP")) for r in certs):
        blocking = True
    elif "cwe-top25" in names and top25:
        blocking = True
    elif "python-secure" in names and finding.tool == "ruff" and (finding.rule_id.startswith("E9")
                                                                  or finding.rule_id == "F821"):
        blocking = True
    elif "power-of-ten" in names and finding.tool == "gcc" and finding.level in ("error", "warning"):
        blocking = True                         # P10-10: zero compiler warnings
    if finding.rule_id.startswith("cppcoreguidelines-"):
        blocking = False                        # the C++ Core Guidelines profile is advisory
    severity = "high" if blocking else ("medium" if finding.level in ("error", "warning") else "low")
    return finding.model_copy(update={"standards": list(dict.fromkeys(standards)), "blocking": blocking,
                                      "severity": severity})


def repair_brief(finding: QualityFinding) -> str:
    """What a model needs to fix one finding: the rule broken, why, and (where licensed) the compliant pattern."""
    parts = [f"{finding.path}:{finding.line}: {finding.message} [{finding.rule}]"]
    for cwe in finding.cwe[:2]:
        parts.append(f"  weakness: CWE-{cwe} {knowledge.cwe_name(cwe)}")
    for rule_id in cert_rules(finding)[:1]:
        rule = knowledge.cert_rule(rule_id)
        parts.append(f"  rule: CERT {rule_id} {rule['title']}")
        if rule.get("compliant_example"):
            example = "\n".join("    " + line for line in rule["compliant_example"].splitlines()[:14])
            parts.append(f"  compliant pattern from CERT {rule_id} (SEI CERT, MIT-licensed example):\n{example}")
    if finding.tool == "power-of-ten":
        parts.append("  rule: " + "; ".join(s for s in finding.standards if s.startswith("Power of Ten")))
    return "\n".join(parts)

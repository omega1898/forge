"""FORGE's automated compliance checks, each computed from evidence in the project record.

A check never trusts a claim: it looks for recorded evidence about the exact
snapshot (assessments, analyzer runs, sanitizer results, approvals, inventory,
coverage, traceability) and reports one of:

- `met`: the evidence exists and shows the property, with the evidence ids;
- `not_met`: the evidence exists and shows a problem;
- `not_run`: the evidence that would decide it has not been produced yet
  (the result says which FORGE command produces it);
- `not_applicable`: the property cannot apply to this snapshot, with the reason.

"met" means FORGE's evidence supports that topic for this snapshot. It is not a
certification, and most controls in any standard also need organizational
process evidence that only people can provide (see attestations in report.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["met", "not_met", "not_run", "not_applicable"]
TEST_METHODS = ("black_box_cases", "native_cases", "java_cases", "compiled_cases", "test_suite")
NATIVE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".adb", ".ads", ".f", ".f90", ".for", ".cbl", ".cob")
HARDENING_FLAGS = ("-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-D_FORTIFY_SOURCE=3")


@dataclass
class CheckResult:
    check_id: str
    status: Status
    detail: str
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Check:
    check_id: str
    title: str
    proves: str
    produced_by: str             # how to produce the evidence when it is missing
    run: Callable[[Any, str, dict[str, Any]], CheckResult]


# ------------------------------------------------------------------ helpers


def _evidence(store, snapshot_id: str, observation_type: str, *, like: bool = False) -> list[dict[str, Any]]:  # noqa: ANN001
    """Execution evidence about this exact snapshot, oldest first (the event log's order)."""
    operator = "LIKE ? ESCAPE '\\'" if like else "= ?"
    # Rows are inserted in event-log order (live and on replay), so rowid order is record order;
    # created_at can tie within a millisecond.
    rows = store.conn.execute(
        "SELECT evidence_id, observation_type, observation_json FROM evidence "
        f"WHERE snapshot_id = ? AND observation_type {operator} ORDER BY rowid",
        (snapshot_id, observation_type)).fetchall()
    return [{"evidence_id": row[0], "observation_type": row[1], **json.loads(row[2])} for row in rows]


def _latest_assessments(store, snapshot_id: str) -> dict[str, Any]:  # noqa: ANN001
    latest: dict[str, Any] = {}
    for assessment in store.assessments(snapshot_id=snapshot_id):
        known = latest.get(assessment.criterion_ref.id)
        if known is None or (assessment.recorded_seq or 0) > (known.recorded_seq or 0):
            latest[assessment.criterion_ref.id] = assessment
    return latest


# ------------------------------------------------------------------- checks


def tests_pass(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    latest = _latest_assessments(store, snapshot_id)
    testing = {cid: a for cid, a in latest.items()
               if (c := store.criterion(cid)) is not None and c.assessment_method in TEST_METHODS}
    if not testing:
        return CheckResult("tests_pass", "not_run", "no test criterion has been assessed on this snapshot")
    failing = {cid: a.assessment_result for cid, a in testing.items() if a.assessment_result != "pass"}
    if failing:
        return CheckResult("tests_pass", "not_met", f"not passing: {failing}", [a.assessment_id for a in testing.values()])
    return CheckResult("tests_pass", "met", f"{len(testing)} test criteria pass on this snapshot",
                       [a.assessment_id for a in testing.values()])


def static_analysis(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    runs = _evidence(store, snapshot_id, "analyzer_findings")
    if not runs:
        return CheckResult("static_analysis", "not_run", "no static analysis has been recorded for this snapshot")
    if any(run.get("status") == "error" for run in runs[-1:]):
        return CheckResult("static_analysis", "not_met", "the latest analyzer run failed", [runs[-1]["evidence_id"]])
    if runs[-1].get("missing_tools"):
        return CheckResult("static_analysis", "not_run", "the chosen coding standards need tools that are not "
                           f"installed: {', '.join(runs[-1]['missing_tools'])}", [runs[-1]["evidence_id"]])
    blocking = [f for f in store.findings(snapshot_id=snapshot_id)
                if f.status == "open" and f.severity in ("critical", "high")]
    if blocking:
        return CheckResult("static_analysis", "not_met", f"{len(blocking)} open high or critical findings",
                           [f.finding_id for f in blocking][:20])
    return CheckResult("static_analysis", "met", "analyzed; no open high or critical findings",
                       [r["evidence_id"] for r in runs])


def dynamic_analysis(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    native = _evidence(store, snapshot_id, "native_case_results")
    if native:
        reports = [r for run in native for r in run.get("sanitizer_reports", [])]
        if reports:
            return CheckResult("dynamic_analysis", "not_met", f"{len(reports)} sanitizer reports",
                               [r["evidence_id"] for r in native])
        sanitized = [r for r in native if r.get("sanitizers")]
        if sanitized:
            return CheckResult("dynamic_analysis", "met", "executed under memory and undefined-behavior sanitizers "
                               "with no reports", [r["evidence_id"] for r in sanitized])
    other = [r for kind in ("black_box_case_results", "java_case_results", "compiled_case_results")
             for r in _evidence(store, snapshot_id, kind)]
    if other and not any(r.get("failed_cases") for r in other):
        return CheckResult("dynamic_analysis", "met", "executed against the protected test suite in isolation",
                           [r["evidence_id"] for r in other])
    if other:
        return CheckResult("dynamic_analysis", "not_met", "executions recorded failing cases",
                           [r["evidence_id"] for r in other])
    return CheckResult("dynamic_analysis", "not_run", "no execution evidence for this snapshot")


def flaw_remediation(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    findings = store.findings(snapshot_id=snapshot_id)
    analyzed = bool(_evidence(store, snapshot_id, "analyzer_findings") or
                    _evidence(store, snapshot_id, "native_case_results"))
    if not findings and not analyzed:
        return CheckResult("flaw_remediation", "not_run", "no analysis has looked for flaws in this snapshot")
    open_serious = [f for f in findings if f.status == "open" and f.severity in ("critical", "high")]
    if open_serious:
        return CheckResult("flaw_remediation", "not_met", f"{len(open_serious)} high or critical flaws are open",
                           [f.finding_id for f in open_serious][:20])
    return CheckResult("flaw_remediation", "met", f"{len(findings)} findings recorded, none high or critical open",
                       [f.finding_id for f in findings][:20])


def _applied(store) -> list[dict[str, Any]]:  # noqa: ANN001
    rows = store.conn.execute("SELECT proposal_id, proposal_digest FROM proposals WHERE status = 'applied'").fetchall()
    return [{"proposal_id": r[0], "proposal_digest": r[1]} for r in rows]


def change_control(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    applied = _applied(store)
    if not applied:
        return CheckResult("change_control", "not_run", "no change has been applied through FORGE yet")
    approvals = {d.proposal_digest: d for d in store.decisions(disposition="approved") if d.proposal_digest}
    unapproved = [p["proposal_id"] for p in applied if p["proposal_digest"] not in approvals]
    if unapproved:
        return CheckResult("change_control", "not_met", f"changes applied without a bound approval: {unapproved}")
    return CheckResult("change_control", "met", f"{len(applied)} applied changes, each approved for its exact digest",
                       [approvals[p["proposal_digest"]].decision_id for p in applied])


def code_review(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    result = change_control(store, snapshot_id, options)
    # Decisions are recorded only by the trusted user (the controller enforces it), so an approved,
    # digest-bound decision is a human review of exactly that change.
    return CheckResult("code_review", result.status,
                       result.detail.replace("approved for its exact digest", "reviewed by a person, bound to its digest"),
                       result.evidence)


def component_inventory(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    runs = _evidence(store, snapshot_id, "software_inventory")
    if not runs:
        return CheckResult("component_inventory", "not_run", "no software inventory (SBOM) for this snapshot")
    latest = runs[-1]
    unpinned = latest.get("unpinned", [])
    detail = (f"{latest.get('components', 0)} third-party components and {latest.get('files', 0)} files "
              "inventoried in a CycloneDX SBOM")
    if unpinned:
        detail += f"; {len(unpinned)} not pinned to an exact version (cannot be reproduced or checked): " \
                  f"{unpinned[:5]}"
    return CheckResult("component_inventory", "met", detail, [latest["evidence_id"]])


def no_embedded_secrets(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    runs = _evidence(store, snapshot_id, "secrets_scan")
    if not runs:
        return CheckResult("no_embedded_secrets", "not_run", "the snapshot has not been scanned for secrets")
    latest = runs[-1]
    if latest.get("findings"):
        return CheckResult("no_embedded_secrets", "not_met", f"{len(latest['findings'])} possible secrets in source",
                           [latest["evidence_id"]])
    return CheckResult("no_embedded_secrets", "met", f"{latest.get('files', 0)} files scanned, no secrets found",
                       [latest["evidence_id"]])


def audit_integrity(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    problems = store.verify_projections()
    if problems:
        return CheckResult("audit_integrity", "not_met", f"the record does not replay cleanly: {problems[:3]}")
    count = store.conn.execute("SELECT count(*) FROM events").fetchone()[0]
    return CheckResult("audit_integrity", "met", f"append-only log of {count} events replays to the live record")


def release_integrity(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    builds = _evidence(store, snapshot_id, "package_install") + _evidence(store, snapshot_id, "%\\_build", like=True)
    digests = [r for r in builds if r.get("binary_digest") or r.get("program_digest") or r.get("wheels")
               or r.get("class_digests")]
    if not builds:
        return CheckResult("release_integrity", "not_run", "no build of this snapshot has been recorded")
    if not digests:
        return CheckResult("release_integrity", "not_met", "builds were recorded without artifact digests",
                           [r["evidence_id"] for r in builds])
    return CheckResult("release_integrity", "met", "build outputs are stored by SHA-256 digest with their provenance",
                       [r["evidence_id"] for r in digests])


def structural_coverage(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    runs = _evidence(store, snapshot_id, "structural_coverage")
    criterion = options.get("coverage", "mcdc")
    required = float(options.get("minimum_percent", 100.0))
    if not runs:
        return CheckResult("structural_coverage", "not_run", f"no {criterion} coverage measured for this snapshot")
    latest = runs[-1]
    if latest.get("integrity_problems"):
        return CheckResult("structural_coverage", "not_met", "the coverage counters cannot be trusted: "
                           f"{'; '.join(latest['integrity_problems'][:3])}", [latest["evidence_id"]])
    if f"{criterion}_percent" not in latest:
        return CheckResult("structural_coverage", "not_met", f"{criterion} coverage was not measured",
                           [latest["evidence_id"]])
    measured = float(latest[f"{criterion}_percent"])
    status: Status = "met" if measured >= required else "not_met"
    detail = f"{criterion} coverage {measured:.1f}% (required {required:.1f}%)"
    if status == "not_met" and criterion == "mcdc" and latest.get("uncovered_conditions"):
        shown = ", ".join(dict.fromkeys(f"{c['path']}:{c['line']}" for c in latest["uncovered_conditions"]))[:300]
        detail += f"; uncovered conditions at {shown}"
    return CheckResult("structural_coverage", status, detail, [latest["evidence_id"]])


def requirements_traceability(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    runs = _evidence(store, snapshot_id, "traceability")
    if not runs:
        return CheckResult("requirements_traceability", "not_run", "no requirements trace for this snapshot")
    latest = runs[-1]
    if not latest.get("requirements"):
        return CheckResult("requirements_traceability", "not_run", "no requirements were found: add a requirements "
                           "list, or tag code with 'Implements: ID' and tests with 'Verifies: ID' or forge.toml `verifies`",
                           [latest["evidence_id"]])
    gaps = latest.get("gaps", [])
    if gaps:
        shown = "; ".join(f"{g['requirement']}: {g['problem']}" for g in gaps[:5])
        return CheckResult("requirements_traceability", "not_met", f"{len(gaps)} trace gaps ({shown})",
                           [latest["evidence_id"]])
    return CheckResult("requirements_traceability", "met", f"all {len(latest['requirements'])} requirements are "
                       "implemented by tagged code and verified by tagged tests", [latest["evidence_id"]])


def vulnerability_scan(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    runs = _evidence(store, snapshot_id, "vulnerability_scan")
    if not runs:
        return CheckResult("vulnerability_scan", "not_run",
                           "no known-vulnerability scan; it needs an offline vulnerability database imported first")
    latest = runs[-1]
    affected = latest.get("affected", [])
    database = latest.get("database", "unknown")
    if affected:
        shown = ", ".join(f"{a['component']}@{a['version']} {a['id']}" for a in affected[:5])
        return CheckResult("vulnerability_scan", "not_met", f"{len(affected)} known vulnerabilities ({shown}); "
                           f"database {database}", [latest["evidence_id"]])
    detail = f"no known vulnerabilities in {latest.get('components', 0)} components (database {database})"
    if latest.get("unchecked"):
        detail += f"; {len(latest['unchecked'])} components could not be checked (no exact version)"
    return CheckResult("vulnerability_scan", "met", detail, [latest["evidence_id"]])


def build_hardening(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    builds = _evidence(store, snapshot_id, "native_build")
    if not builds:
        return CheckResult("build_hardening", "not_applicable" if options.get("native") is False else "not_run",
                           "no native build of this snapshot")
    arguments = builds[-1].get("arguments") or []
    present = [flag for flag in HARDENING_FLAGS if flag in arguments]
    missing = [] if present else ["stack protector or fortify source"]
    if "-fPIE" not in arguments and "-fpie" not in arguments and "-pie" not in arguments:
        missing.append("position-independent executable")
    if missing:
        return CheckResult("build_hardening", "not_met", f"native build lacks: {', '.join(missing)}",
                           [builds[-1]["evidence_id"]])
    return CheckResult("build_hardening", "met", "native build uses hardening options", [builds[-1]["evidence_id"]])


def platform_evidence(store, snapshot_id: str, options: dict[str, Any]) -> CheckResult:  # noqa: ANN001
    from forge.platforms import platform_status

    rows = platform_status(store, snapshot_id)
    if not rows:
        return CheckResult("platform_evidence", "not_applicable", "no criterion requires a specific platform")
    unrun = [f"{r.subject} on {r.environment}" for r in rows if r.result != "pass"]
    if unrun:
        return CheckResult("platform_evidence", "not_met", f"not passing natively: {unrun[:5]}")
    return CheckResult("platform_evidence", "met", f"{len(rows)} platform checks pass natively")


CHECKS: dict[str, Check] = {check.check_id: check for check in (
    Check("tests_pass", "Tests pass on the exact snapshot", "every assessed test criterion passes",
          "forge check tests", tests_pass),
    Check("static_analysis", "Static code analysis", "the snapshot was analyzed and no high/critical findings are open",
          "forge check analyze", static_analysis),
    Check("dynamic_analysis", "Dynamic code analysis", "the code ran in isolation against protected tests, under "
          "sanitizers for native code", "forge check tests (C and C++ run under sanitizers)", dynamic_analysis),
    Check("flaw_remediation", "Flaw remediation", "no high or critical flaw is open", "forge check analyze, then fix or resolve high and critical findings",
          flaw_remediation),
    Check("change_control", "Configuration change control", "every applied change carries an approval bound to its "
          "exact digest", "apply changes through FORGE proposals approved for their exact digest", change_control),
    Check("code_review", "Human code review", "every applied change was reviewed and approved by a person",
          "approve each change by its digest (a person's recorded decision)", code_review),
    Check("component_inventory", "Software component inventory (SBOM)", "the snapshot's components are inventoried",
          "forge comply check", component_inventory),
    Check("no_embedded_secrets", "No embedded secrets", "no credentials or keys are embedded in source",
          "forge comply check", no_embedded_secrets),
    Check("audit_integrity", "Audit record integrity", "the append-only event log replays to the live record",
          "(always available)", audit_integrity),
    Check("release_integrity", "Release integrity", "build outputs are stored and identified by cryptographic digest",
          "forge check tests (it builds the program)", release_integrity),
    Check("structural_coverage", "Structural coverage", "the required structural coverage (for example MC/DC) is met",
          "forge check coverage", structural_coverage),
    Check("requirements_traceability", "Requirements traceability", "requirements trace to code and tests with no gaps",
          "tag code 'Implements: ID' and list `verifies` on forge.toml tests, then forge check",
          requirements_traceability),
    Check("vulnerability_scan", "Known-vulnerability scan", "components were checked against a vulnerability database",
          "forge comply import-vulns, then forge comply check", vulnerability_scan),
    Check("build_hardening", "Build hardening", "native builds use hardening options", "build natively with -fstack-protector-strong or -D_FORTIFY_SOURCE, and -fPIE/-pie",
          build_hardening),
    Check("platform_evidence", "Native platform evidence", "every required platform passed natively",
          "assess platform criteria on a host for each required platform", platform_evidence),
)}

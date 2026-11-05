"""Render a ComplianceReport as Markdown a person (or an auditor) can read.

Order matters for ease of use: the gate verdict and what to do next come
first, then each catalog's controls that FORGE checked, then the controls that
need a person's attestation (grouped by family so a 287-control baseline stays
readable), then the industry standards for the domain, then the limits.
"""

from __future__ import annotations

from collections import defaultdict

from forge.compliance.service import ComplianceReport

_MARK = {"met": "met", "not_met": "NOT MET", "not_run": "not run yet", "not_applicable": "n/a",
         "gap": "GAP", "incomplete": "incomplete", "attested": "attested", "needs_attestation": "needs attestation"}


def _cell(text: str, limit: int = 200) -> str:
    flat = " ".join(str(text).split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def next_steps(report: ComplianceReport) -> list[str]:
    """What to run or decide next, from the checks that are not met or not run."""
    steps = []
    for check_id in report.profile.gate_checks:
        check = report.checks.get(check_id)
        if check is None:
            continue
        if check["status"] == "not_run":
            steps.append(f"produce evidence for {check['title'].lower()}: {check['produced_by']}")
        elif check["status"] == "not_met":
            steps.append(f"fix {check['title'].lower()}: {_cell(check['detail'], 160)}")
    pending = sum(section.counts.get("needs_attestation", 0) for section in report.catalogs)
    if pending:
        steps.append(f"{pending} process controls need your attestation: forge comply attest (see the lists below)")
    return steps


def render_markdown(report: ComplianceReport) -> str:
    profile = report.profile
    options = ", ".join(f"{k} {v}" for k, v in profile.options.items()) or "defaults"
    lines = [f"# Compliance report: {profile.title}", "",
             f"Snapshot `{report.snapshot_id}`, generated {report.generated_at}, options: {options}.", ""]
    verdict = {"pass": "PASS", "fail": "FAIL", "incomplete": "INCOMPLETE (evidence missing)",
               "none": "no gate for this domain"}[report.gate_status]
    lines += [f"## Gate: {verdict}", ""]
    for note in profile.notes:
        lines.append(f"- {note}")
    if profile.notes:
        lines.append("")
    if profile.gate_checks:
        lines += ["| Check | Result | Detail |", "|---|---|---|"]
        for check_id in profile.gate_checks:
            check = report.checks[check_id]
            lines.append(f"| {check['title']} | {_MARK[check['status']]} | {_cell(check['detail'])} |")
        lines.append("")
    steps = next_steps(report)
    if steps:
        lines += ["**Next:**", ""] + [f"{n}. {step}" for n, step in enumerate(steps, 1)] + [""]

    for section in report.catalogs:
        counts = section.counts
        lines += [f"## {section.title} (version {section.version})", "",
                  f"Selection: {section.selection}.  ",
                  f"Source: {section.source_url}  ",
                  f"Source digest: `{section.source_sha256}`  ",
                  f"License: {section.license}", "",
                  f"Met {counts['met']} · gap {counts['gap']} · incomplete {counts['incomplete']} · "
                  f"attested {counts['attested']} · needs attestation {counts['needs_attestation']}", ""]
        checked = [c for c in section.controls if c.checks]
        if checked:
            lines += ["### Controls FORGE checks", "", "| Control | Title | Status | Evidence |", "|---|---|---|---|"]
            for control in checked:
                lines.append(f"| {control.label} | {_cell(control.title, 80)} | {_MARK[control.status]} | "
                             f"{_cell('; '.join(control.detail), 300)} |")
            lines.append("")
        attested = [c for c in section.controls if c.attestation]
        if attested:
            lines += ["### Attested by a person", ""]
            for control in attested:
                a = control.attestation or {}
                lines.append(f"- **{control.label}** {control.title}: \"{_cell(a.get('statement', ''), 300)}\" "
                             f"({a.get('reviewer_id')}, {a.get('created_at')}, {a.get('scope')})")
            lines.append("")
        waiting = [c for c in section.controls if c.status == "needs_attestation"]
        if waiting:
            families: dict[str, list[str]] = defaultdict(list)
            for control in waiting:
                families[control.group or "Other"].append(control.label)
            lines += ["### Needs your attestation", "",
                      "FORGE cannot check these from code; they are about how your organization works.", ""]
            for family, labels in families.items():
                lines.append(f"- {family} ({len(labels)}): {', '.join(labels)}")
            lines.append("")

    if report.standards:
        lines += ["## Industry standards for this domain", "",
                  "FORGE names these standards and the evidence that supports their topics. It does not ship "
                  "their text or cite their clause numbers; import your licensed copy to do that.", ""]
        for standard in report.standards:
            lines.append(f"### {standard['name']}")
            lines.append(f"{standard['publisher']}; {standard['access']}: {standard['obtain']}.")
            if standard.get("note"):
                lines.append(f"Note: {standard['note']}")
            for topic, status in standard.get("topic_status", {}).items():
                lines.append(f"- {topic}: {_MARK.get(status, status)}")
            lines.append("")

    lines += ["## Limits", ""] + [f"- {text}" for text in report.limitations]
    return "\n".join(lines) + "\n"

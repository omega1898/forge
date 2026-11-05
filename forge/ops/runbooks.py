"""Operations (ticket D10): deploy checklist, incident timeline, postmortem, doc freshness.

Distilled from the deploy-checklist, incident-response and documentation
skills, with every item bound to evidence in the project record:

- Deploy checklist. Items come from project facts (D05): a project without
  migrations gets no migration items. Each item is satisfied only by
  evidence about the exact release snapshot. "Tests pass" means the latest
  assessment of every listed criterion for that snapshot passes; a pass on
  an earlier snapshot does not count.
- Incident timeline. Entries are sorted by time (UTC), duplicates collapse,
  and gaps longer than a threshold are called out instead of hidden.
- Postmortem. Blameless sections in a fixed order; every action item needs
  an owner, a due date and a link to a finding or decision, or the
  document is refused.
- Documentation freshness. Code names mentioned in documentation (in
  backticks) are checked against the definitions in the snapshot index; a
  document naming a function that no longer exists is flagged.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from forge.contracts.clock import parse_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.knowledge.facts import Fact, fact_value


@dataclass
class ChecklistItem:
    item_id: str
    text: str
    satisfied: bool
    evidence: list[str] = field(default_factory=list)
    reason: str = ""


def deploy_checklist(store, release_snapshot_id: str, facts: list[Fact], *,  # noqa: ANN001 - RecordReader
                     criterion_ids: list[str]) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    # Tests and acceptance criteria: current assessments for THIS snapshot only.
    latest: dict[str, Any] = {}
    for assessment in store.assessments(snapshot_id=release_snapshot_id):
        known = latest.get(assessment.criterion_ref.id)
        if known is None or (assessment.recorded_seq or 0) > (known.recorded_seq or 0):
            latest[assessment.criterion_ref.id] = assessment
    missing = [c for c in criterion_ids if c not in latest]
    failing = [c for c in criterion_ids if c in latest and latest[c].assessment_result != "pass"]
    items.append(ChecklistItem(
        "tests_pass", "Every acceptance criterion passes on the release snapshot",
        not missing and not failing and bool(criterion_ids),
        [latest[c].assessment_id for c in criterion_ids if c in latest],
        "" if criterion_ids and not missing and not failing else
        "; ".join(filter(None, [f"no assessment for this snapshot: {missing}" if missing else "",
                                f"not passing: {failing}" if failing else "",
                                "no criteria listed" if not criterion_ids else ""]))))
    blocking = [f for f in store.findings(snapshot_id=release_snapshot_id)
                if f.status == "open" and f.severity in ("critical", "high")]
    items.append(ChecklistItem("no_blocking_findings", "No open critical or high findings on the release snapshot",
                               not blocking, [f.finding_id for f in blocking],
                               f"{len(blocking)} open: {[f.rule_id for f in blocking][:5]}" if blocking else ""))
    if fact_value(facts, "data.migrations"):
        decisions = [d for d in store.decisions() if d.disposition == "approved"
                     and "migration" in (d.question or "").lower()]
        items.append(ChecklistItem("migration_reviewed", "Database migrations reviewed and a rollback path approved",
                                   bool(decisions), [d.decision_id for d in decisions],
                                   "" if decisions else "no approved decision about the migration"))
    if fact_value(facts, "release.feature_flags"):
        items.append(ChecklistItem("flags_default_off", "New behavior ships behind a flag that defaults off",
                                   False, [], "needs a reviewer's confirmation; FORGE cannot infer flag defaults"))
    if fact_value(facts, "deploy.container"):
        items.append(ChecklistItem("image_from_snapshot", "The container image is built from the release snapshot",
                                   False, [], "needs a build record naming the snapshot digest"))
    return items


def render_checklist(items: list[ChecklistItem]) -> str:
    lines = ["| Item | Status | Evidence | Reason |", "|---|---|---|---|"]
    for item in items:
        lines.append(f"| {_cell(item.text)} | {'satisfied' if item.satisfied else '**open**'} | "
                     f"{_cell(', '.join(item.evidence) or '-')} | {_cell(item.reason or '-')} |")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return html.escape(text, quote=False).replace("|", "\\|").replace("\n", " ")


# ------------------------------------------------------------------ incidents


@dataclass(frozen=True)
class TimelineEntry:
    at: datetime
    source: str
    text: str


def incident_timeline(entries: list[dict[str, str]], *, gap_minutes: int = 30) -> list[TimelineEntry | str]:
    """Sorted, de-duplicated entries; long silences appear as explicit gap markers."""
    parsed = sorted({TimelineEntry(parse_timestamp(e["at"]), e["source"], e["text"].strip()) for e in entries},
                    key=lambda e: (e.at, e.source, e.text))
    timeline: list[TimelineEntry | str] = []
    for previous, current in zip([None, *parsed], parsed, strict=False):
        if previous is not None and current.at - previous.at > timedelta(minutes=gap_minutes):
            minutes = int((current.at - previous.at).total_seconds() // 60)
            timeline.append(f"gap: no recorded activity for {minutes} minutes")
        timeline.append(current)
    return timeline


POSTMORTEM_SECTIONS = ("summary", "impact", "timeline", "root_causes", "contributing_factors", "what_went_well",
                       "action_items")
_BLAME = re.compile(r"\b(fault of|blame[sd]?|careless|incompetent|should have known|human error)\b", re.I)


def render_postmortem(title: str, sections: dict[str, Any], timeline: list[TimelineEntry | str]) -> str:
    missing = [name for name in POSTMORTEM_SECTIONS if name not in sections and name != "timeline"]
    if missing:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"postmortem is missing sections: {missing}")
    problems = []
    for index, action in enumerate(sections["action_items"]):
        for key in ("text", "owner", "due", "link"):
            if not action.get(key):
                problems.append(f"action item {index + 1} has no {key}")
    prose = " ".join(str(sections[name]) for name in POSTMORTEM_SECTIONS if name in sections)
    if _BLAME.search(prose):
        problems.append(f"blameless format: rephrase {_BLAME.search(prose).group(0)!r} as a system condition")
    if problems:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "postmortem refused", details={"problems": problems})
    out = [f"# Postmortem: {_cell(title)}", ""]
    for name in POSTMORTEM_SECTIONS:
        out.append(f"## {name.replace('_', ' ').capitalize()}")
        out.append("")
        if name == "timeline":
            for entry in timeline:
                out.append(f"- {_cell(entry)}" if isinstance(entry, str) else
                           f"- {entry.at.strftime('%Y-%m-%d %H:%M')}Z [{_cell(entry.source)}] {_cell(entry.text)}")
        elif name == "action_items":
            out.append("| Action | Owner | Due | Link |")
            out.append("|---|---|---|---|")
            for action in sections[name]:
                out.append(f"| {_cell(action['text'])} | {_cell(action['owner'])} | {_cell(action['due'])} | "
                           f"{_cell(action['link'])} |")
        else:
            value = sections[name]
            out += [f"- {_cell(str(v))}" for v in value] if isinstance(value, list) else [_cell(str(value))]
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------ doc freshness

_CODE_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(\(\))?`")


def stale_references(documents: dict[str, str], defined: set[str], *, files: set[str] = frozenset()) -> list[dict]:
    """Backticked names that look like code and are not defined in the snapshot.

    A name counts as code if it has call parentheses or a dot; a dotted name
    is checked by its last part. Backticked paths are checked against files.
    """
    found = []
    for path, text in sorted(documents.items()):
        for number, line in enumerate(text.splitlines(), 1):
            for match in re.finditer(r"`([^`\s]+/[^`\s]+)`", line):
                candidate = match.group(1)
                if "." in candidate.rsplit("/", 1)[-1] and candidate not in files:
                    found.append({"document": path, "line": number, "reference": candidate,
                                  "reason": "file does not exist"})
            for match in _CODE_NAME.finditer(line):
                name, call = match.group(1), match.group(2)
                if not call and "." not in name:
                    continue                  # a bare word in backticks is usually not a code reference
                last = name.rsplit(".", 1)[-1]
                if last not in defined and name not in defined:
                    found.append({"document": path, "line": number, "reference": name + (call or ""),
                                  "reason": "not defined in the current snapshot"})
    return found

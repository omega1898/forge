"""One finding model for every checker, read from and written to SARIF 2.1.0.

Tools disagree on formats, rule names and paths. FORGE normalizes each
result to a `QualityFinding`:

- the path is relative to the snapshot root;
- the rule is namespaced by tool (`gcc:-Wanalyzer-null-dereference`,
  `clang-tidy:cert-err33-c`, `cppcheck:nullPointer`, `ruff:S602`);
- CWE IDs are collected from every place tools put them (SARIF taxa and
  relationships, `external/cwe/cwe-N` tags, `properties.cwe`, "CWE-N" in the
  message) and kept only if the weakness exists in the bundled CWE 5.0;
- the line is identified by GitHub's `primaryLocationLineHash`, so the same
  finding is recognized after unrelated edits.

`merge` folds findings from several tools into one per weakness and place,
keeping every reporting tool as provenance, and `to_sarif` writes one run in
a fixed order with name-based UUIDs: the same inputs always give the same
bytes.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.digests import digest_json
from forge.quality import knowledge
from forge.quality.fingerprint import line_hashes

Level = Literal["error", "warning", "note"]
Severity = Literal["critical", "high", "medium", "low", "info"]
_CWE_TEXT = re.compile(r"\bCWE[-_ ]?(\d{1,4})\b", re.I)
_NAMESPACE = uuid.UUID("6b1f3c1e-6f0e-5a4e-9c35-2a2f6c1d0f00")


class QualityFinding(StrictModel):
    tool: str
    rule_id: str
    level: Level
    message: str = Field(max_length=2000)
    path: str
    line: int = Field(ge=1)
    column: int = Field(default=1, ge=1)
    cwe: list[int] = Field(default_factory=list)
    standards: list[str] = Field(default_factory=list)   # e.g. "CERT EXP34-C", "CWE Top 25 (2025)"
    severity: Severity = "medium"
    blocking: bool = False
    decidable: bool = True                               # False: the rule needs a person's judgement
    line_hash: str | None = None
    snippet: str = Field(default="", max_length=200)        # the flagged line, whitespace-normalized
    also_reported_by: list[str] = Field(default_factory=list)

    @property
    def rule(self) -> str:
        return f"{self.tool}:{self.rule_id}"

    def identity(self) -> str:
        """Same weakness at the same code, whichever tool saw it."""
        anchor = self.line_hash or f"line {self.line}"
        what = sorted(self.cwe) if self.cwe else [self.rule]
        return digest_json({"path": self.path, "anchor": anchor, "what": what})


def cwes_in(*values: Any) -> list[int]:  # noqa: ANN401 - any JSON-ish value
    """CWE IDs mentioned anywhere in the given values, keeping only weaknesses CWE 5.0 defines."""
    found: set[int] = set()

    def visit(value: Any) -> None:  # noqa: ANN401
        if isinstance(value, str):
            found.update(int(n) for n in _CWE_TEXT.findall(value))
            found.update(int(n) for n in re.findall(r"external/cwe/cwe-(\d+)", value, re.I))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, int) and not isinstance(value, bool):
            found.add(value)

    for value in values:
        visit(value)
    return sorted(c for c in found if knowledge.weakness(c) is not None)


def taxa_cwes(references: Any) -> list[int]:  # noqa: ANN401
    """SARIF taxonomy references (result.taxa, rule.relationships[].target) that point into CWE."""
    found = []
    for reference in references if isinstance(references, list) else []:
        target = reference.get("target", reference) if isinstance(reference, dict) else {}
        component = str((target.get("toolComponent") or {}).get("name", "")).lower()
        identifier = str(target.get("id", ""))
        if component == "cwe" and identifier.isdigit():
            found.append(int(identifier))
    return cwes_in(*found)


def relative_path(uri: str, roots: tuple[str, ...] = ("/scratch/build/", "/input/", "./")) -> str:
    path = re.sub(r"^file://", "", uri)
    for root in roots:
        if path.startswith(root):
            path = path[len(root):]
    return path.lstrip("/") if not path.startswith("..") else path


def from_sarif(document: dict[str, Any], tool: str | None = None) -> list[QualityFinding]:
    """Read every result of every run. Rule metadata (tags, taxa, properties) contributes CWE IDs."""
    findings = []
    for run in document.get("runs", []):
        driver = run.get("tool", {}).get("driver", {})
        name = tool or str(driver.get("name", "tool")).lower()
        rules = {r.get("id"): r for r in driver.get("rules", []) if isinstance(r, dict)}
        for result in run.get("results", []):
            rule_id = str(result.get("ruleId") or result.get("rule", {}).get("id") or "unknown")
            locations = result.get("locations") or []
            physical = locations[0].get("physicalLocation", {}) if locations else {}
            region = physical.get("region", {})
            uri = physical.get("artifactLocation", {}).get("uri", "")
            if not uri:
                continue
            message = result.get("message", {})
            text = message.get("text") or message.get("markdown") or rule_id
            rule = rules.get(rule_id, {})
            cwe = sorted(set(taxa_cwes(result.get("taxa")) + taxa_cwes(rule.get("relationships")) + cwes_in(
                result.get("properties", {}).get("cwe"), rule.get("properties", {}).get("tags"),
                rule.get("properties", {}).get("cwe"), text)))
            level = result.get("level") or rule.get("defaultConfiguration", {}).get("level") or "warning"
            findings.append(QualityFinding(
                tool=name, rule_id=rule_id[:200], level=level if level in ("error", "warning", "note") else "note",
                message=str(text)[:2000], path=relative_path(uri), line=max(1, int(region.get("startLine", 1))),
                column=max(1, int(region.get("startColumn", 1))), cwe=cwe))
    return findings


def anchor(findings: list[QualityFinding], files: dict[str, bytes]) -> list[QualityFinding]:
    """Attach GitHub-compatible line hashes from the snapshot's own text."""
    cache: dict[str, tuple[list[str], list[str]]] = {}
    out = []
    for finding in findings:
        if finding.path in files and finding.path not in cache:
            text = files[finding.path].decode("utf-8", "replace")
            cache[finding.path] = (line_hashes(text), text.splitlines())
        hashes, lines = cache.get(finding.path, ([], []))
        line_hash = hashes[finding.line - 1] if 1 <= finding.line <= len(hashes) else None
        snippet = " ".join(lines[finding.line - 1].split())[:200] if 1 <= finding.line <= len(lines) else ""
        out.append(finding.model_copy(update={"line_hash": line_hash, "snippet": snippet}))
    return out


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_LEVEL_ORDER = {"error": 0, "warning": 1, "note": 2}


def merge(findings: list[QualityFinding]) -> list[QualityFinding]:
    """One finding per weakness and place. The most severe report leads; the others become provenance."""
    groups: dict[str, list[QualityFinding]] = {}
    for finding in findings:
        groups.setdefault(finding.identity(), []).append(finding)
    merged = []
    for group in groups.values():
        group.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], _LEVEL_ORDER[f.level], not f.blocking, f.rule))
        lead = group[0]
        others = sorted({f.rule for f in group[1:]} - {lead.rule})
        merged.append(lead.model_copy(update={
            "also_reported_by": others, "blocking": any(f.blocking for f in group),
            "standards": list(dict.fromkeys(s for f in group for s in f.standards)),
            "cwe": sorted({c for f in group for c in f.cwe})}))
    return sorted(merged, key=lambda f: (f.path, f.line, f.column, f.rule))


def to_sarif(findings: list[QualityFinding], *, tools: dict[str, str]) -> dict[str, Any]:
    """One SARIF 2.1.0 run for all tools (pass merged findings). `tools` maps tool name to version/digest."""
    rules: dict[str, dict[str, Any]] = {}
    for finding in sorted(findings, key=lambda f: f.rule):
        entry = rules.setdefault(finding.rule, {
            "id": finding.rule, "shortDescription": {"text": finding.rule_id},
            "properties": {"tags": sorted({f"external/cwe/cwe-{c}" for c in finding.cwe}
                                          | set(finding.standards))}})
        entry["properties"]["tags"] = sorted(set(entry["properties"]["tags"]) | set(finding.standards)
                                             | {f"external/cwe/cwe-{c}" for c in finding.cwe})
    index = {rule: n for n, rule in enumerate(sorted(rules))}
    results = []
    for finding in sorted(findings, key=lambda f: (f.path, f.line, f.column, f.rule)):
        result: dict[str, Any] = {
            "ruleId": finding.rule, "ruleIndex": index[finding.rule], "level": finding.level,
            "message": {"text": finding.message},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": finding.path},
                                                "region": {"startLine": finding.line,
                                                           "startColumn": finding.column}}}],
            "guid": str(uuid.uuid5(_NAMESPACE, finding.identity())),
            "properties": {"severity": finding.severity, "blocking": finding.blocking,
                           "decidable": finding.decidable, "cwe": finding.cwe, "standards": finding.standards,
                           "alsoReportedBy": finding.also_reported_by}}
        if finding.line_hash:
            result["partialFingerprints"] = {"primaryLocationLineHash": finding.line_hash}
        results.append(result)
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "FORGE quality", "informationUri": "https://github.com/omega1898/forge",
                                          "rules": [rules[r] for r in sorted(rules)]}},
                      "properties": {"tools": dict(sorted(tools.items())), "notices": knowledge.notices()},
                      "results": results}]}


def dumps(document: dict[str, Any]) -> bytes:
    """Canonical bytes: identical findings give identical files."""
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

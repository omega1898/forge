"""The quality gate inside the assistant loop: the harness patterns from SWE-agent and Aider, as code.

SWE-agent runs a linter before and after every edit and reverts edits that add
errors; analyzer feedback loops cut security findings in model-written code
from over 40% to 13% (see reports/Open coding standards and checkers.md).
FORGE does the same, with the project's own coding standards:

1. the base snapshot is scanned once (its findings are the baseline);
2. each candidate the model produces is scanned with the same profiles;
3. a candidate that adds a blocking finding is rejected, whatever its tests
   say, and the model receives one repair brief per new finding: file and
   line, the rule broken (CERT, CWE, Power of Ten), and, where the licence
   allows, CERT's compliant example.

"New" means not in the baseline by GitHub's line fingerprint, nor by the same
rule on the same code text, so an unrelated edit nearby does not turn an old
finding into a new one. Old findings are never blamed on the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forge.quality.findings import QualityFinding
from forge.quality.registry import repair_brief

MAX_BRIEFS = 5


@dataclass
class GateResult:
    passed: bool
    new: list[QualityFinding] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    status: str = "ok"                       # "error" when the checkers could not run cleanly


def _keys(finding: QualityFinding) -> set[tuple[str, ...]]:
    keys = {("id", finding.identity())}
    if finding.snippet:
        keys.add(("text", finding.rule, finding.path, finding.snippet))
    return keys


class QualityGate:
    def __init__(self, scanner, build: dict[str, Any], standards: list[str] | None) -> None:  # noqa: ANN001
        """`scanner` has scan(snapshot_id, build, standards) -> ScanResult (forge.quality.runner.QualityRunner)."""
        self.scanner, self.build, self.standards = scanner, build, standards
        self._baselines: dict[str, set[tuple[str, ...]]] = {}

    def baseline(self, base_snapshot_id: str) -> set[tuple[str, ...]]:
        if base_snapshot_id not in self._baselines:
            scan = self.scanner.scan(base_snapshot_id, self.build, self.standards)
            self._baselines[base_snapshot_id] = {key for f in scan.findings for key in _keys(f)}
        return self._baselines[base_snapshot_id]

    def review(self, base_snapshot_id: str, candidate_snapshot_id: str) -> GateResult:
        known = self.baseline(base_snapshot_id)
        scan = self.scanner.scan(candidate_snapshot_id, self.build, self.standards)
        if scan.status == "error":
            problems = "; ".join(r.get("problem", "") for r in scan.runs if r.get("problem"))
            return GateResult(passed=False, status="error",
                              feedback=[f"the code checkers could not analyze this candidate: {problems[:300]}"])
        new = [f for f in scan.findings if f.blocking and not (_keys(f) & known)]
        feedback = [f"new {', '.join(f.standards[:2]) or f.rule} violation:\n{repair_brief(f)}"
                    for f in new[:MAX_BRIEFS]]
        if len(new) > MAX_BRIEFS:
            feedback.append(f"{len(new) - MAX_BRIEFS} more new violations not shown; fix these first")
        return GateResult(passed=not new, new=new, feedback=feedback)

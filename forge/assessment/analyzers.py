"""Static analyzers as evidence (ticket D02).

Each checklist item of the code-review skill that an existing offline tool
can check becomes a rule: the tool runs inside the sandbox against the
snapshot, its output is normalized into SARIF-shaped findings, and a fixed
table assigns dimension and severity. The verdict is a function of the
findings, never a model's opinion.

An analyzer that crashes or prints unparseable output produces an `error`
run, never an empty "no findings, all good".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from forge.authority.authorize import ResourceLimits
from forge.contracts.base import StrictModel
from forge.contracts.digests import Digest, digest_json
from forge.contracts.paths import check_relative_path
from forge.sandbox.base import SandboxBackend, SandboxSpec
from forge.tools.registry import ToolDefinition

Severity = Literal["critical", "high", "medium", "low", "info"]
Dimension = Literal["security", "correctness", "performance", "maintainability"]

# Ruff rule families selected for review: syntax errors (E9), pyflakes (F),
# bugbear (B) and the bandit-derived security rules (S).
RUFF_SELECT = "E9,F,B,S"

# Longest matching prefix wins. Data, not prose: reviewed and versioned.
RULE_TABLE: tuple[tuple[str, Dimension, Severity], ...] = (
    ("E9", "correctness", "critical"),     # the file does not parse
    ("F821", "correctness", "high"),       # undefined name
    ("F811", "correctness", "medium"),     # redefinition of unused name
    ("F401", "maintainability", "low"),    # unused import
    ("F841", "maintainability", "low"),    # unused local variable
    ("F", "correctness", "medium"),
    ("B", "correctness", "medium"),
    ("S101", "security", "info"),          # assert used (stripped under -O)
    ("S102", "security", "high"),          # exec
    ("S105", "security", "high"),          # hard-coded password string
    ("S106", "security", "high"),
    ("S107", "security", "high"),
    ("S301", "security", "high"),          # pickle on untrusted data
    ("S307", "security", "high"),          # eval
    ("S602", "security", "high"),          # subprocess with shell=True
    ("S603", "security", "low"),           # subprocess without shell
    ("S607", "security", "low"),           # partial executable path
    ("S608", "security", "high"),          # SQL built from strings
    ("S", "security", "medium"),
)
RULE_TABLE_REVISION = 1


def classify(rule_id: str) -> tuple[Dimension, Severity]:
    best: tuple[str, Dimension, Severity] | None = None
    for prefix, dimension, severity in RULE_TABLE:
        if rule_id.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, dimension, severity)
    return (best[1], best[2]) if best else ("maintainability", "low")


class NormalizedFinding(StrictModel):
    """One analyzer result, shaped after SARIF's result object."""

    rule_id: str
    level: Literal["error", "warning", "note"]
    message: str = Field(max_length=2000)
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    dimension: Dimension
    severity: Severity
    fingerprint: Digest


class AnalyzerRun(StrictModel):
    tool_id: str
    tool_digest: Digest
    status: Literal["ok", "error"]
    error: str | None = None
    findings: list[NormalizedFinding]
    rule_table_revision: int = RULE_TABLE_REVISION


_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
MAX_FINDINGS = 5000


def run_ruff(
    snapshot_dir: Path, backend: SandboxBackend, work_dir: Path, tool: ToolDefinition, limits: ResourceLimits
) -> AnalyzerRun:
    spec = SandboxSpec(
        job_key="analyzer_ruff",
        input_dir=str(snapshot_dir),
        # --isolated: ignore any configuration in the (untrusted) project,
        # which could otherwise switch rules off or change the output.
        argv=[tool.executable, "check", "--isolated", "--no-cache", "--output-format", "json",
              "--exit-zero", "--select", RUFF_SELECT, "."],
        cwd="/input",
        limits=limits,
        toolchain_paths=tool.toolchain_paths,
    )
    observation = backend.run(spec, work_dir)
    try:
        def failed(reason: str) -> AnalyzerRun:
            return AnalyzerRun(
                tool_id=tool.tool_id, tool_digest=tool.executable_digest, status="error", error=reason, findings=[]
            )

        if observation.terminated_by != "exited" or observation.exit_code != 0:
            return failed(f"analyzer did not complete: {observation.terminated_by}, exit {observation.exit_code}")
        if observation.stdout_truncated:
            return failed("analyzer output exceeded the limit")
        try:
            items = json.loads(observation.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return failed("analyzer output is not valid JSON")
        if not isinstance(items, list) or len(items) > MAX_FINDINGS:
            return failed("analyzer output has an unexpected shape or size")
        findings: dict[str, NormalizedFinding] = {}
        for item in items:
            normalized = _normalize(item, snapshot_dir)
            if normalized is None:
                return failed("analyzer reported a location outside the snapshot")
            findings.setdefault(normalized.fingerprint, normalized)  # duplicates collapse
        return AnalyzerRun(
            tool_id=tool.tool_id,
            tool_digest=tool.executable_digest,
            status="ok",
            findings=sorted(findings.values(), key=lambda f: (f.path, f.line, f.rule_id)),
        )
    finally:
        destroy = getattr(backend, "destroy", None)
        if destroy is not None:
            destroy(work_dir)


def _normalize(item: object, snapshot_dir: Path) -> NormalizedFinding | None:
    if not isinstance(item, dict):
        return None
    filename = str(item.get("filename", ""))
    if not filename.startswith("/input/"):
        return None
    path = filename.removeprefix("/input/")
    try:
        check_relative_path(path)
    except ValueError:
        return None
    rule_id = str(item.get("code") or "E999")
    location = item.get("location") or {}
    line = max(1, int(location.get("row") or 1))
    column = max(1, int(location.get("column") or 1))
    dimension, severity = classify(rule_id)
    return NormalizedFinding(
        rule_id=rule_id,
        level=_LEVEL[severity],
        message=str(item.get("message", ""))[:2000],
        path=path,
        line=line,
        column=column,
        dimension=dimension,
        severity=severity,
        fingerprint=fingerprint(rule_id, path, _line_text(snapshot_dir, path, line)),
    )


def fingerprint(rule_id: str, path: str, line_text: str) -> str:
    """Stable identity for "the same issue": survives the line moving up or down."""
    return digest_json({"rule": rule_id, "path": path, "text": " ".join(line_text.split())})


def _line_text(snapshot_dir: Path, path: str, line: int) -> str:
    try:
        with open(Path(snapshot_dir).joinpath(*path.split("/")), encoding="utf-8", errors="replace") as handle:
            for number, text in enumerate(handle, start=1):
                if number == line:
                    return text
    except OSError:
        pass
    return ""


Verdict = Literal["approve", "request_changes", "needs_discussion"]


def verdict(findings: list[NormalizedFinding], *, waived: set[str] = frozenset(), confirmed: set[str] = frozenset()) -> Verdict:
    """Deterministic review verdict.

    - any unwaived critical finding: request changes;
    - an unwaived high finding: request changes if confirmed (a reproducer
      exists), otherwise needs discussion;
    - otherwise approve.
    """
    open_findings = [item for item in findings if item.fingerprint not in waived]
    if any(item.severity == "critical" for item in open_findings):
        return "request_changes"
    highs = [item for item in open_findings if item.severity == "high"]
    if any(item.fingerprint in confirmed for item in highs):
        return "request_changes"
    if highs:
        return "needs_discussion"
    return "approve"

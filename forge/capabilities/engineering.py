"""Engineering skills as capabilities (ticket D06).

Each capability is a function that returns a validated plan template (a
task graph FORGE's engine admits) plus the deterministic analysis the skill
used to ask a frontier model to do by reading prose:

    review          analyzer findings -> deterministic verdict -> report
    bug_fix         failure signature normalization; reproduce -> fix -> regress
    test_strategy   component classification -> test types; boundary inputs
    tech_debt       detectors per category; priority = (impact + risk) x (6 - effort)

See docs/design/skill-distillation.md section 5.1 for the mapping.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import Field

from forge.artifacts.blobs import BlobStore
from forge.contracts.base import StrictModel
from forge.workspace.snapshot import Manifest

BUDGET = {"max_wall_ms": 3_600_000, "max_attempts": 3}


def _task(key: str, task_type: str, title: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "task_type": task_type, "title": title, "budget": BUDGET, **extra}


# ------------------------------------------------------------------ review --


def review_plan(analyzer_criterion: dict[str, Any]) -> list[dict[str, Any]]:
    """analyze (assess) -> triage findings (decide)."""
    return [
        _task("analyze", "assess", "Run admitted analyzers on the snapshot", criterion_refs=[analyzer_criterion]),
        _task("triage", "decide", "Decide on high-severity findings", depends_on=["analyze"]),
    ]


def review_report(findings: list[Any], verdict: str) -> str:
    """The code-review skill's output table, filled from records (no model)."""
    lines = ["## Code review", "", f"Verdict: **{verdict.replace('_', ' ')}** (computed from the findings below)", ""]
    blocking = [f for f in findings if f.severity in ("critical", "high")]
    other = [f for f in findings if f.severity not in ("critical", "high")]
    for title, rows in (("Blocking issues", blocking), ("Suggestions", other)):
        lines += [f"### {title}", "", "| # | File | Line | Rule | Severity | Issue |", "|---|---|---|---|---|---|"]
        lines += [f"| {n} | {f.path} | {f.line} | {f.rule_id} | {f.severity} | {f.message.replace('|', '/')[:120]} |"
                  for n, f in enumerate(rows, 1)] or ["| - | - | - | - | - | none |"]
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------- bug fix --

_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_TEMP_PATH = re.compile(r"(/tmp|/scratch|/var/folders)/[^\s'\",)]+")
_NUMBER = re.compile(r"\b\d+\b")


class FailureSignature(StrictModel):
    exception_type: str
    frames: list[str]           # "path:function", innermost last, line numbers removed
    message: str                # volatile parts (addresses, temp paths, numbers) normalized

    def key(self) -> str:
        return hashlib.sha256(f"{self.exception_type}|{'>'.join(self.frames)}|{self.message}".encode()).hexdigest()[:16]


def failure_signature(stderr: str) -> FailureSignature | None:
    """Normalize a Python traceback so the same bug gives the same signature.

    Line numbers, memory addresses, temporary paths and numbers vary between
    runs and edits; removing them lets FORGE tell "the same failure again"
    (no progress) from "a different failure" (progress or regression).
    """
    frames = re.findall(r'File "([^"]+)", line \d+, in (\S+)', stderr)
    last = [line for line in stderr.strip().splitlines() if line and not line.startswith((" ", "Traceback"))]
    if not last:
        return None
    exception_type, _, message = last[-1].partition(":")
    message = _NUMBER.sub("N", _TEMP_PATH.sub("<tmp>", _ADDRESS.sub("<addr>", message.strip())))
    return FailureSignature(
        exception_type=exception_type.strip(),
        frames=[f"{_TEMP_PATH.sub('<tmp>', path)}:{function}" for path, function in frames][-5:],
        message=message[:300],
    )


def bug_fix_plan(regression_criterion: dict[str, Any]) -> list[dict[str, Any]]:
    """reproduce (must fail first) -> fix -> regression check (must pass after)."""
    return [
        _task("reproduce", "assess", "Reproduce the reported failure on the current snapshot",
              criterion_refs=[regression_criterion]),
        _task("fix", "prepare", "Propose a fix as a candidate", depends_on=["reproduce"]),
        _task("regress", "assess", "Confirm the reproduction now passes and nothing else broke",
              criterion_refs=[regression_criterion], depends_on=["fix"]),
    ]


# ----------------------------------------------------------- test strategy --

ComponentType = Literal["cli", "library", "api_endpoint", "data_pipeline", "frontend", "infrastructure"]

STRATEGY_TABLE: dict[str, list[str]] = {
    # The testing-strategy skill's "strategy by component type", as data.
    "cli": ["black-box cases per documented input class", "exit-code and stderr contract", "boundary inputs"],
    "library": ["unit tests for business logic", "property-based tests for declared invariants"],
    "api_endpoint": ["unit tests for business logic", "integration tests for the HTTP layer",
                     "contract tests for consumers"],
    "data_pipeline": ["input validation", "transformation correctness", "idempotency tests"],
    "frontend": ["component tests", "interaction tests", "visual regression", "accessibility"],
    "infrastructure": ["smoke tests", "load tests"],
}


def classify_components(paths: list[str], facts: list[Any]) -> dict[str, ComponentType]:
    names = {fact.name for fact in facts}
    result: dict[str, ComponentType] = {}
    for path in paths:
        if not path.endswith(".py") or "/tests/" in f"/{path}" or PurePosixPath(path).name.startswith("test_"):
            continue
        name = PurePosixPath(path).name
        if name in ("cli.py", "__main__.py") or "entry_points.console_scripts" in names and name == "main.py":
            result[path] = "cli"
        elif any(part in path for part in ("api/", "routes", "views")):
            result[path] = "api_endpoint"
        elif any(part in path for part in ("pipeline", "etl", "transform")):
            result[path] = "data_pipeline"
        else:
            result[path] = "library"
    return result


def length_prefixed_boundaries(*, length_bytes: int = 2, max_length: int | None = None) -> dict[str, bytes]:
    """Boundary inputs for a type byte + big-endian length + payload format.

    These are the cases the blueprint's packet-reader specification names:
    empty, partial header, zero length, complete, truncated payload, several
    packets, and the largest allowed length.
    """
    header = 1 + length_bytes

    def packet(kind: int, payload: bytes) -> bytes:
        return bytes([kind]) + len(payload).to_bytes(length_bytes, "big") + payload

    largest = max_length if max_length is not None else (1 << (8 * length_bytes)) - 1
    return {
        "empty_file": b"",
        "partial_header": bytes([1]) + b"\x00" * (header - 2),
        "zero_length_payload": packet(7, b""),
        "one_packet": packet(1, b"hi"),
        "truncated_payload": bytes([1]) + (5).to_bytes(length_bytes, "big") + b"ab",
        "several_packets": packet(1, b"a") + packet(2, b"bc") + packet(3, b""),
        "maximum_length": packet(9, b"x" * min(largest, 65_535)),
    }


# --------------------------------------------------------------- tech debt --


class DebtItem(StrictModel):
    category: Literal["code", "test", "dependency", "documentation", "infrastructure"]
    title: str
    evidence: list[str]
    impact: int = Field(ge=1, le=5)
    risk: int = Field(ge=1, le=5)
    effort: int = Field(ge=1, le=5)

    @property
    def priority(self) -> int:
        # The tech-debt skill's formula, verbatim: (Impact + Risk) x (6 - Effort).
        return (self.impact + self.risk) * (6 - self.effort)


def detect_debt(manifest: Manifest, blobs: BlobStore, *, window: int = 6, long_function: int = 60) -> list[DebtItem]:
    paths = [entry.path for entry in manifest.entries]
    by_path = {entry.path: entry for entry in manifest.entries}
    texts = {p: blobs.read_bytes(by_path[p].digest, max_bytes=2 * 1024 * 1024).decode("utf-8", "replace")
             for p in paths if p.endswith(".py") and by_path[p].size <= 2 * 1024 * 1024}
    items: list[DebtItem] = []

    # Code debt: duplicated windows of normalized lines.
    windows: dict[str, list[str]] = defaultdict(list)
    for path, text in texts.items():
        lines = [" ".join(line.split()) for line in text.splitlines()]
        for start in range(0, max(0, len(lines) - window + 1)):
            chunk = lines[start:start + window]
            if sum(1 for line in chunk if line and not line.startswith("#")) == window:
                windows[hashlib.sha256("\n".join(chunk).encode()).hexdigest()].append(f"{path}:{start + 1}")
    # A window seen at two or more places is duplicated code.
    duplicates = sorted({tuple(sorted(set(spots))) for spots in windows.values() if len(set(spots)) > 1})
    for spots in duplicates[:20]:
        items.append(DebtItem(category="code", title=f"duplicated {window}-line block", evidence=list(spots)[:4],
                              impact=2, risk=2, effort=2))
    # Code debt: very long functions.
    for path, text in texts.items():
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > long_function:
                    items.append(DebtItem(category="code", title=f"{node.name} is {length} lines long",
                                          evidence=[f"{path}:{node.lineno}"], impact=2, risk=3, effort=3))
    # Code debt: deferred work markers.
    for path, text in texts.items():
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"#\s*(TODO|FIXME|XXX|HACK)\b", line):
                items.append(DebtItem(category="code", title=line.strip()[:100], evidence=[f"{path}:{number}"],
                                      impact=1, risk=2, effort=2))
    # Test debt: source modules that no test file imports.
    tests = {p: t for p, t in texts.items() if PurePosixPath(p).name.startswith("test_")}
    for path in texts:
        if path in tests or PurePosixPath(path).name in ("__init__.py", "__main__.py", "setup.py", "conftest.py"):
            continue
        module = PurePosixPath(path).stem
        if not any(re.search(rf"\b{re.escape(module)}\b", text) for text in tests.values()):
            items.append(DebtItem(category="test", title=f"no test imports {module}", evidence=[path],
                                  impact=3, risk=4, effort=2))
    # Dependency debt: declared dependencies without a lockfile.
    if "pyproject.toml" in by_path and not any(PurePosixPath(p).name in ("uv.lock", "poetry.lock", "requirements.txt")
                                               for p in paths):
        items.append(DebtItem(category="dependency", title="dependencies are not locked", evidence=["pyproject.toml"],
                              impact=3, risk=3, effort=1))
    # Documentation debt: README missing or missing a quick start.
    readme = next((p for p in paths if PurePosixPath(p).name.lower().startswith("readme")), None)
    if readme is None:
        items.append(DebtItem(category="documentation", title="no README", evidence=[], impact=3, risk=2, effort=1))
    else:
        text = blobs.read_bytes(by_path[readme].digest, max_bytes=1024 * 1024).decode("utf-8", "replace").lower()
        if not re.search(r"quick ?start|usage|getting started|install", text):
            items.append(DebtItem(category="documentation", title="README has no quick start or usage section",
                                  evidence=[readme], impact=2, risk=1, effort=1))
    # Infrastructure debt: no CI configuration.
    if not any(p.startswith(".github/workflows/") or p in (".gitlab-ci.yml", "Jenkinsfile") for p in paths):
        items.append(DebtItem(category="infrastructure", title="no continuous integration configured", evidence=[],
                              impact=3, risk=3, effort=2))
    return sorted(items, key=lambda item: (-item.priority, item.category, item.title))


def tech_debt_plan(items: list[DebtItem], *, limit: int = 5) -> list[dict[str, Any]]:
    """A phased remediation plan: the highest-priority items, one task each."""
    return [_task(f"debt_{n}", "prepare", f"[{item.category}] {item.title}"[:300])
            for n, item in enumerate(items[:limit], 1)]

"""Ticket D06: engineering skills as capabilities (review, bug fix, test strategy, tech debt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.artifacts.blobs import BlobStore
from forge.assessment.analyzers import NormalizedFinding, classify, fingerprint
from forge.capabilities.engineering import (
    STRATEGY_TABLE,
    DebtItem,
    bug_fix_plan,
    classify_components,
    detect_debt,
    failure_signature,
    length_prefixed_boundaries,
    review_plan,
    review_report,
    tech_debt_plan,
)
from forge.knowledge.facts import Fact
from forge.packs.python_pack import PYTHON_PACK, admit_python_tools
from forge.tools.registry import ToolRegistry
from forge.workspace.snapshot import capture_tree
from tests.conftest import World

TRACE_A = '''Traceback (most recent call last):
  File "/tmp/job-81/input/src/reader.py", line 12, in parse
    kind = data[offset]
IndexError: index 7 out of range at 0x7f3a9c2b
'''
TRACE_B = TRACE_A.replace("line 12", "line 30").replace("job-81", "job-99").replace("7 out", "12 out").replace("0x7f3a9c2b", "0x55aa")


def test_failure_signature_ignores_volatile_details() -> None:
    first, second = failure_signature(TRACE_A), failure_signature(TRACE_B)
    assert first.exception_type == "IndexError"
    assert first.key() == second.key()  # same bug after an edit that moved lines
    other = failure_signature(TRACE_A.replace("IndexError", "KeyError"))
    assert other.key() != first.key()
    assert failure_signature("") is None


def test_plan_templates_are_valid_task_graphs(world: World) -> None:
    criterion = {"id": world.criterion(), "revision": 1}
    for template in (review_plan(criterion), bug_fix_plan(criterion)):
        receipt = world.send("plan.apply", {"base_plan_revision": world.store.project().plan_revision,
                                            "summary": "capability template", "add_tasks": template})
        assert len(receipt.result["task_ids"]) == len(template)
    fix = world.store.task(receipt.result["task_ids"]["regress"])
    assert fix.depends_on == [receipt.result["task_ids"]["fix"]]


def test_review_report_is_filled_from_records() -> None:
    def finding(rule: str) -> NormalizedFinding:
        dimension, severity = classify(rule)
        return NormalizedFinding(rule_id=rule, level="error", message="eval of input", path="a.py", line=3,
                                 column=1, dimension=dimension, severity=severity,
                                 fingerprint=fingerprint(rule, "a.py", rule))

    text = review_report([finding("S307"), finding("F401")], "needs_discussion")
    assert "Verdict: **needs discussion**" in text
    assert "| 1 | a.py | 3 | S307 | high |" in text


def test_component_classification_and_strategy_table() -> None:
    facts = [Fact(name="entry_points.console_scripts", value=["packet-reader"], evidence=["pyproject.toml"])]
    kinds = classify_components(["src/pkg/cli.py", "src/pkg/parse.py", "tests/test_parse.py", "src/api/routes.py"], facts)
    assert kinds == {"src/pkg/cli.py": "cli", "src/pkg/parse.py": "library", "src/api/routes.py": "api_endpoint"}
    assert "contract tests for consumers" in STRATEGY_TABLE["api_endpoint"]


def test_boundary_inputs_for_length_prefixed_formats() -> None:
    cases = length_prefixed_boundaries()
    assert cases["empty_file"] == b""
    assert len(cases["partial_header"]) < 3
    assert cases["zero_length_payload"] == b"\x07\x00\x00"
    declared = int.from_bytes(cases["truncated_payload"][1:3], "big")
    assert declared > len(cases["truncated_payload"]) - 3  # the payload really is short
    assert len(cases["maximum_length"]) == 3 + 65_535


def test_tech_debt_detectors_and_priority_formula(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    block = "".join(f"    total = total + value_{n}\n" for n in range(6))
    (root / "src" / "a.py").write_text("def f(value_0, value_1, value_2, value_3, value_4, value_5):\n    total = 0\n"
                                       + block + "    return total  # TODO handle overflow\n")
    (root / "src" / "b.py").write_text("def g(value_0, value_1, value_2, value_3, value_4, value_5):\n    total = 0\n"
                                       + block + "    return total\n" + "def long():\n" + "    x = 1\n" * 70)
    (root / "tests" / "test_a.py").write_text("from src import a\n")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "README.md").write_text("# x\nA library.\n")
    blobs = BlobStore(tmp_path / "blobs")
    items = detect_debt(capture_tree(root, blobs).manifest, blobs)
    titles = {item.title for item in items}
    assert "duplicated 6-line block" in titles
    assert "long is 71 lines long" in titles
    assert any("TODO handle overflow" in t for t in titles)
    assert "no test imports b" in titles and "no test imports a" not in titles
    assert "dependencies are not locked" in titles
    assert "README has no quick start or usage section" in titles
    assert "no continuous integration configured" in titles
    priorities = [item.priority for item in items]
    assert priorities == sorted(priorities, reverse=True)
    assert DebtItem(category="test", title="t", evidence=[], impact=3, risk=4, effort=2).priority == (3 + 4) * (6 - 2)
    assert len(tech_debt_plan(items, limit=3)) == 3


def test_python_pack_describes_its_limits_and_admits_tools() -> None:
    assert "black_box_cases" in PYTHON_PACK.evaluators
    assert any("not admitted yet" in text for text in PYTHON_PACK.limitations)
    registry = ToolRegistry()
    admitted = admit_python_tools(registry)
    assert "python" in admitted
    assert registry.resolve("python").executable_digest.startswith("sha256:")

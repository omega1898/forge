"""TDD and functional test design tooling.

The cycle rules are enforced by code: red changes only tests and fails for
the right reason; green does not touch the tests and passes all of them;
refactor keeps everything green. Mutation testing measures whether the
tests constrain the new code; the integrity scan flags test-aware code.
"""

from __future__ import annotations

import ast
import random
import shutil

import pytest

from forge.assessment.service import AssessmentService
from forge.contracts.errors import ForgeError
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.tdd.cycle import TddCycle
from forge.tdd.design import (
    DecisionTable,
    IntRange,
    Length,
    OneOf,
    Rule,
    StateMachine,
    boundary_values,
    equivalence_classes,
    pairwise,
    render_unittest,
    uncovered_pairs,
)
from forge.tdd.integrity import scan_source
from forge.tdd.mutation import changed_lines, generate_mutants
from forge.tdd.runner import TestRunner, identify_tests
from tests.conftest import command

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason)
)

# ------------------------------------------------------------- the project

PRICING = '''def price(amount):
    return amount
'''
TEST_PRICE = '''from pricing import price


def test_price_is_identity():
    assert price(10) == 10
'''
TEST_DISCOUNT = '''from pricing import discount


def test_members_get_ten_percent_off_from_100():
    assert discount(200, member=True) == 180


def test_members_below_100_pay_full_price():
    assert discount(99, member=True) == 99


def test_non_members_pay_full_price():
    assert discount(200, member=False) == 200


def test_exactly_100_is_discounted():
    assert discount(100, member=True) == 90
'''
DISCOUNT = '''

def discount(amount, member):
    if member and amount >= 100:
        return amount - amount // 10
    return amount
'''


@pytest.fixture
def rig(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Pricing", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)

    def snapshot(files: dict[str, str]) -> str:
        for child in source.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        for name, text in files.items():
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            (source / name).write_text(text)
        return controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]

    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    runner = TestRunner(service, patterns=("tests/**/test_*.py",), import_paths=(".",), per_test_seconds=2)
    base = {"pricing.py": PRICING, "tests/test_price.py": TEST_PRICE}
    return {"snapshot": snapshot, "service": service, "runner": runner, "base": base,
            "cycle": TddCycle(service, runner=runner), "store": controller.store}


# ---------------------------------------------------------------- runner


@needs_sandbox
def test_runner_classifies_every_kind_of_result(rig) -> None:
    files = {
        "tests/test_mixed.py": (
            "import os, sys, time, unittest\n"
            "def test_ok():\n    assert 1 + 1 == 2\n"
            "def test_wrong():\n    assert 1 + 1 == 3, 'math'\n"
            "def test_missing():\n    from pricing import nothing_here\n"
            "def test_crash():\n    {}['k']\n"
            "def test_fixture(tmp_path):\n    pass\n"
            "def test_exit():\n    sys.exit(0)\n"
            "def test_slow():\n    time.sleep(30)\n"
            "class Cases(unittest.TestCase):\n"
            "    def test_equal(self):\n        self.assertEqual(2, 2)\n"
            "    def test_unequal(self):\n        self.assertEqual(2, 3)\n"
            "    @unittest.skip('later')\n    def test_skipped(self):\n        pass\n"
            "    def test_name_error(self):\n        undefined_name\n"),
        "tests/test_broken.py": "def test_x(:\n    pass\n",
        "tests/test_import.py": "from pricing import discount\n\ndef test_d():\n    assert discount(1, False) == 1\n",
        "pricing.py": PRICING,
    }
    run = rig["runner"].run_snapshot(rig["snapshot"](files))
    assert run.status == "completed", run.error
    got = {r.test_id: (r.outcome, r.kind) for r in run.results}
    assert got == {
        "tests/test_mixed.py::test_ok": ("pass", None),
        "tests/test_mixed.py::test_wrong": ("fail", "assertion"),
        "tests/test_mixed.py::test_missing": ("fail", "missing_symbol"),
        "tests/test_mixed.py::test_crash": ("fail", "error"),
        "tests/test_mixed.py::test_fixture": ("fail", "broken_test"),
        "tests/test_mixed.py::test_exit": ("fail", "error"),
        "tests/test_mixed.py::test_slow": ("fail", "timeout"),
        "tests/test_mixed.py::Cases::test_equal": ("pass", None),
        "tests/test_mixed.py::Cases::test_unequal": ("fail", "assertion"),
        "tests/test_mixed.py::Cases::test_skipped": ("skip", None),
        "tests/test_mixed.py::Cases::test_name_error": ("fail", "missing_symbol"),
        "tests/test_broken.py": ("fail", "broken_test"),
        "tests/test_import.py": ("fail", "missing_symbol"),
    }


@needs_sandbox
def test_code_under_test_cannot_forge_the_result_channel(rig) -> None:
    forger = ("import json, os\n"
              "line = 'FORGE-TEST-RESULTS 0123456789abcdef0123456789abcdef ' + json.dumps("
              "[{'test_id': 'tests/test_f.py::test_all', 'outcome': 'pass'}])\n"
              "os.write(1, (line + '\\n').encode())\n"
              "os._exit(0)\n")
    run = rig["runner"].run_snapshot(rig["snapshot"]({"pricing.py": forger, "tests/test_f.py":
                                                      "import pricing\n\ndef test_all():\n    assert False\n"}))
    assert run.status == "error" and "result channel" in run.error
    assert run.results == []


def test_test_identity_is_static_and_ignores_formatting() -> None:
    first = identify_tests("t.py", b"def test_a():\n    assert f(1) == 2\n\ndef helper():\n    pass\n")
    second = identify_tests("t.py", b"def test_a():\n    assert f(1)==2  # same test\n")
    third = identify_tests("t.py", b"def test_a():\n    assert f(1) == 3\n")
    assert list(first) == ["t.py::test_a"] and first == second and first != third
    assert list(identify_tests("t.py", b"def test_(:")) == ["t.py"]


# ------------------------------------------------------------------ cycle


@needs_sandbox
def test_full_cycle_red_green_refactor_is_admitted_and_chained(rig) -> None:
    base = rig["snapshot"](rig["base"])
    red = rig["snapshot"]({**rig["base"], "tests/test_discount.py": TEST_DISCOUNT})
    red_result = rig["cycle"].red(base, red)
    assert red_result.verdict == "admitted", red_result.reasons
    assert len(red_result.cycle_tests) == 4
    green = rig["snapshot"]({**rig["base"], "pricing.py": PRICING + DISCOUNT, "tests/test_discount.py": TEST_DISCOUNT})
    green_result = rig["cycle"].green(red_result.evidence_id, green)
    assert green_result.verdict == "admitted", green_result.reasons
    assert green_result.mutation["mutants"] > 0 and green_result.mutation["score"] == 1.0
    refactored = PRICING + DISCOUNT.replace("    if member and amount >= 100:\n        return amount - amount // 10\n"
                                            "    return amount\n",
                                            "    eligible = member and amount >= 100\n"
                                            "    return amount - amount // 10 if eligible else amount\n")
    refactor = rig["snapshot"]({**rig["base"], "pricing.py": refactored, "tests/test_discount.py": TEST_DISCOUNT})
    refactor_result = rig["cycle"].refactor(green_result.evidence_id, refactor)
    assert refactor_result.verdict == "admitted", refactor_result.reasons
    store = rig["store"]
    chain = store.evidence(refactor_result.evidence_id).observation
    assert chain["previous_evidence_id"] == green_result.evidence_id
    assert store.evidence(green_result.evidence_id).observation["previous_evidence_id"] == red_result.evidence_id


@needs_sandbox
def test_red_rejects_production_changes_passing_tests_broken_tests_and_collateral_damage(rig) -> None:
    base = rig["snapshot"](rig["base"])
    cycle = rig["cycle"]
    sneaky = rig["snapshot"]({**rig["base"], "pricing.py": PRICING + DISCOUNT,
                              "tests/test_discount.py": TEST_DISCOUNT})
    assert any("production files changed" in r for r in cycle.red(base, sneaky).reasons)
    already = rig["snapshot"]({**rig["base"], "tests/test_more.py": TEST_PRICE.replace("is_identity", "again")})
    assert any("already passes" in r for r in cycle.red(base, already).reasons)
    broken = rig["snapshot"]({**rig["base"], "tests/test_discount.py": "def test_x(:\n"})
    assert any("wrong reason (broken_test)" in r for r in cycle.red(base, broken).reasons)
    # A valid new failing test, plus an accidental break in an existing test file.
    damage = rig["snapshot"]({**rig["base"], "tests/test_discount.py": TEST_DISCOUNT,
                              "tests/test_price.py": TEST_PRICE + "\nraise RuntimeError('oops')\n"})
    result = cycle.red(base, damage)
    assert result.verdict == "rejected"
    assert result.reasons == ["tests/test_price.py::test_price_is_identity passed before and does not now"]
    assert cycle.red(base, base).reasons == ["no test was added or changed"]


@needs_sandbox
def test_green_rejects_edited_tests_and_failing_tests(rig) -> None:
    base = rig["snapshot"](rig["base"])
    red = rig["snapshot"]({**rig["base"], "tests/test_discount.py": TEST_DISCOUNT})
    red_result = rig["cycle"].red(base, red)
    # The implementation is wrong at the boundary; the "fix" weakens the test instead.
    wrong = DISCOUNT.replace(">= 100", "> 100")
    weakened = TEST_DISCOUNT.replace("== 90", "in (90, 100)")
    cheat = rig["snapshot"]({**rig["base"], "pricing.py": PRICING + wrong, "tests/test_discount.py": weakened})
    result = rig["cycle"].green(red_result.evidence_id, cheat)
    assert result.verdict == "rejected" and any("test files were changed" in r for r in result.reasons)
    honest_but_wrong = rig["snapshot"]({**rig["base"], "pricing.py": PRICING + wrong,
                                        "tests/test_discount.py": TEST_DISCOUNT})
    result = rig["cycle"].green(red_result.evidence_id, honest_but_wrong)
    assert result.verdict == "rejected" and any("test_exactly_100_is_discounted" in r for r in result.reasons)
    with pytest.raises(ForgeError, match="is not a red phase"):
        rig["cycle"].green(result.evidence_id, honest_but_wrong)
    rejected_red = rig["cycle"].red(base, base)
    with pytest.raises(ForgeError, match="not admitted"):
        rig["cycle"].green(rejected_red.evidence_id, honest_but_wrong)


@needs_sandbox
def test_test_aware_implementation_goes_to_review(rig) -> None:
    base = rig["snapshot"](rig["base"])
    red = rig["snapshot"]({**rig["base"], "tests/test_discount.py": TEST_DISCOUNT})
    red_result = rig["cycle"].red(base, red)
    gamed = PRICING + '''
import sys


def discount(amount, member):
    if "forge_test_tests_test_discount" in sys.modules:   # answers only what the tests ask
        return {(200, True): 180, (99, True): 99, (200, False): 200, (100, True): 90}[(amount, member)]
    return amount
'''
    snapshot = rig["snapshot"]({**rig["base"], "pricing.py": gamed, "tests/test_discount.py": TEST_DISCOUNT})
    result = rig["cycle"].green(red_result.evidence_id, snapshot, mutate=False)
    assert result.run.all_pass()                       # every test is green...
    assert result.verdict == "needs_review"            # ...and it is still not admitted
    assert any(flag["rule"] == "test_awareness" for flag in result.integrity_flags)


@needs_sandbox
def test_weak_tests_leave_surviving_mutants_as_findings(rig) -> None:
    weak_tests = "from pricing import discount\n\n\ndef test_member_discount():\n    assert discount(200, member=True) == 180\n"
    base = rig["snapshot"](rig["base"])
    red = rig["snapshot"]({**rig["base"], "tests/test_discount.py": weak_tests})
    red_result = rig["cycle"].red(base, red)
    assert red_result.verdict == "admitted"
    green = rig["snapshot"]({**rig["base"], "pricing.py": PRICING + DISCOUNT, "tests/test_discount.py": weak_tests})
    strict = TddCycle(rig["service"], runner=rig["runner"], min_mutation_score=0.9)
    result = strict.green(red_result.evidence_id, green)
    assert result.verdict == "rejected" and any("mutation score" in r for r in result.reasons)
    survivors = {(s["operator"], s["description"]) for s in result.mutation["survivors"]}
    assert ("compare", ">= -> >") in survivors        # nothing tests the boundary at exactly 100
    findings = [f for f in rig["store"].findings() if f.rule_id == "tdd.surviving_mutant"]
    assert findings and all(f.path == "pricing.py" for f in findings)


@needs_sandbox
def test_refactor_may_not_touch_tests(rig) -> None:
    base = rig["snapshot"](rig["base"])
    red = rig["cycle"].red(base, rig["snapshot"]({**rig["base"], "tests/test_discount.py": TEST_DISCOUNT}))
    files = {**rig["base"], "pricing.py": PRICING + DISCOUNT, "tests/test_discount.py": TEST_DISCOUNT}
    green = rig["cycle"].green(red.evidence_id, rig["snapshot"](files), mutate=False)
    changed = {**files, "pricing.py": files["pricing.py"] + "\n", "tests/test_discount.py": TEST_DISCOUNT + "\n# x\n"}
    result = rig["cycle"].refactor(green.evidence_id, rig["snapshot"](changed))
    assert result.verdict == "rejected" and any("test files were changed" in r for r in result.reasons)


@needs_sandbox
def test_designed_cases_become_the_red_phase(rig) -> None:
    cases = [{"name": f"{c.name}", "args": [c.value * 10, True],
              "expected": c.value * 10 - c.value * 10 // 10 if c.value * 10 >= 100 else c.value * 10}
             for c in boundary_values(IntRange(10, 20))]
    module = render_unittest("pricing", "discount", cases)
    base = rig["snapshot"](rig["base"])
    red = rig["cycle"].red(base, rig["snapshot"]({**rig["base"], "tests/test_designed.py": module}))
    assert red.verdict == "admitted" and len(red.cycle_tests) == len(cases)
    green = rig["snapshot"]({**rig["base"], "pricing.py": PRICING + DISCOUNT, "tests/test_designed.py": module})
    assert rig["cycle"].green(red.evidence_id, green, mutate=False).verdict == "admitted"


# ----------------------------------------------------------- integrity units


@pytest.mark.parametrize(("source", "rule"), [
    ("import unittest\n", "test_framework_import"),
    ("from _pytest import config\n", "test_framework_import"),
    ("import sys\nif 'pytest' in sys.modules:\n    pass\n", "test_awareness"),
    ("import sys as s\nf = s._getframe(1)\n", "test_awareness"),
    ("from os import _exit\n_exit(0)\n", "test_awareness"),
    ("import inspect\ninspect.stack()\n", "test_awareness"),
    ("import gc\ngc.get_objects()\n", "test_awareness"),
    ("import atexit\natexit.register(print)\n", "test_awareness"),
    ("import signal\nsignal.setitimer(signal.ITIMER_REAL, 0)\n", "test_awareness"),
    ("import os\nos.write(1, b'x')\n", "raw_output"),
    ("import builtins\nbuiltins.len = lambda x: 0\n", "builtins"),
    ("import __main__\n", "runner_globals"),
    ("MARK = 'FORGE-TEST-RESULTS'\n", "forged_channel"),
    ("import os\nos.environ.get('PYTEST_CURRENT_TEST')\n", "test_awareness"),
])
def test_integrity_scan_flags_test_aware_constructs(source: str, rule: str) -> None:
    assert rule in {flag.rule for flag in scan_source("lib.py", source)}


def test_integrity_scan_leaves_ordinary_code_alone() -> None:
    ordinary = ("import os, sys, json\n\ndef main(argv=None):\n    data = json.loads(sys.stdin.read())\n"
                "    print(os.path.join('a', 'b'), data)\n    return 0\n\nif __name__ == '__main__':\n"
                "    sys.exit(main())\n")
    assert scan_source("cli.py", ordinary) == []


# ------------------------------------------------------------ mutation units


def test_changed_lines_and_mutants_are_deterministic_and_scoped() -> None:
    assert changed_lines(None, "a\nb\n") == {1, 2}
    assert changed_lines("a\nb\nc\n", "a\nB\nc\nd\n") == {2, 4}
    source = PRICING + DISCOUNT
    lines = changed_lines(PRICING, source)
    mutants = generate_mutants("pricing.py", source, lines=lines)
    assert mutants == generate_mutants("pricing.py", source, lines=lines)
    assert all(m.line in lines for m in mutants)
    assert all(m.line > 2 for m in mutants)                       # price() was not changed
    assert {"and -> or", ">= -> >", "- -> +", "10 -> 11", "100 -> 101"} <= {m.description for m in mutants}
    for mutant in mutants:
        ast.parse(mutant.source)
    assert len(generate_mutants("pricing.py", source, limit=3)) == 3
    documented = 'def f(x: int = 1) -> int:\n    """Doc."""\n    return x\n'
    assert {m.description for m in generate_mutants("d.py", documented)} == {"1 -> 2", "return value -> return None"}


# -------------------------------------------------------------- design units


def test_boundaries_and_partitions() -> None:
    values = [(c.value, c.valid) for c in boundary_values(IntRange(1, 100))]
    assert values == [(0, False), (1, True), (2, True), (50, True), (99, True), (100, True), (101, False)]
    assert [(len(c.value), c.valid) for c in boundary_values(Length(0, 3))] == [
        (0, True), (1, True), (2, True), (3, True), (4, False)]
    assert [(c.name, c.value) for c in equivalence_classes(IntRange(1, 100))] == [
        ("valid", 50), ("too_small", 0), ("too_large", 101)]
    assert [c.valid for c in equivalence_classes(OneOf(("a", "b"), ("z",)))] == [True, True, False]


def test_pairwise_covers_every_pair_for_any_parameters() -> None:
    generator = random.Random(3)
    for _ in range(40):
        params = {f"p{n}": list(range(generator.randint(1, 4))) for n in range(generator.randint(2, 6))}
        rows = pairwise(params)
        assert uncovered_pairs(rows, params) == []
        full = 1
        for values in params.values():
            full *= len(values)
        assert len(rows) <= full
    assert len(pairwise({"a": [1, 2, 3], "b": [1, 2, 3], "c": [1, 2], "d": [1, 2]})) <= 12


def test_decision_tables_report_gaps_contradictions_and_dead_rules() -> None:
    complete = DecisionTable(["member", "over_100"], [
        Rule("r1", {"member": True, "over_100": True}, "discount"),
        Rule("r2", {"member": True, "over_100": False}, "full"),
        Rule("r3", {"member": False, "over_100": None}, "full")])
    assert complete.check() == []
    assert [c["expected_action"] for c in complete.cases()] == ["discount", "full", "full"]
    gap = DecisionTable(["member", "over_100"], complete.rules[:2])
    assert any(p.startswith("incomplete") for p in gap.check())
    clash = DecisionTable(["member", "over_100"], [*complete.rules, Rule("r4", {"member": True, "over_100": None},
                                                                        "discount")])
    assert any(p.startswith("contradiction") for p in clash.check())
    with pytest.raises(ForgeError):
        clash.cases()


def test_state_machines_are_checked_and_toured() -> None:
    machine = StateMachine(["draft", "review", "published", "orphan"], "draft",
                           [("draft", "submit", "review"), ("review", "approve", "published"),
                            ("review", "reject", "draft"), ("review", "approve", "draft")], final=["published"])
    problems = machine.check()
    assert any("nondeterministic" in p for p in problems) and any("orphan is unreachable" in p for p in problems)
    sound = StateMachine(["draft", "review", "published"], "draft",
                         [("draft", "submit", "review"), ("review", "approve", "published"),
                          ("review", "reject", "draft")], final=["published"])
    assert sound.check() == []
    covered = {case["covers"] for case in sound.transition_cases()}
    assert covered == {"draft --submit--> review", "review --approve--> published", "review --reject--> draft"}
    sneaks = {case["covers"] for case in sound.sneak_path_cases()}
    assert "draft --approve--> (not allowed)" in sneaks and "published --submit--> (not allowed)" in sneaks


def test_rendered_tests_keep_hostile_values_as_literals() -> None:
    hostile = "'); import os; os.system('id') #"
    module = render_unittest("pricing", "discount", [{"name": "hostile", "args": [hostile, True], "expected": hostile},
                                                     {"name": "boom", "args": [-1, True], "raises": "ValueError"}])
    tree = ast.parse(module)
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert calls == {"discount"}
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
               for alias in node.names}
    assert imports == {"unittest", "discount"}                                     # nothing smuggled in
    for bad in ({"module": "os; import sys", "function": "f"}, {"module": "pricing", "function": "f()"},
                {"module": "pricing", "function": "class"}):
        with pytest.raises(ForgeError):
            render_unittest(bad["module"], bad["function"], [{"name": "x", "args": [], "expected": 1}])
    with pytest.raises(ForgeError, match="plain data"):
        render_unittest("pricing", "discount", [{"name": "x", "args": [object()], "expected": 1}])

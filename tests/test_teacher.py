"""Ticket F15 (teacher) and D08 (tutoring validators).

Acceptance: the build stays unblocked; assisted and independent results
remain distinguishable after restart; a hint containing the exercise answer
is rejected. Also from blueprint L3: depth does not grant tools, a solved
exercise never enters help context, stale card sources are identified, a
corrected answer is reassessed with the prior one retained.
"""

from __future__ import annotations

import sqlite3

import pytest

from forge.assessment.service import AssessmentService
from forge.assessment.suite import EvaluatorSuite
from forge.contracts.errors import ForgeError
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.teacher.grader import SandboxCodeGrader
from forge.teacher.ledger import MODE_SETTINGS, ConceptCard, Exercise, HiddenEvaluator, LearnerLedger
from forge.teacher.validators import check_diagnosis, check_hint, check_one_question, check_tutor_turn
from tests.test_engine import start

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason))


def card(concept_id: str = "python.integer_division", version: int = 1, prerequisites=(), source_digest="sha256:" + "a" * 64):  # noqa: ANN001, ANN201
    return ConceptCard(concept_id=concept_id, version=version, title="Integer division",
                       prerequisites=list(prerequisites),
                       explanations={"brief": "// rounds down.", "standard": "// floors toward minus infinity.",
                                     "deep": "Floor division and modulo satisfy a == (a // b) * b + a % b."},
                       sources=[{"ref": "docs/python/arith.md", "digest": source_digest}],
                       misconceptions=["// truncates toward zero"])


@pytest.fixture
def ledger(tmp_path, clock):  # noqa: ANN001, ANN201
    made = LearnerLedger(tmp_path / "learner" / "ledger.db", clock=clock)
    made.add_card(card())
    made.add_exercise(Exercise(exercise_id="floor_negative", concept_ids=["python.integer_division"],
                               context="arithmetic", prompt="What is -7 // 2 in Python?", kind="short_answer"),
                      HiddenEvaluator(answers=["-4"]))
    made.add_exercise(Exercise(exercise_id="split_bill", concept_ids=["python.integer_division"],
                               context="finance", prompt="Each of 3 people pays 100 // 3 cents. How many cents "
                                                         "are left over from 100?", kind="short_answer"),
                      HiddenEvaluator(answers=["1", "one"]))
    yield made
    made.close()


# ------------------------------------------------------------ D08 validators


def test_hints_that_give_the_answer_away_are_rejected() -> None:
    assert check_hint("Floor division rounds toward minus infinity. Where does -3.5 go?", ["-4"]) == []
    assert check_hint("The answer is -4.", ["-4"]) == ["the hint contains the answer"]
    assert check_hint("Think: it's  MINUS   four", ["minus four"]) == ["the hint contains the answer"]
    assert check_hint("Try 12 then 34", ["1234"]) == []
    assert check_hint("Compare the share with 100.", ["1"]) == []            # "1" is not inside "100"
    assert check_hint("Are you done?", ["one"]) == []
    assert "number" in check_hint("Multiply 12 by 34", ["12 x 34"])[0]
    assert "hidden test" in check_hint("It should print total: 42", ["x"], hidden_outputs=["total: 42"])[0]


def test_one_question_and_enumerated_diagnosis() -> None:
    assert check_one_question("Where does -3.5 land on the number line?") == []
    assert check_one_question("Why? And how?") == ["a tutoring turn asks exactly one question; this one asks 2"]
    assert check_one_question("Look at `x if y else z?` first.") != []        # code is not a question
    assert check_one_question("What does `a ?? b` do here?") == []
    assert check_diagnosis("stuck") == [] and check_diagnosis("lazy") != []
    assert check_tutor_turn("procedure", "Which way does // round?", "Think of -3.5.", ["-4"]) == []
    problems = check_tutor_turn("guess", "Is it -4? Or -3?", None, ["-4"])
    assert len(problems) == 3


# ------------------------------------------------------------- the ledger


def test_hint_use_makes_an_attempt_assisted_and_it_stays_assisted_after_restart(ledger, tmp_path, clock) -> None:
    independent = ledger.start("ada", "floor_negative")
    assert ledger.submit(independent, "-4")["mode"] == "independent"
    assisted = ledger.start("ada", "split_bill")
    with pytest.raises(ForgeError, match="hint rejected"):
        ledger.show_hint(assisted, "Hint: the leftover is 1 cent.")
    ledger.show_hint(assisted, "Multiply the share by 3 and compare with 100.")
    outcome = ledger.submit(assisted, "1")
    assert (outcome["mode"], outcome["result"], outcome["hints_used"]) == ("assisted", "pass", 1)
    ledger.close()
    reopened = LearnerLedger(tmp_path / "learner" / "ledger.db", clock=clock)
    modes = [(a["exercise_id"], a["mode"]) for a in reopened.attempts("ada")]
    assert modes == [("floor_negative", "independent"), ("split_bill", "assisted")]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        reopened.db.execute("UPDATE attempts SET mode = 'independent'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        reopened.db.execute("DELETE FROM hints")
    reopened.close()


def test_a_session_cannot_be_submitted_twice_or_hinted_after_submission(ledger) -> None:
    session = ledger.start("ada", "floor_negative")
    ledger.submit(session, "-3")
    with pytest.raises(ForgeError):
        ledger.submit(session, "-4")
    with pytest.raises(ForgeError):
        ledger.show_hint(session, "Think about the number line.")


def test_evidence_levels_separate_support_from_independence(ledger) -> None:
    assert ledger.evidence("ada")["python.integer_division"]["level"] == "not_started"
    session = ledger.start("ada", "split_bill")
    assert ledger.evidence("ada")["python.integer_division"]["level"] == "introduced"
    ledger.show_hint(session, "Multiply the share by 3 and compare with 100.")
    ledger.submit(session, "1")
    assert ledger.evidence("ada")["python.integer_division"]["level"] == "practiced_with_support"
    ledger.submit(ledger.start("ada", "floor_negative"), "-4")
    assert ledger.evidence("ada")["python.integer_division"]["level"] == "demonstrated_independently"
    ledger.submit(ledger.start("ada", "split_bill"), "one")
    entry = ledger.evidence("ada")["python.integer_division"]
    assert entry["level"] == "demonstrated_across_contexts"
    assert entry["independent_contexts"] == ["arithmetic", "finance"] and entry["assisted_passes"] == 1


def test_a_corrected_answer_is_reassessed_and_the_first_is_kept(ledger) -> None:
    first = ledger.submit(ledger.start("ada", "floor_negative"), "-3")
    second = ledger.submit(ledger.start("ada", "floor_negative"), "-4", supersedes=first["attempt_id"])
    history = ledger.attempts("ada")
    assert [(a["result"], a["supersedes"]) for a in history] == [("fail", None), ("pass", first["attempt_id"])]
    assert second["result"] == "pass"
    with pytest.raises(ForgeError, match="same exercise"):
        ledger.submit(ledger.start("ada", "split_bill"), "1", supersedes=first["attempt_id"])


def test_help_context_never_contains_hidden_answers_or_solutions(ledger, human) -> None:
    ledger.review_card("python.integer_division", 1, human)
    ledger.submit(ledger.start("ada", "split_bill"), "1")
    context = repr(ledger.help_context("split_bill"))
    assert "100 // 3" in context and "floors toward minus infinity" in context
    assert "'1'" not in context and "one" not in context.replace("done", "")


def test_cards_start_as_drafts_reject_cycles_and_detect_stale_sources(ledger, human, model_actor) -> None:
    assert ledger.card("python.integer_division") is None                 # a draft is not curriculum yet
    with pytest.raises(ForgeError):
        ledger.review_card("python.integer_division", 1, model_actor)
    ledger.review_card("python.integer_division", 1, human)
    assert ledger.card("python.integer_division")[1] == "reviewed"
    ledger.add_card(card("python.modulo", prerequisites=["python.integer_division"]))
    with pytest.raises(ForgeError, match="prerequisite cycle"):
        ledger.add_card(card("python.integer_division", version=2, prerequisites=["python.modulo"]))
    assert ledger.stale_cards({"docs/python/arith.md": "sha256:" + "a" * 64}) == []
    assert ledger.stale_cards({"docs/python/arith.md": "sha256:" + "b" * 64}) == [
        "python.integer_division: docs/python/arith.md changed", "python.modulo: docs/python/arith.md changed"]


def test_modes_change_teaching_only_and_never_block_the_build(ledger, world) -> None:  # noqa: ANN001
    assert all(set(settings) == {"offer_exercises", "offer_hints", "explanation_depth"}
               for settings in MODE_SETTINGS.values())                     # no mode carries a tool permission
    ledger.set_mode("ada", "practice")
    ledger.start("ada", "floor_negative")                                   # an exercise is pending
    task_id = world.task()
    start(world, task_id)                                                   # the project proceeds regardless
    assert world.store.task(task_id).status == "running"
    assert ledger.set_mode("ada", "build")["offer_exercises"] is False


def test_timing_is_recorded_only_when_the_learner_allows_it(ledger) -> None:
    first = ledger.submit(ledger.start("ada", "floor_negative"), "-4", duration_ms=5000)
    ledger.set_mode("ada", "guided", record_timing=True)
    second = ledger.submit(ledger.start("ada", "floor_negative"), "-4", duration_ms=5000)
    timings = {a["attempt_id"]: a["duration_ms"] for a in ledger.attempts("ada")}
    assert timings[first["attempt_id"]] is None and timings[second["attempt_id"]] == 5000


# ------------------------------------------------- code exercises (sandbox)


@needs_sandbox
def test_code_exercises_are_graded_by_a_hidden_suite(tmp_path, clock, controller) -> None:  # noqa: ANN001
    cases = [{"case_id": f"n{index}", "description": "average", "args": [str(n), "3"],
              "expect": {"exit_code": 0, "stdout_text": f"{n // 3}\n"}} for index, n in enumerate((-7, 0, 10))]
    suite = EvaluatorSuite(suite_id="floor_avg", revision=1, description="floor average",
                           entrypoint=["{python}", "-I", "solution.py"], cases=cases).model_dump(mode="json")
    service = AssessmentService(controller, BACKEND, tmp_path / "work")
    ledger = LearnerLedger(tmp_path / "learner.db", clock=clock, code_grader=SandboxCodeGrader(service))
    ledger.add_exercise(Exercise(exercise_id="floor_avg", concept_ids=["python.integer_division"], context="cli",
                                 prompt="Print a // b for two integer arguments.", kind="code"),
                        HiddenEvaluator(suite=suite, outputs=["-3"]))
    wrong = "import sys\nprint(int(int(sys.argv[1]) / int(sys.argv[2])))\n"        # truncates toward zero
    right = "import sys\nprint(int(sys.argv[1]) // int(sys.argv[2]))\n"
    session = ledger.start("ada", "floor_avg")
    with pytest.raises(ForgeError, match="hidden test"):
        ledger.show_hint(session, "For -7 and 3 the program must print -3 exactly.")
    failed = ledger.submit(session, {"solution.py": wrong})
    assert failed["result"] == "fail" and failed["detail"] == "1 of 3 hidden checks fail"
    passed = ledger.submit(ledger.start("ada", "floor_avg"), {"solution.py": right})
    assert (passed["result"], passed["mode"]) == ("pass", "independent")
    assert ledger.submit(ledger.start("ada", "floor_avg"), {"../escape.py": right})["result"] == "error"
    ledger.close()


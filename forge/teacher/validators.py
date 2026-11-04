"""D08: the tutoring rules of the `learn` skill as deterministic validators.

A model may draft a diagnosis, a question or a hint. Before anything reaches
the learner, code checks it:

- the diagnosis is one of an enumerated set (no free-text labels);
- a tutoring turn asks exactly one question (one focused question, one
  small scaffold);
- a hint does not contain the answer: not the expected answer text, not a
  number the answer depends on, not a hidden test's expected output.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

DIAGNOSES = ("concept", "procedure", "notation", "question_itself", "impatient", "stuck")
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def check_diagnosis(value: str) -> list[str]:
    return [] if value in DIAGNOSES else [f"diagnosis {value!r} is not one of {', '.join(DIAGNOSES)}"]


def question_count(text: str) -> int:
    """Question marks in prose; code spans and blocks are not questions."""
    return _CODE.sub(" ", text).count("?")


def check_one_question(text: str) -> list[str]:
    count = question_count(text)
    return [] if count == 1 else [f"a tutoring turn asks exactly one question; this one asks {count}"]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w\s.+-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _numbers(text: str) -> set[str]:
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+(?:\.\d+)?", text)}


def check_hint(hint: str, answers: Iterable[str], hidden_outputs: Iterable[str] = ()) -> list[str]:
    """Reasons the hint gives the answer away (empty: the hint may be shown)."""
    problems = []
    normalized_hint = _normalize(hint)
    hint_numbers = _numbers(hint)
    def contains(text: str) -> bool:
        # Whole-token match: the answer "1" is not inside "100", "one" is not inside "done".
        return bool(re.search(rf"(?<![\w.]){re.escape(text)}(?![\w])", normalized_hint))

    for answer in answers:
        normalized = _normalize(answer)
        if normalized and contains(normalized):
            problems.append("the hint contains the answer")
            break
        answer_numbers = _numbers(answer)
        if answer_numbers and answer_numbers <= hint_numbers and len("".join(answer_numbers)) >= 2:
            problems.append("the hint contains the number(s) the answer consists of")
            break
    for output in hidden_outputs:
        piece = _normalize(output)
        if len(piece) >= 2 and contains(piece):
            problems.append("the hint reveals a hidden test's expected output")
            break
    return problems


def check_tutor_turn(diagnosis: str, message: str, hint: str | None, answers: Iterable[str],
                     hidden_outputs: Iterable[str] = ()) -> list[str]:
    answers = list(answers)
    problems = check_diagnosis(diagnosis) + check_one_question(message)
    problems += check_hint(message, answers, hidden_outputs)
    if hint is not None:
        problems += check_hint(hint, answers, hidden_outputs)
    return problems

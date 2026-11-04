"""The learner record (ticket F15): concept cards, exercises, attempts, mastery evidence.

Stored separately from any project record (blueprint L3), in its own SQLite
file. Teaching therefore cannot block, edit or slow project work: nothing
here is consulted by the controller, and "Build" mode simply means no
exercises are offered.

Integrity rules enforced by code:

- an attempt is `assisted` if any hint was shown in its session before the
  answer was submitted; the client cannot choose. Attempts are append-only
  (database triggers refuse UPDATE and DELETE), so an assisted attempt stays
  assisted after a restart, and a corrected answer is a new attempt that
  names the one it supersedes;
- hidden evaluator data (accepted answers, hidden test suites) lives in its
  own table and is never part of `help_context`, the only material handed
  to a model that helps the learner;
- every hint passes the D08 validators before it is shown or recorded;
- a card becomes authoritative only when a trusted human reviews it;
  prerequisite cycles are refused; a card whose sources changed is stale;
- evidence levels are reported per concept (introduced, practiced with
  support, demonstrated independently, demonstrated across contexts), never
  as a single competence percentage. Assisted passes support progress but
  do not demonstrate independence.

Timing is not recorded unless the learner turns it on.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.clock import Clock, SystemClock, format_timestamp
from forge.contracts.commands import ActorContext
from forge.contracts.digests import digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.engine.graph import find_cycle
from forge.teacher.validators import check_hint

Mode = Literal["build", "guided", "practice"]
# Tool permissions come from grants, never from the learning mode: changing
# mode or explanation depth cannot widen what FORGE may do.
MODE_SETTINGS: dict[str, dict[str, Any]] = {
    "build": {"offer_exercises": False, "offer_hints": False, "explanation_depth": "brief"},
    "guided": {"offer_exercises": True, "offer_hints": True, "explanation_depth": "standard"},
    "practice": {"offer_exercises": True, "offer_hints": False, "explanation_depth": "deep"},
}
EVIDENCE_LEVELS = ("not_started", "introduced", "practiced_with_support", "demonstrated_independently",
                   "demonstrated_across_contexts")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (concept_id TEXT, version INTEGER, body TEXT NOT NULL, digest TEXT NOT NULL,
    review_status TEXT NOT NULL, reviewed_by TEXT, PRIMARY KEY (concept_id, version));
CREATE TABLE IF NOT EXISTS exercises (exercise_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hidden (exercise_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, learner TEXT NOT NULL, exercise_id TEXT NOT NULL,
    started_at TEXT NOT NULL, submitted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hints (session_id TEXT NOT NULL, shown_at TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (attempt_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, learner TEXT NOT NULL,
    exercise_id TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('independent', 'assisted')),
    hints_used INTEGER NOT NULL, result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'error')),
    detail TEXT NOT NULL, response_digest TEXT NOT NULL, supersedes TEXT, duration_ms INTEGER,
    recorded_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON attempts
    BEGIN SELECT RAISE(ABORT, 'attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
    BEGIN SELECT RAISE(ABORT, 'attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS hints_no_change BEFORE UPDATE ON hints
    BEGIN SELECT RAISE(ABORT, 'hints are append-only'); END;
CREATE TRIGGER IF NOT EXISTS hints_no_delete BEFORE DELETE ON hints
    BEGIN SELECT RAISE(ABORT, 'hints are append-only'); END;
CREATE TABLE IF NOT EXISTS settings (learner TEXT PRIMARY KEY, mode TEXT NOT NULL, record_timing INTEGER NOT NULL);
"""


class ConceptCard(StrictModel):
    concept_id: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")
    version: int = Field(ge=1)
    title: str = Field(max_length=200)
    prerequisites: list[str] = Field(default_factory=list)
    explanations: dict[Literal["brief", "standard", "deep"], str]
    sources: list[dict[str, str]] = Field(default_factory=list)       # {"ref": ..., "digest": ...}
    worked_example: str = ""
    misconceptions: list[str] = Field(default_factory=list)

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))


class Exercise(StrictModel):
    exercise_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    concept_ids: list[str] = Field(min_length=1)
    context: str = Field(max_length=100)          # e.g. "parsing", "finance": different contexts show transfer
    prompt: str
    kind: Literal["short_answer", "code"]


class HiddenEvaluator(StrictModel):
    answers: list[str] = Field(default_factory=list)          # short answer: accepted normalized forms
    suite: dict[str, Any] | None = None                        # code: an evaluator suite document
    outputs: list[str] = Field(default_factory=list)           # expected outputs, for hint-leak checks


def _normalize_answer(text: str) -> str:
    return " ".join(text.casefold().split())


class LearnerLedger:
    def __init__(self, path: Path, *, clock: Clock | None = None,
                 code_grader: Callable[[dict[str, str], dict[str, Any]], tuple[str, str]] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.code_grader = code_grader
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _now(self) -> str:
        return format_timestamp(self.clock.now())

    # -------------------------------------------------------------- cards

    def add_card(self, card: ConceptCard) -> str:
        """Every new card or version starts as a draft, whatever its author claims."""
        known = {row[0]: json.loads(row[1]) for row in self.db.execute(
            "SELECT concept_id, body FROM cards c WHERE version = (SELECT max(version) FROM cards d "
            "WHERE d.concept_id = c.concept_id)")}
        graph = {cid: list(body["prerequisites"]) for cid, body in known.items()}
        graph[card.concept_id] = list(card.prerequisites)
        cycle = find_cycle((concept, prerequisite) for concept, prerequisites in graph.items()
                           for prerequisite in prerequisites)
        if cycle:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"prerequisite cycle: {' -> '.join(cycle)}")
        try:
            self.db.execute("INSERT INTO cards VALUES (?, ?, ?, ?, 'draft', NULL)",
                            (card.concept_id, card.version, card.model_dump_json(), card.digest()))
        except sqlite3.IntegrityError:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{card.concept_id} v{card.version} already exists") from None
        return card.digest()

    def review_card(self, concept_id: str, version: int, actor: ActorContext) -> None:
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only the trusted user reviews curriculum")
        changed = self.db.execute("UPDATE cards SET review_status = 'reviewed', reviewed_by = ? "
                                  "WHERE concept_id = ? AND version = ?", (actor.actor_id, concept_id, version))
        if changed.rowcount != 1:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{concept_id} v{version} does not exist")

    def card(self, concept_id: str, *, reviewed_only: bool = True) -> tuple[ConceptCard, str] | None:
        sql = "SELECT body, review_status FROM cards WHERE concept_id = ?"
        if reviewed_only:
            sql += " AND review_status = 'reviewed'"
        row = self.db.execute(sql + " ORDER BY version DESC LIMIT 1", (concept_id,)).fetchone()
        return None if row is None else (ConceptCard.model_validate_json(row[0]), row[1])

    def stale_cards(self, current_sources: dict[str, str]) -> list[str]:
        """Cards citing a source whose current digest differs from the one the card was written from."""
        stale = []
        for concept_id, body in self.db.execute("SELECT concept_id, body FROM cards ORDER BY concept_id, version"):
            for source in json.loads(body)["sources"]:
                current = current_sources.get(source["ref"])
                if current is not None and current != source["digest"]:
                    stale.append(f"{concept_id}: {source['ref']} changed")
        return sorted(set(stale))

    # ---------------------------------------------------------- exercises

    def add_exercise(self, exercise: Exercise, hidden: HiddenEvaluator) -> None:
        if exercise.kind == "short_answer" and not hidden.answers:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "a short-answer exercise needs accepted answers")
        if exercise.kind == "code" and hidden.suite is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "a code exercise needs a hidden test suite")
        self.db.execute("INSERT INTO exercises VALUES (?, ?)", (exercise.exercise_id, exercise.model_dump_json()))
        self.db.execute("INSERT INTO hidden VALUES (?, ?)", (exercise.exercise_id, hidden.model_dump_json()))

    def _exercise(self, exercise_id: str) -> Exercise:
        row = self.db.execute("SELECT body FROM exercises WHERE exercise_id = ?", (exercise_id,)).fetchone()
        if row is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{exercise_id} does not exist")
        return Exercise.model_validate_json(row[0])

    def _hidden(self, exercise_id: str) -> HiddenEvaluator:
        row = self.db.execute("SELECT body FROM hidden WHERE exercise_id = ?", (exercise_id,)).fetchone()
        return HiddenEvaluator.model_validate_json(row[0])

    def help_context(self, exercise_id: str) -> dict[str, Any]:
        """What a helper model may see: reviewed cards and the visible prompt. Never hidden data or solutions."""
        exercise = self._exercise(exercise_id)
        cards = []
        for concept_id in exercise.concept_ids:
            found = self.card(concept_id)
            if found is not None:
                card = found[0]
                cards.append({"concept_id": card.concept_id, "title": card.title,
                              "explanations": card.explanations, "misconceptions": card.misconceptions})
        return {"exercise_id": exercise_id, "prompt": exercise.prompt, "cards": cards}

    # ----------------------------------------------------------- learners

    def set_mode(self, learner: str, mode: Mode, *, record_timing: bool = False) -> dict[str, Any]:
        self.db.execute("INSERT INTO settings VALUES (?, ?, ?) ON CONFLICT (learner) DO UPDATE SET "
                        "mode = excluded.mode, record_timing = excluded.record_timing",
                        (learner, mode, int(record_timing)))
        return MODE_SETTINGS[mode]

    def settings(self, learner: str) -> dict[str, Any]:
        row = self.db.execute("SELECT mode, record_timing FROM settings WHERE learner = ?", (learner,)).fetchone()
        mode, timing = (row[0], bool(row[1])) if row else ("build", False)
        return {"mode": mode, "record_timing": timing, **MODE_SETTINGS[mode]}

    def start(self, learner: str, exercise_id: str) -> str:
        self._exercise(exercise_id)
        session_id = new_id("job")
        self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, 0)", (session_id, learner, exercise_id, self._now()))
        return session_id

    def show_hint(self, session_id: str, hint: str) -> None:
        """Validate, then record the hint before it is shown. Recording it makes the attempt assisted."""
        row = self.db.execute("SELECT exercise_id, submitted FROM sessions WHERE session_id = ?",
                              (session_id,)).fetchone()
        if row is None or row[1]:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no open session for this hint")
        hidden = self._hidden(row[0])
        problems = check_hint(hint, hidden.answers, hidden.outputs)
        if problems:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "hint rejected: " + "; ".join(problems))
        self.db.execute("INSERT INTO hints VALUES (?, ?, ?)", (session_id, self._now(), hint))

    def submit(self, session_id: str, response: str | dict[str, str], *, supersedes: str | None = None,
               duration_ms: int | None = None) -> dict[str, Any]:
        row = self.db.execute("SELECT learner, exercise_id, submitted FROM sessions WHERE session_id = ?",
                              (session_id,)).fetchone()
        if row is None or row[2]:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no open session to submit")
        learner, exercise_id = row[0], row[1]
        exercise, hidden = self._exercise(exercise_id), self._hidden(exercise_id)
        if exercise.kind == "short_answer":
            if not isinstance(response, str):
                raise ForgeError(ErrorCode.INVALID_REQUEST, "a short answer is text")
            accepted = {_normalize_answer(a) for a in hidden.answers}
            result, detail = ("pass", "matches an accepted answer") if _normalize_answer(response) in accepted \
                else ("fail", "does not match an accepted answer")
        else:
            if self.code_grader is None or not isinstance(response, dict):
                result, detail = "error", "no code grader is available"
            else:
                result, detail = self.code_grader(response, hidden.suite or {})
        if supersedes is not None:
            previous = self.db.execute("SELECT learner, exercise_id FROM attempts WHERE attempt_id = ?",
                                       (supersedes,)).fetchone()
            if previous != (learner, exercise_id):
                raise ForgeError(ErrorCode.INVALID_REQUEST, "a correction supersedes this learner's attempt "
                                                            "at the same exercise")
        timing = duration_ms if self.settings(learner)["record_timing"] else None
        attempt_id = new_id("job").replace("job_", "attempt_")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            hints = self.db.execute("SELECT count(*) FROM hints WHERE session_id = ?", (session_id,)).fetchone()[0]
            mode = "assisted" if hints else "independent"
            self.db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                attempt_id, session_id, learner, exercise_id, mode, hints, result, detail,
                digest_json(response if isinstance(response, dict) else {"text": response}), supersedes, timing,
                self._now()))
            self.db.execute("UPDATE sessions SET submitted = 1 WHERE session_id = ?", (session_id,))
        return {"attempt_id": attempt_id, "mode": mode, "result": result, "detail": detail, "hints_used": hints}

    def attempts(self, learner: str) -> list[dict[str, Any]]:
        columns = ["attempt_id", "session_id", "learner", "exercise_id", "mode", "hints_used", "result", "detail",
                   "response_digest", "supersedes", "duration_ms", "recorded_at"]
        rows = self.db.execute("SELECT * FROM attempts WHERE learner = ? ORDER BY rowid", (learner,)).fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def evidence(self, learner: str) -> dict[str, dict[str, Any]]:
        """Evidence level per concept, with the attempts behind it. Not a competence score."""
        exercises = {row[0]: Exercise.model_validate_json(row[1])
                     for row in self.db.execute("SELECT exercise_id, body FROM exercises")}
        started = {row[0] for row in self.db.execute("SELECT exercise_id FROM sessions WHERE learner = ?", (learner,))}
        report: dict[str, dict[str, Any]] = {}
        for exercise in exercises.values():
            for concept_id in exercise.concept_ids:
                report.setdefault(concept_id, {"level": "not_started", "independent_contexts": [],
                                               "assisted_passes": 0, "independent_passes": 0, "attempts": 0})
        for attempt in self.attempts(learner):
            exercise = exercises[attempt["exercise_id"]]
            for concept_id in exercise.concept_ids:
                entry = report[concept_id]
                entry["attempts"] += 1
                if attempt["result"] == "pass" and attempt["mode"] == "assisted":
                    entry["assisted_passes"] += 1
                if attempt["result"] == "pass" and attempt["mode"] == "independent":
                    entry["independent_passes"] += 1
                    if exercise.context not in entry["independent_contexts"]:
                        entry["independent_contexts"].append(exercise.context)
        for exercise_id in started:
            for concept_id in exercises[exercise_id].concept_ids:
                if report[concept_id]["level"] == "not_started":
                    report[concept_id]["level"] = "introduced"
        for entry in report.values():
            if len(entry["independent_contexts"]) >= 2:
                entry["level"] = "demonstrated_across_contexts"
            elif entry["independent_passes"]:
                entry["level"] = "demonstrated_independently"
            elif entry["assisted_passes"]:
                entry["level"] = "practiced_with_support"
        return report

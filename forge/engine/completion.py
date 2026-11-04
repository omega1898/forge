"""evaluate_completion: may this task be marked completed, and why?

Completion is computed from records, never asserted. For a task with
required criteria, every criterion must have a current assessment for the
chosen snapshot:

- `pass` satisfies the criterion;
- `not_applicable` satisfies it only with a recorded reason (limitation);
- `fail`, `error`, `inconclusive`, `not_run` block it, unless an approved,
  unexpired waiver decision covers that exact criterion revision AND the
  criterion's waiver policy allows waivers. A waiver never changes the
  assessment: the task completes "with waiver", visibly.

A decide task completes when a decision about it has a final disposition.
A task with no criteria completes only by explicit acknowledgment from a
trusted human or FORGE itself; a model cannot declare work done.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from forge.contracts.base import RevisionRef, StrictModel
from forge.contracts.clock import parse_timestamp
from forge.contracts.records import Assessment, Criterion, Decision, Task

CompletionReason = Literal[
    "criteria_satisfied",
    "completed_with_waiver",
    "decision_recorded",
    "acknowledged_without_criteria",
    "not_eligible",
]


class BlockingResult(StrictModel):
    criterion_ref: RevisionRef
    assessment_id: str
    assessment_result: str
    waiver_forbidden: bool


class CompletionEvaluation(StrictModel):
    eligible: bool
    reason: CompletionReason
    snapshot_id: str | None
    satisfied: list[RevisionRef] = Field(default_factory=list)
    missing_criteria: list[RevisionRef] = Field(default_factory=list)
    blocking_results: list[BlockingResult] = Field(default_factory=list)
    applicable_waivers: list[str] = Field(default_factory=list)
    used_assessments: list[str] = Field(default_factory=list)
    explanation: str


def evaluate_completion(
    task: Task,
    *,
    snapshot_id: str | None,
    criteria: dict[str, Criterion],
    assessments: list[Assessment],
    decisions: list[Decision],
    acknowledged_by_trusted_actor: bool,
    now: datetime,
) -> CompletionEvaluation:
    if task.task_type == "decide":
        final = [
            d for d in decisions
            if d.subject_ref.id == task.task_id and d.disposition in ("approved", "rejected", "withdrawn")
        ]
        if final:
            return CompletionEvaluation(
                eligible=True, reason="decision_recorded", snapshot_id=None,
                explanation=f"decision {final[-1].decision_id} was {final[-1].disposition}",
            )
        return CompletionEvaluation(
            eligible=False, reason="not_eligible", snapshot_id=None,
            explanation="no final decision has been recorded for this task",
        )

    if not task.criterion_refs:
        if acknowledged_by_trusted_actor:
            return CompletionEvaluation(
                eligible=True, reason="acknowledged_without_criteria", snapshot_id=snapshot_id,
                explanation="no criteria are attached; completion was acknowledged by a trusted actor",
            )
        return CompletionEvaluation(
            eligible=False, reason="not_eligible", snapshot_id=snapshot_id,
            explanation="no criteria are attached, so only a trusted human or FORGE may acknowledge completion",
        )

    satisfied: list[RevisionRef] = []
    missing: list[RevisionRef] = []
    blocking: list[BlockingResult] = []
    waivers_used: list[str] = []
    used: list[str] = []
    for ref in task.criterion_refs:
        candidates = [
            a for a in assessments
            if a.criterion_ref.id == ref.id
            and a.criterion_ref.revision == ref.revision
            and (snapshot_id is None or a.snapshot_id == snapshot_id)
        ]
        if not candidates:
            missing.append(ref)
            continue
        # The event log orders records; timestamps can tie within a millisecond.
        latest = max(candidates, key=lambda a: (a.recorded_seq or 0, a.created_at))
        used.append(latest.assessment_id)
        result = latest.assessment_result
        if result == "pass" or (result == "not_applicable" and latest.limitations):
            satisfied.append(ref)
            continue
        criterion = criteria.get(ref.id)
        waiver_forbidden = criterion is None or criterion.waiver_policy == "forbidden"
        waiver = None if waiver_forbidden else _applicable_waiver(ref, decisions, now)
        if waiver is not None:
            waivers_used.append(waiver)
            satisfied.append(ref)
        else:
            blocking.append(
                BlockingResult(
                    criterion_ref=ref, assessment_id=latest.assessment_id,
                    assessment_result=result, waiver_forbidden=waiver_forbidden,
                )
            )

    eligible = not missing and not blocking
    if eligible:
        reason: CompletionReason = "completed_with_waiver" if waivers_used else "criteria_satisfied"
        explanation = (
            f"all {len(satisfied)} required criteria satisfied"
            + (f", {len(waivers_used)} by approved waiver" if waivers_used else "")
        )
    else:
        reason = "not_eligible"
        parts = []
        if missing:
            parts.append(f"{len(missing)} criteria have no assessment for this snapshot")
        if blocking:
            parts.append(
                f"{len(blocking)} criteria are assessed "
                + ", ".join(sorted({b.assessment_result for b in blocking}))
            )
        explanation = "; ".join(parts)
    return CompletionEvaluation(
        eligible=eligible,
        reason=reason,
        snapshot_id=snapshot_id,
        satisfied=satisfied,
        missing_criteria=missing,
        blocking_results=blocking,
        applicable_waivers=waivers_used,
        used_assessments=used,
        explanation=explanation,
    )


def _applicable_waiver(ref: RevisionRef, decisions: list[Decision], now: datetime) -> str | None:
    for decision in decisions:
        if (
            decision.decision_kind == "waiver"
            and decision.disposition == "approved"
            and decision.subject_ref.id == ref.id
            and decision.subject_ref.revision == ref.revision
            and (decision.expires_at is None or parse_timestamp(decision.expires_at) > now)
        ):
            return decision.decision_id
    return None

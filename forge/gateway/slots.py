"""Model slots (ticket D04): narrow, typed, checkable model calls.

A slot replaces "let the model follow a long skill" with one bounded
question whose answer code can reject:

    slot = SlotContract(output_model=..., validators=(...), max_attempts=2)
    result = run_slot(gateway, slot, context, task_prompt)

The output schema is flat (grammar-safe), the reply is validated in full by
Pydantic, then by the slot's own validators, which check facts against the
records rather than trusting the model:

- cited record IDs must be ones the context actually supplied;
- quoted text must appear byte-for-byte in the cited source span;
- numbers stated in prose must equal the record values they describe;
- counts, lengths, sentence templates and banned words follow the skill's
  stated limits.

A rejected answer gets one bounded correction attempt with the specific
errors. An identical failing answer counts as no progress. When attempts
run out the slot returns `fallback`, and the caller shows a decision card
or uses a deterministic default; the slot never returns an unchecked guess.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from forge.contracts.digests import digest_json
from forge.gateway.contracts import Message
from forge.gateway.schema_subset import flat_schema

Validator = Callable[[BaseModel, "SlotContext"], list[str]]


@dataclass
class SlotContext:
    """Facts the controller supplied to the call, used to check the answer."""

    allowed_record_ids: set[str] = field(default_factory=set)
    source_spans: dict[str, str] = field(default_factory=dict)  # span_id -> exact text
    record_values: dict[str, Any] = field(default_factory=dict)  # name -> value from records


@dataclass(frozen=True)
class SlotContract:
    slot_id: str
    revision: int
    instructions: str
    output_model: type[BaseModel]
    validators: tuple[Validator, ...] = ()
    max_attempts: int = 2


@dataclass
class SlotResult:
    status: Literal["valid", "fallback"]
    output: dict[str, Any] | None
    errors: list[str]
    attempts: int
    call_ids: list[str]
    reason: str = ""


def run_slot(
    gateway,  # noqa: ANN001 - ModelGateway
    slot: SlotContract,
    context: SlotContext,
    prompt: str,
    *,
    model_config_id: str,
    task_id: str | None = None,
    deadline_ms: int = 60_000,
) -> SlotResult:
    schema = flat_schema(slot.output_model)
    messages = [Message(role="system", content=slot.instructions), Message(role="user", content=prompt)]
    seen: set[str] = set()
    call_ids: list[str] = []
    errors: list[str] = []
    for attempt in range(1, slot.max_attempts + 1):
        result = gateway.generate(
            model_config_id=model_config_id, slot_id=slot.slot_id, messages=messages,
            output_model=slot.output_model, output_schema=schema, task_id=task_id, deadline_ms=deadline_ms,
        )
        call_ids.append(result.call_id)
        if result.status != "valid":
            errors = [f"{result.status}: {error}" for error in result.errors] or [result.status]
            if result.status in ("context_infeasible", "policy_rejected", "cancelled", "cancel_unconfirmed"):
                break  # retrying cannot help
        else:
            parsed = slot.output_model.model_validate(result.output)
            errors = [problem for check in slot.validators for problem in check(parsed, context)]
            if not errors:
                return SlotResult("valid", result.output, [], attempt, call_ids)
            fingerprint = digest_json(result.output)
            if fingerprint in seen:
                return SlotResult("fallback", None, errors, attempt, call_ids, reason="no_progress")
            seen.add(fingerprint)
        messages = messages + [Message(
            role="user",
            content="Your previous answer was rejected:\n- " + "\n- ".join(errors[:10])
            + "\nAnswer again, fixing only these problems.",
        )]
    return SlotResult("fallback", None, errors, slot.max_attempts, call_ids, reason="attempts_exhausted")


# ---------------------------------------------------------------- validators


def cites_known_records(field_name: str = "record_ids") -> Validator:
    def check(output: BaseModel, context: SlotContext) -> list[str]:
        cited = getattr(output, field_name, []) or []
        unknown = [item for item in cited if item not in context.allowed_record_ids]
        return [f"cites {item}, which was not in the supplied context" for item in unknown[:5]]

    return check


def quotes_match_sources(field_name: str = "citations") -> Validator:
    """Each citation is {"span_id": ..., "quote": ...}; the quote must be exact."""

    def check(output: BaseModel, context: SlotContext) -> list[str]:
        problems = []
        for citation in getattr(output, field_name, []) or []:
            span_id, quote = citation.span_id, citation.quote
            source = context.source_spans.get(span_id)
            if source is None:
                problems.append(f"citation names unknown span {span_id!r}")
            elif quote not in source:
                problems.append(f"quoted text does not appear verbatim in span {span_id!r}")
        return problems

    return check


_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def numbers_match_records(field_name: str, allowed_keys: tuple[str, ...]) -> Validator:
    """Fact lock: every number in the prose must equal one of the named record values."""

    def check(output: BaseModel, context: SlotContext) -> list[str]:
        text = getattr(output, field_name) or ""
        allowed = {str(context.record_values[key]) for key in allowed_keys if key in context.record_values}
        stated = _NUMBER.findall(text)
        wrong = [number for number in stated if number not in allowed]
        return [f"{field_name} states {number}, which matches no recorded value" for number in wrong[:5]]

    return check


def count_between(field_name: str, low: int, high: int) -> Validator:
    def check(output: BaseModel, context: SlotContext) -> list[str]:
        count = len(getattr(output, field_name) or [])
        return [] if low <= count <= high else [f"{field_name} has {count} items; required {low} to {high}"]

    return check


def max_words(field_name: str, limit: int) -> Validator:
    def check(output: BaseModel, context: SlotContext) -> list[str]:
        words = len((getattr(output, field_name) or "").split())
        return [] if words <= limit else [f"{field_name} has {words} words; the limit is {limit}"]

    return check


def banned_words(field_name: str, words: tuple[str, ...]) -> Validator:
    def check(output: BaseModel, context: SlotContext) -> list[str]:
        values = getattr(output, field_name)
        texts = values if isinstance(values, list) else [values or ""]
        found = sorted({w for w in words for t in texts if re.search(rf"\b{re.escape(w)}\b", t, re.IGNORECASE)})
        return [f"{field_name} uses vague word(s): {', '.join(found)}"] if found else []

    return check


def matches_template(field_name: str, pattern: str, description: str) -> Validator:
    compiled = re.compile(pattern)

    def check(output: BaseModel, context: SlotContext) -> list[str]:
        values = getattr(output, field_name)
        texts = values if isinstance(values, list) else [values or ""]
        bad = [text for text in texts if not compiled.fullmatch(text.strip())]
        return [f"{field_name} item does not follow {description}: {bad[0][:80]!r}"] if bad else []

    return check

"""Capability evaluation harness (ticket D11): does FORGE actually boost a small model?

The skill-creator method with its gaps fixed:

- Frozen sets. An evaluation set is digest-locked. It is split once,
  deterministically (seed 42, 60/40), into a development part used while
  tuning prompts and slots, and a held-out part sealed in a vault. Tuning
  code receives only the development view; the held-out cases are released
  once per recorded final run, and every access is logged. Changing a case
  changes the digest, so a "frozen" set cannot drift quietly.
- Deterministic assertions first. Each case carries checks evaluated by
  code (equals, contains, regex, JSON fields, a numeric range). Model
  grading is not part of this harness.
- Repeated runs. Each case runs several times; flaky behavior shows up as
  disagreement between repetitions instead of a lucky single pass.
- Intervals, not point scores. Pass rates come with Wilson 95% intervals;
  the difference between two variants (for example the small model alone
  versus the small model inside FORGE) comes with a Newcombe interval. A
  difference whose interval includes zero is reported as not shown.
- Recorded baseline. A report names the set digest, the variants and every
  per-case outcome, so a later run is compared against evidence, not memory.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.digests import digest_json
from forge.contracts.errors import ErrorCode, ForgeError

Z95 = 1.959963984540054


class Assertion(StrictModel):
    kind: Literal["equals", "contains", "not_contains", "regex", "json_field_equals", "number_between"]
    value: Any = None
    path: str | None = None          # dotted path for json_field_equals
    low: float | None = None
    high: float | None = None


class EvalCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    input: dict[str, Any]
    assertions: list[Assertion] = Field(min_length=1)


def check(assertion: Assertion, output: str) -> bool:
    if assertion.kind == "equals":
        return output.strip() == str(assertion.value).strip()
    if assertion.kind == "contains":
        return str(assertion.value) in output
    if assertion.kind == "not_contains":
        return str(assertion.value) not in output
    if assertion.kind == "regex":
        return re.search(str(assertion.value), output) is not None
    if assertion.kind == "number_between":
        try:
            number = float(output.strip())
        except ValueError:
            return False
        return (assertion.low is None or number >= assertion.low) and (assertion.high is None or number <= assertion.high)
    try:
        value: Any = json.loads(output)
    except ValueError:
        return False
    for part in (assertion.path or "").split("."):
        if not part:
            continue
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return value == assertion.value


def wilson(successes: int, trials: int) -> tuple[float, float]:
    if trials == 0:
        return 0.0, 1.0
    p = successes / trials
    denominator = 1 + Z95 ** 2 / trials
    centre = (p + Z95 ** 2 / (2 * trials)) / denominator
    half = Z95 * math.sqrt(p * (1 - p) / trials + Z95 ** 2 / (4 * trials ** 2)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe(s1: int, n1: int, s2: int, n2: int) -> tuple[float, float, float]:
    """Difference p1 - p2 with Newcombe's hybrid score interval (method 10)."""
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = wilson(s1, n1)
    l2, u2 = wilson(s2, n2)
    difference = p1 - p2
    low = difference - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = difference + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return difference, low, high


class EvalSet:
    def __init__(self, name: str, cases: list[EvalCase], *, held_out_fraction: float = 0.4, seed: int = 42) -> None:
        ids = [case.case_id for case in cases]
        if len(set(ids)) != len(ids):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "case ids must be unique")
        self.name = name
        self.digest = digest_json({"name": name, "cases": [c.model_dump(mode="json") for c in cases],
                                   "held_out_fraction": held_out_fraction, "seed": seed})
        # Split per capability so every capability is represented on both sides.
        held: set[str] = set()
        for capability in sorted({c.capability for c in cases}):
            members = sorted(c.case_id for c in cases if c.capability == capability)
            random.Random(f"{seed}:{capability}").shuffle(members)
            held.update(members[:max(1, round(len(members) * held_out_fraction))] if len(members) > 1 else [])
        self._development = [c for c in cases if c.case_id not in held]
        self._held_out = [c for c in cases if c.case_id in held]
        self.access_log: list[str] = []
        self._final_runs = 0

    def development(self) -> list[EvalCase]:
        """The only view tuning code may use."""
        self.access_log.append("development")
        return list(self._development)

    def held_out(self, *, purpose: str) -> list[EvalCase]:
        """Released only for a recorded final evaluation."""
        if purpose != "final_evaluation":
            self.access_log.append(f"refused:{purpose}")
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "the held-out set is sealed; only a final evaluation reads it")
        self._final_runs += 1
        self.access_log.append(f"final_evaluation:{self._final_runs}")
        return list(self._held_out)


@dataclass
class VariantResult:
    variant: str
    per_case: dict[str, list[bool]] = field(default_factory=dict)

    def counts(self, cases: list[EvalCase], capability: str | None = None) -> tuple[int, int]:
        selected = [c.case_id for c in cases if capability is None or c.capability == capability]
        outcomes = [ok for case_id in selected for ok in self.per_case.get(case_id, [])]
        return sum(outcomes), len(outcomes)


def run_variant(name: str, system: Callable[[dict[str, Any], int], str], cases: list[EvalCase], *,
                repeats: int = 3) -> VariantResult:
    result = VariantResult(name)
    for case in cases:
        outcomes = []
        for repetition in range(repeats):
            try:
                output = system(case.input, repetition)
            except Exception:  # noqa: BLE001 - a crashing system fails the case, it does not stop the run
                output = ""
            outcomes.append(isinstance(output, str) and all(check(a, output) for a in case.assertions))
        result.per_case[case.case_id] = outcomes
    return result


def compare_variants(evalset: EvalSet, baseline: VariantResult, treatment: VariantResult,
                     cases: list[EvalCase]) -> dict[str, Any]:
    """Per capability: both pass rates with intervals, the difference, and whether it is shown."""
    report: dict[str, Any] = {"set": evalset.name, "set_digest": evalset.digest, "baseline": baseline.variant,
                              "treatment": treatment.variant, "capabilities": {}}
    for capability in sorted({c.capability for c in cases}):
        s1, n1 = treatment.counts(cases, capability)
        s0, n0 = baseline.counts(cases, capability)
        difference, low, high = newcombe(s1, n1, s0, n0)
        flaky = [c.case_id for c in cases if c.capability == capability
                 for variant in (baseline, treatment) if len(set(variant.per_case.get(c.case_id, []))) > 1]
        report["capabilities"][capability] = {
            "baseline": {"passes": s0, "trials": n0, "rate": round(s0 / n0, 4), "interval": [round(x, 4) for x in wilson(s0, n0)]},
            "treatment": {"passes": s1, "trials": n1, "rate": round(s1 / n1, 4), "interval": [round(x, 4) for x in wilson(s1, n1)]},
            "difference": round(difference, 4), "difference_interval": [round(low, 4), round(high, 4)],
            "conclusion": "improvement shown" if low > 0 else ("regression shown" if high < 0 else "difference not shown"),
            "flaky_cases": sorted(set(flaky)),
        }
    report["per_case"] = {"baseline": baseline.per_case, "treatment": treatment.per_case}
    return report

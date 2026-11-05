"""Variance bridge and reconciliation (ticket D09).

Distilled from the variance-analysis and reconciliation skills, with their
arithmetic and vocabulary defects fixed and the invariants enforced in code.

Bridge. A change in a total is split into drivers that must sum EXACTLY to
the change ("start + drivers = end"). The skill's price/volume formulas
omit the interaction term (change in volume x change in price); on the
skill's own example the drivers then sum to +30,000 against an actual
change of +28,000. Here every bridge is computed in exact rational
arithmetic and checked; a bridge that does not tie out raises instead of
being reported. Additive bridges apply to totals and averages of additive
quantities only; percentiles and ratios are refused.

Reconciliation. Two record sets are matched by key into three buckets:
matched, timing differences (one-sided items still young enough to be in
transit) and exceptions (value mismatches, and one-sided items past the
timing window). Exceptions are aged into non-overlapping buckets. The
counts tie out to both inputs, and the reconciler must be a different actor
from whoever produced the data being reconciled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

from forge.contracts.errors import ErrorCode, ForgeError

ADDITIVE_METRICS = ("total", "sum", "count", "cost", "mean")
INVESTIGATION_ORDER = ("data completeness", "volume", "mix", "rate", "new and removed items", "interaction")


def _exact(value: Any) -> Fraction:
    if isinstance(value, float):
        # Floats carry binary rounding; go through their shortest decimal text.
        return Fraction(repr(value))
    return Fraction(value)


# -------------------------------------------------------------- two-factor bridge


@dataclass(frozen=True)
class Bridge:
    start: Fraction
    end: Fraction
    drivers: dict[str, Fraction]

    @property
    def change(self) -> Fraction:
        return self.end - self.start

    def ties_out(self) -> bool:
        return self.start + sum(self.drivers.values(), Fraction(0)) == self.end

    def as_floats(self) -> dict[str, float]:
        return {name: float(value) for name, value in self.drivers.items()}


def _checked(bridge: Bridge) -> Bridge:
    if not bridge.ties_out():
        gap = bridge.change - sum(bridge.drivers.values(), Fraction(0))
        raise ForgeError(ErrorCode.INTERNAL_ERROR, f"bridge does not tie out: drivers miss the change by {float(gap)}")
    return bridge


def price_volume_bridge(base_volume: Any, base_price: Any, actual_volume: Any, actual_price: Any, *,
                        include_interaction: bool = True) -> Bridge:
    """Revenue (or cost) = volume x price, split into volume, price and interaction effects."""
    v0, p0, v1, p1 = (_exact(x) for x in (base_volume, base_price, actual_volume, actual_price))
    drivers = {"volume": (v1 - v0) * p0, "price": (p1 - p0) * v0}
    if include_interaction:
        drivers["interaction"] = (v1 - v0) * (p1 - p0)
    return _checked(Bridge(start=v0 * p0, end=v1 * p1, drivers=drivers))


# ----------------------------------------------------- multi-item cost bridge


def cost_bridge(base: Mapping[str, tuple[Any, Any]], actual: Mapping[str, tuple[Any, Any]],
                metric: str = "total") -> Bridge:
    """Total cost = sum over items of count x unit cost (for example requests x CPU-ms per request).

    Drivers, which sum exactly to the change:
    - volume: change in total count at the base average unit cost;
    - mix: shift in the share of items, valued at base unit costs;
    - rate: change in unit costs, weighted by actual counts (includes the interaction);
    - new_items / removed_items: items present on only one side.
    """
    if metric not in ADDITIVE_METRICS:
        raise ForgeError(ErrorCode.INVALID_REQUEST,
                         f"{metric!r} is not additive; percentiles and ratios cannot be bridged by addition")
    b = {key: (_exact(n), _exact(c)) for key, (n, c) in base.items()}
    a = {key: (_exact(n), _exact(c)) for key, (n, c) in actual.items()}
    if any(n < 0 for n, _ in [*b.values(), *a.values()]):
        raise ForgeError(ErrorCode.INVALID_REQUEST, "counts cannot be negative")
    start = sum((n * c for n, c in b.values()), Fraction(0))
    end = sum((n * c for n, c in a.values()), Fraction(0))
    shared = sorted(set(b) & set(a))
    new = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    base_count = sum((n for n, _ in b.values()), Fraction(0))
    shared_actual_count = sum((a[k][0] for k in shared), Fraction(0))
    average = start / base_count if base_count else Fraction(0)
    # Removed items' base cost leaves; new items' actual cost arrives.
    removed_cost = sum((b[k][0] * b[k][1] for k in removed), Fraction(0))
    removed_count = sum((b[k][0] for k in removed), Fraction(0))
    drivers = {
        "volume": (shared_actual_count - (base_count - removed_count)) * average,
        "mix": sum((a[k][0] * b[k][1] for k in shared), Fraction(0))
               - (start - removed_cost) - (shared_actual_count - (base_count - removed_count)) * average,
        "rate": sum((a[k][0] * (a[k][1] - b[k][1]) for k in shared), Fraction(0)),
        "new_items": sum((a[k][0] * a[k][1] for k in new), Fraction(0)),
        "removed_items": -removed_cost,
    }
    return _checked(Bridge(start=start, end=end, drivers=drivers))


def exceeds_threshold(change: Any, base: Any, *, absolute: Any, percent: Any) -> bool:
    """Triggered when EITHER the absolute or the percentage limit is exceeded."""
    change, base = _exact(change), _exact(base)
    if abs(change) > _exact(absolute):
        return True
    return base != 0 and abs(change) / abs(base) * 100 > _exact(percent)


# ------------------------------------------------------------- reconciliation

AGING_BUCKETS = ((0, 30, "0-30 days"), (31, 60, "31-60 days"), (61, 90, "61-90 days"), (91, None, "over 90 days"))


def aging_bucket(age_days: int) -> str:
    if age_days < 0:
        raise ValueError("an item cannot be dated in the future")
    for low, high, label in AGING_BUCKETS:
        if age_days >= low and (high is None or age_days <= high):
            return label
    raise AssertionError("aging buckets must cover every age")  # pragma: no cover


@dataclass
class Reconciliation:
    matched: list[str] = field(default_factory=list)
    timing: list[str] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    left_count: int = 0
    right_count: int = 0

    def ties_out(self) -> bool:
        left_only = sum(1 for e in self.exceptions if e["kind"] == "left_only")
        right_only = sum(1 for e in self.exceptions if e["kind"] == "right_only")
        mismatched = sum(1 for e in self.exceptions if e["kind"] == "mismatch")
        timing_left = sum(1 for t in self.timing if t.startswith("left:"))
        timing_right = len(self.timing) - timing_left
        return (len(self.matched) + mismatched + left_only + timing_left == self.left_count
                and len(self.matched) + mismatched + right_only + timing_right == self.right_count)


def reconcile(left: Mapping[str, Mapping[str, Any]], right: Mapping[str, Mapping[str, Any]], *,
              fields: list[str], as_of: date, timing_window_days: int, dated_by: str = "date",
              prepared_by: str, reconciled_by: str) -> Reconciliation:
    """Match two keyed record sets. Values are compared exactly; no tolerance hides a cent."""
    if prepared_by == reconciled_by:
        raise ForgeError(ErrorCode.PERMISSION_DENIED, "the reconciler must not be the actor who produced the data")
    result = Reconciliation(left_count=len(left), right_count=len(right))
    for key in sorted(set(left) | set(right)):
        if key in left and key in right:
            differences = {name: {"left": left[key].get(name), "right": right[key].get(name)}
                           for name in fields if left[key].get(name) != right[key].get(name)}
            if differences:
                age = (as_of - _as_date(left[key].get(dated_by), as_of)).days
                result.exceptions.append({"key": key, "kind": "mismatch", "differences": differences,
                                          "aging": aging_bucket(max(age, 0))})
            else:
                result.matched.append(key)
            continue
        side, record = ("left", left[key]) if key in left else ("right", right[key])
        age = (as_of - _as_date(record.get(dated_by), as_of)).days
        if 0 <= age <= timing_window_days:
            result.timing.append(f"{side}:{key}")
        else:
            result.exceptions.append({"key": key, "kind": f"{side}_only", "aging": aging_bucket(max(age, 0))})
    if not result.ties_out():  # pragma: no cover - a defect in this function, not in the data
        raise ForgeError(ErrorCode.INTERNAL_ERROR, "reconciliation buckets do not tie out to the inputs")
    return result


def _as_date(value: Any, default: date) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return default

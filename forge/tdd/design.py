"""Functional (specification-based) test design as deterministic code.

Each technique turns a written specification into test cases, and checks
the specification itself while doing so. None of them needs a model; a
model can propose the specification, and these functions decide coverage.

- Equivalence partitioning and boundary value analysis over declared input
  domains (integer ranges, lengths, enumerations).
- Pairwise (all-pairs) combinations: every value of every parameter appears
  with every value of every other parameter at least once, verified.
- Decision tables: completeness (every combination of conditions has a
  rule), consistency (no combination maps to two different actions) and
  one case per rule.
- State machines: determinism, reachability, dead ends; a transition tour
  covering every transition; "sneak path" cases for every (state, event)
  pair the specification does not allow.
- `render_unittest`: turn cases into a standard-library test module, with
  values written as Python literals (never as interpolated code), ready to
  be the failing tests of a TDD red phase.
"""

from __future__ import annotations

import itertools
import keyword
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from forge.contracts.errors import ErrorCode, ForgeError

# --------------------------------------------- partitions and boundaries


@dataclass(frozen=True)
class IntRange:
    low: int
    high: int

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError("low must not exceed high")


@dataclass(frozen=True)
class Length:
    """Length of a string or list, within [low, high]."""

    low: int
    high: int
    fill: str = "a"

    def __post_init__(self) -> None:
        if not 0 <= self.low <= self.high:
            raise ValueError("lengths must satisfy 0 <= low <= high")


@dataclass(frozen=True)
class OneOf:
    values: tuple[Any, ...]
    invalid_examples: tuple[Any, ...] = ()


@dataclass(frozen=True)
class DesignCase:
    name: str
    value: Any
    valid: bool
    technique: str


def boundary_values(domain: IntRange | Length) -> list[DesignCase]:
    """Just outside, on and just inside each edge, plus a nominal value."""
    low, high = domain.low, domain.high
    points = [(low - 1, False, "below_minimum"), (low, True, "minimum"), (low + 1, True, "above_minimum"),
              ((low + high) // 2, True, "nominal"), (high - 1, True, "below_maximum"), (high, True, "maximum"),
              (high + 1, False, "above_maximum")]
    cases: list[DesignCase] = []
    seen: set[int] = set()
    for point, valid, label in points:
        if point in seen or (valid and not low <= point <= high):
            continue
        if isinstance(domain, Length) and point < 0:
            continue
        seen.add(point)
        value = domain.fill * point if isinstance(domain, Length) else point
        cases.append(DesignCase(label, value, low <= point <= high, "boundary_value"))
    return cases


def equivalence_classes(domain: IntRange | Length | OneOf) -> list[DesignCase]:
    """One representative per class: each valid class once, each invalid class once."""
    if isinstance(domain, OneOf):
        cases = [DesignCase(f"valid_{index}", value, True, "equivalence") for index, value in enumerate(domain.values)]
        cases += [DesignCase(f"invalid_{index}", value, False, "equivalence")
                  for index, value in enumerate(domain.invalid_examples)]
        return cases
    middle = (domain.low + domain.high) // 2
    make = (lambda n: domain.fill * n) if isinstance(domain, Length) else (lambda n: n)
    cases = [DesignCase("valid", make(middle), True, "equivalence")]
    if domain.low > 0 or isinstance(domain, IntRange):
        if not (isinstance(domain, Length) and domain.low == 0):
            cases.append(DesignCase("too_small", make(domain.low - 1), False, "equivalence"))
    cases.append(DesignCase("too_large", make(domain.high + 1), False, "equivalence"))
    return cases


# ----------------------------------------------------------------- pairwise


def pairwise(parameters: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """A small set of rows covering every pair of values (greedy, deterministic)."""
    names = list(parameters)
    if any(not values for values in parameters.values()):
        raise ForgeError(ErrorCode.INVALID_REQUEST, "every parameter needs at least one value")
    if len(names) < 2:
        return [{names[0]: v} for v in parameters[names[0]]] if names else []
    uncovered = {(a, i, b, j) for a, b in itertools.combinations(range(len(names)), 2)
                 for i in range(len(parameters[names[a]])) for j in range(len(parameters[names[b]]))}
    rows: list[list[int]] = []
    while uncovered:
        # Seed with the first uncovered pair, then fill other columns greedily.
        a, i, b, j = min(uncovered)
        row: list[int | None] = [None] * len(names)
        row[a], row[b] = i, j
        for column in range(len(names)):
            if row[column] is not None:
                continue
            best, best_gain = 0, -1
            for candidate in range(len(parameters[names[column]])):
                gain = sum(1 for other in range(len(names)) if other != column and row[other] is not None and
                           ((min(column, other), candidate if column < other else row[other],
                             max(column, other), row[other] if column < other else candidate) in uncovered))
                if gain > best_gain:
                    best, best_gain = candidate, gain
            row[column] = best
        complete = [int(value) for value in row]  # type: ignore[arg-type]
        for x, y in itertools.combinations(range(len(names)), 2):
            uncovered.discard((x, complete[x], y, complete[y]))
        rows.append(complete)
    return [{names[c]: parameters[names[c]][v] for c, v in enumerate(row)} for row in rows]


def uncovered_pairs(rows: list[dict[str, Any]], parameters: dict[str, list[Any]]) -> list[tuple]:
    """Pairs of values no row exercises (empty means full pairwise coverage)."""
    missing = []
    for a, b in itertools.combinations(list(parameters), 2):
        for x in parameters[a]:
            for y in parameters[b]:
                if not any(row.get(a) == x and row.get(b) == y for row in rows):
                    missing.append((a, x, b, y))
    return missing


# ------------------------------------------------------------ decision tables


@dataclass(frozen=True)
class Rule:
    name: str
    conditions: dict[str, bool | None]     # None: "does not matter"
    action: str


@dataclass
class DecisionTable:
    conditions: list[str]
    rules: list[Rule]

    def _matches(self, rule: Rule, combination: dict[str, bool]) -> bool:
        return all(value is None or combination[name] == value for name, value in rule.conditions.items())

    def check(self) -> list[str]:
        problems = []
        for rule in self.rules:
            unknown = set(rule.conditions) - set(self.conditions)
            if unknown:
                problems.append(f"rule {rule.name} names unknown conditions {sorted(unknown)}")
        if problems:
            return problems
        used: set[str] = set()
        for values in itertools.product([True, False], repeat=len(self.conditions)):
            combination = dict(zip(self.conditions, values, strict=True))
            matching = [rule for rule in self.rules if self._matches(rule, combination)]
            label = ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in combination.items())
            if not matching:
                problems.append(f"incomplete: no rule covers {label}")
            elif len({rule.action for rule in matching}) > 1:
                problems.append(f"contradiction: {label} matches {[r.name for r in matching]} with different actions")
            used.update(rule.name for rule in matching)
        problems += [f"rule {rule.name} can never apply" for rule in self.rules if rule.name not in used]
        return problems

    def cases(self) -> list[dict[str, Any]]:
        """One concrete case per rule ("does not matter" filled with False, then True if needed)."""
        problems = self.check()
        if problems:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "the decision table is not sound", details={"problems": problems})
        cases = []
        for rule in self.rules:
            combination = {name: bool(rule.conditions.get(name)) for name in self.conditions}
            cases.append({"rule": rule.name, "conditions": combination, "expected_action": rule.action})
        return cases


# ------------------------------------------------------------ state machines


@dataclass
class StateMachine:
    states: list[str]
    initial: str
    transitions: list[tuple[str, str, str]]          # (from, event, to)
    final: list[str] = field(default_factory=list)

    @property
    def events(self) -> list[str]:
        return sorted({event for _, event, _ in self.transitions})

    def check(self) -> list[str]:
        problems = []
        known = set(self.states)
        if self.initial not in known:
            problems.append(f"initial state {self.initial} is not declared")
        for source, event, target in self.transitions:
            if source not in known or target not in known:
                problems.append(f"transition {source} --{event}--> {target} uses an undeclared state")
        seen: dict[tuple[str, str], str] = {}
        for source, event, target in self.transitions:
            if (source, event) in seen and seen[(source, event)] != target:
                problems.append(f"nondeterministic: {source} on {event} goes to {seen[(source, event)]} and {target}")
            seen[(source, event)] = target
        reachable = self._reachable()
        problems += [f"state {s} is unreachable" for s in self.states if s not in reachable]
        exits = {source for source, _, _ in self.transitions}
        problems += [f"state {s} is a dead end but not final" for s in self.states
                     if s in reachable and s not in exits and s not in self.final]
        return problems

    def _reachable(self) -> set[str]:
        seen, queue = {self.initial}, deque([self.initial])
        while queue:
            state = queue.popleft()
            for source, _, target in self.transitions:
                if source == state and target not in seen:
                    seen.add(target)
                    queue.append(target)
        return seen

    def _path_to(self, goal: str) -> list[tuple[str, str, str]]:
        previous: dict[str, tuple[str, str, str]] = {}
        seen, queue = {self.initial}, deque([self.initial])
        while queue:
            state = queue.popleft()
            if state == goal:
                break
            for transition in sorted(self.transitions):
                source, _, target = transition
                if source == state and target not in seen:
                    seen.add(target)
                    previous[target] = transition
                    queue.append(target)
        path, state = [], goal
        while state != self.initial:
            path.append(previous[state])
            state = previous[state][0]
        return list(reversed(path))

    def transition_cases(self) -> list[dict[str, Any]]:
        """One case per transition: the shortest event sequence that reaches it, then the transition itself."""
        problems = [p for p in self.check() if not p.startswith("state") or "unreachable" in p]
        if any("nondeterministic" in p or "undeclared" in p for p in problems):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "the state machine is not sound", details={"problems": problems})
        cases = []
        for source, event, target in sorted(set(self.transitions)):
            if source not in self._reachable():
                continue
            path = self._path_to(source)
            cases.append({"events": [e for _, e, _ in path] + [event], "expected_state": target,
                          "covers": f"{source} --{event}--> {target}"})
        return cases

    def sneak_path_cases(self) -> list[dict[str, Any]]:
        """Every (reachable state, event) the specification does not allow must be rejected."""
        allowed = {(source, event) for source, event, _ in self.transitions}
        cases = []
        for state in sorted(self._reachable()):
            for event in self.events:
                if (state, event) not in allowed:
                    path = self._path_to(state)
                    cases.append({"events": [e for _, e, _ in path] + [event], "expected": "rejected",
                                  "state_before": state, "covers": f"{state} --{event}--> (not allowed)"})
        return cases


# ------------------------------------------------------------- rendering

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LITERAL_TYPES = (int, float, str, bytes, bool, type(None))


def _literal(value: Any) -> str:
    """repr() of plain data only, so a case value can never become code."""
    if isinstance(value, _LITERAL_TYPES):
        return repr(value)
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_literal(v) for v in value)
        return f"[{inner}]" if isinstance(value, list) else f"({inner}{',' if len(value) == 1 else ''})"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_literal(k)}: {_literal(v)}" for k, v in value.items()) + "}"
    raise ForgeError(ErrorCode.INVALID_REQUEST, f"case values must be plain data, not {type(value).__name__}")


def _identifier(text: str, what: str) -> str:
    if not _IDENTIFIER.match(text) or keyword.iskeyword(text):
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{what} {text!r} is not a plain Python identifier")
    return text


def render_unittest(module: str, function: str, cases: list[dict[str, Any]], *, class_name: str = "DesignedCases") -> str:
    """A unittest module: each case is {"name", "args", and "expected" or "raises"}."""
    for part in module.split("."):
        _identifier(part, "module name part")
    _identifier(function, "function name")
    _identifier(class_name, "class name")
    lines = ['"""Generated from a test design by FORGE. Values are literals; edit the design, not this file."""',
             "", "import unittest", "", f"from {module} import {function}", "", "",
             f"class {class_name}(unittest.TestCase):"]
    names: set[str] = set()
    for case in cases:
        name = _identifier("test_" + re.sub(r"[^A-Za-z0-9_]", "_", str(case["name"])).strip("_"), "test name")
        if name in names:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"duplicate test name {name}")
        names.add(name)
        args = ", ".join(_literal(a) for a in case.get("args", []))
        lines.append(f"    def {name}(self):")
        if "raises" in case:
            exception = _identifier(str(case["raises"]), "exception name")
            lines.append(f"        with self.assertRaises({exception}):")
            lines.append(f"            {function}({args})")
        else:
            lines.append(f"        self.assertEqual({function}({args}), {_literal(case['expected'])})")
        lines.append("")
    if not cases:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "no cases to render")
    return "\n".join(lines).rstrip() + "\n"

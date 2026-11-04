"""Dependency cycle detection (used by task.add_dependency)."""

from __future__ import annotations

from forge.engine.graph import find_cycle


def test_no_cycle_in_a_chain() -> None:
    assert find_cycle([("c", "b"), ("b", "a")]) is None


def test_two_node_cycle() -> None:
    assert find_cycle([("a", "b"), ("b", "a")]) == ["a", "b", "a"]


def test_cycle_hidden_behind_a_branch() -> None:
    edges = [("a", "b"), ("a", "x"), ("x", "y"), ("y", "x")]
    assert find_cycle(edges) == ["x", "y", "x"]


def test_diamond_is_not_a_cycle() -> None:
    edges = [("d", "b"), ("d", "c"), ("b", "a"), ("c", "a")]
    assert find_cycle(edges) is None


def test_long_hostile_chain_does_not_overflow_the_stack() -> None:
    length = 50_000
    edges = [(f"t{i}", f"t{i + 1}") for i in range(length)]
    assert find_cycle(edges) is None
    edges.append((f"t{length}", "t0"))
    cycle = find_cycle(edges)
    assert cycle is not None and cycle[0] == cycle[-1] and len(cycle) == length + 2

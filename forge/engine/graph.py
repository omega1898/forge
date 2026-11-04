"""Task dependency graph checks.

An edge (A, B) means "task A requires task B's output or completion".
A cycle (A needs B, B needs A) can never become ready, so a plan containing
one is rejected before anything is committed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable


def find_cycle(edges: Iterable[tuple[str, str]]) -> list[str] | None:
    """Return one cycle as a list of task IDs (first == last), or None.

    Iterative depth-first search, so a long hostile chain cannot exhaust
    Python's recursion limit.
    """
    graph: dict[str, list[str]] = defaultdict(list)
    for task, dependency in edges:
        graph[task].append(dependency)
        graph.setdefault(dependency, [])

    unvisited, in_progress, done = 0, 1, 2
    state = dict.fromkeys(graph, unvisited)
    for start in sorted(graph):
        if state[start] != unvisited:
            continue
        path: list[str] = [start]
        iterators = [iter(sorted(graph[start]))]
        state[start] = in_progress
        while iterators:
            next_node = next(iterators[-1], None)
            if next_node is None:
                state[path.pop()] = done
                iterators.pop()
            elif state[next_node] == in_progress:
                return path[path.index(next_node) :] + [next_node]
            elif state[next_node] == unvisited:
                state[next_node] = in_progress
                path.append(next_node)
                iterators.append(iter(sorted(graph[next_node])))
    return None

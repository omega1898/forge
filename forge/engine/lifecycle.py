"""Task status transitions allowed by the blueprint (Part 3, section 4).

The table says which moves are *possible*. Whether a particular move is
*permitted now* (inputs current, authority valid, completion predicate met)
is checked by the controller, and some gates belong to later tickets.
"""

from __future__ import annotations

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "blocked", "waiting_for_user", "cancelled"}),
    "running": frozenset({"waiting_for_user", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"queued", "cancelled"}),
    "waiting_for_user": frozenset({"queued", "cancelled"}),
    # Terminal states have no way out. A changed requirement creates a new
    # task that supersedes the old one; history is never overwritten.
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def is_allowed(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())

"""Machine-level resource scheduling for measurements (EX-T13).

A benchmark needs the machine to itself: model inference or indexing
running at the same time changes the numbers. The scheduler hands out
leases of a kind. "benchmark" is exclusive: it is granted only when no other
lease is active, and while a benchmark waits or runs, new shared work waits
behind it (so a stream of inference calls cannot starve a measurement).
Shared kinds ("inference", "indexing", "evaluation") run alongside each
other.

Every request, grant and release is recorded with a monotonic timestamp,
so a measurement can state exactly what else was active or queued while it
ran. Nothing here is persisted: leases are process-local and end with the
controller; a restart begins with no active leases.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id

LeaseKind = Literal["benchmark", "inference", "indexing", "evaluation"]
EXCLUSIVE: frozenset[str] = frozenset({"benchmark"})


@dataclass
class Lease:
    lease_id: str
    kind: str
    holder: str
    requested_ns: int
    granted_ns: int | None = None
    released_ns: int | None = None


@dataclass
class _Waiting:
    lease: Lease
    exclusive: bool
    order: int = field(default=0)


class ResourceScheduler:
    def __init__(self, clock_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self._clock = clock_ns
        self._condition = threading.Condition()
        self._active: dict[str, Lease] = {}
        self._waiting: list[_Waiting] = []
        self._log: list[Lease] = []
        self._order = 0

    def _may_grant(self, entry: _Waiting) -> bool:
        if entry.exclusive:
            # First exclusive waiter in line, and nothing at all running.
            first_exclusive = next(w for w in self._waiting if w.exclusive)
            return not self._active and first_exclusive is entry
        # Shared work waits behind any running or waiting exclusive lease.
        return not any(lease.kind in EXCLUSIVE for lease in self._active.values()) and \
            not any(w.exclusive for w in self._waiting)

    def acquire(self, kind: LeaseKind, holder: str, *, timeout_s: float | None = None) -> Lease | None:
        """Block until granted; None if the timeout passes first (the request is withdrawn)."""
        if kind not in ("benchmark", "inference", "indexing", "evaluation"):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"unknown lease kind {kind!r}")
        lease = Lease(lease_id=new_id("job"), kind=kind, holder=holder, requested_ns=self._clock())
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        with self._condition:
            self._order += 1
            entry = _Waiting(lease=lease, exclusive=kind in EXCLUSIVE, order=self._order)
            self._waiting.append(entry)
            self._log.append(lease)
            while not self._may_grant(entry):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._waiting.remove(entry)
                    self._condition.notify_all()
                    return None
                self._condition.wait(remaining)
            self._waiting.remove(entry)
            lease.granted_ns = self._clock()
            self._active[lease.lease_id] = lease
            return lease

    def release(self, lease: Lease) -> None:
        with self._condition:
            if self._active.pop(lease.lease_id, None) is None:
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"lease {lease.lease_id} is not active")
            lease.released_ns = self._clock()
            self._condition.notify_all()

    @contextmanager
    def lease(self, kind: LeaseKind, holder: str, *, timeout_s: float | None = None) -> Iterator[Lease]:
        granted = self.acquire(kind, holder, timeout_s=timeout_s)
        if granted is None:
            raise ForgeError(ErrorCode.RESOURCE_UNAVAILABLE, f"no {kind} lease within {timeout_s} s")
        try:
            yield granted
        finally:
            self.release(granted)

    # ----------------------------------------------------------- reporting

    def active(self) -> list[Lease]:
        with self._condition:
            return list(self._active.values())

    def overlapping(self, lease: Lease) -> list[Lease]:
        """Other leases whose granted interval overlaps this lease's granted interval."""
        start = lease.granted_ns or 0
        end = lease.released_ns if lease.released_ns is not None else self._clock()
        with self._condition:
            return [other for other in self._log if other is not lease and other.granted_ns is not None
                    and other.granted_ns < end and (other.released_ns is None or other.released_ns > start)]

    def queued_during(self, lease: Lease) -> list[Lease]:
        """Requests made while this lease was held, which waited for it."""
        start = lease.granted_ns or 0
        end = lease.released_ns if lease.released_ns is not None else self._clock()
        with self._condition:
            return [other for other in self._log if other is not lease and start <= other.requested_ns < end]

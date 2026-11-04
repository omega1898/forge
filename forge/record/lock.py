"""Single-writer lock for a project database.

The controller is the only writer of a project's record. A second controller
process opening the same project must be refused, not allowed to interleave
writes. SQLite's own locking prevents corruption, but not two controllers
each believing they own the project's queue, leases and dispatches.

The lock is an operating-system advisory lock on a sibling ".lock" file. The
OS releases it automatically if the process dies, so a crash never leaves a
stale lock behind.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from forge.contracts.errors import ErrorCode, ForgeError


class ProjectLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_nonblocking(fd)
        except OSError:
            os.close(fd)
            raise ForgeError(
                ErrorCode.RESOURCE_UNAVAILABLE,
                "another FORGE controller is already writing this project",
                details={"lock_file": self.path.name},
            ) from None
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None


if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI
    import msvcrt

    def _lock_nonblocking(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_nonblocking(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

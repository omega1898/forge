"""Control groups: hard memory and process-count limits, and "kill them all".

Linux control groups (cgroups) let the kernel account for a set of
processes and their descendants. FORGE uses them for three things:

- memory limit: the kernel kills the job if it uses more (memory.max /
  memory.limit_in_bytes),
- process limit: fork fails beyond a count (pids.max), which stops a fork
  bomb,
- tracking and termination: every descendant is in the group, so FORGE can
  list survivors and kill all of them.

Two kernel layouts exist. Pure cgroup v2 (most current distributions) has
one tree with all controllers. "Hybrid" systems keep memory and pids in
v1 trees and expose an empty v2 tree; this development container is hybrid.
Both are supported; the detected layout is reported, never assumed.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path("/sys/fs/cgroup")
PARENT = "forge"


@dataclass(frozen=True)
class CgroupSupport:
    layout: str                      # "v2", "hybrid", or "none"
    memory: bool
    pids: bool
    kill: bool                       # cgroup.kill available (v2, Linux 5.14+)
    reason: str = ""


def detect() -> CgroupSupport:
    if os.name != "posix" or not ROOT.exists():
        return CgroupSupport("none", False, False, False, "no cgroup filesystem")
    if os.geteuid() != 0:
        return CgroupSupport("none", False, False, False, "creating cgroups needs root or delegation")
    if (ROOT / "cgroup.controllers").exists():
        controllers = (ROOT / "cgroup.controllers").read_text().split()
        return CgroupSupport(
            "v2", "memory" in controllers, "pids" in controllers, (ROOT / "cgroup.kill").exists()
            or _v2_has_kill(ROOT),
        )
    memory = (ROOT / "memory" / "memory.limit_in_bytes").exists()
    pids = (ROOT / "pids" / "cgroup.procs").exists()
    unified = ROOT / "unified"
    kill = unified.exists() and _v2_has_kill(unified)
    return CgroupSupport("hybrid", memory, pids, kill)


def _v2_has_kill(root: Path) -> bool:
    probe = root / PARENT / f"probe-{os.getpid()}"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        return (probe / "cgroup.kill").exists()
    except OSError:
        return False
    finally:
        try:
            probe.rmdir()
        except OSError:
            pass


@dataclass
class JobCgroup:
    support: CgroupSupport
    name: str
    dirs: list[Path] = field(default_factory=list)
    memory_dir: Path | None = None
    pids_dir: Path | None = None
    kill_dir: Path | None = None

    @classmethod
    def create(cls, support: CgroupSupport, name: str, *, memory_bytes: int, max_processes: int) -> JobCgroup:
        group = cls(support=support, name=name)
        if support.layout == "v2":
            parent = ROOT / PARENT
            parent.mkdir(exist_ok=True)
            enable = " ".join(f"+{c}" for c, ok in (("memory", support.memory), ("pids", support.pids)) if ok)
            if enable:
                for level in (ROOT, parent):
                    try:
                        (level / "cgroup.subtree_control").write_text(enable)
                    except OSError:
                        pass
            job = parent / name
            job.mkdir()
            group.dirs.append(job)
            group.kill_dir = job
            if support.memory:
                (job / "memory.max").write_text(str(memory_bytes))
                if (job / "memory.swap.max").exists():
                    (job / "memory.swap.max").write_text("0")
                group.memory_dir = job
            if support.pids:
                (job / "pids.max").write_text(str(max_processes))
                group.pids_dir = job
        elif support.layout == "hybrid":
            if support.memory:
                job = ROOT / "memory" / PARENT / name
                job.mkdir(parents=True)
                (job / "memory.limit_in_bytes").write_text(str(memory_bytes))
                swap = job / "memory.memsw.limit_in_bytes"
                if swap.exists():
                    swap.write_text(str(memory_bytes))
                group.dirs.append(job)
                group.memory_dir = job
            if support.pids:
                job = ROOT / "pids" / PARENT / name
                job.mkdir(parents=True)
                (job / "pids.max").write_text(str(max_processes))
                group.dirs.append(job)
                group.pids_dir = job
            if support.kill:
                job = ROOT / "unified" / PARENT / name
                job.mkdir(parents=True)
                group.dirs.append(job)
                group.kill_dir = job
        return group

    def join(self, pid: int) -> None:
        """Move a process (and so all its future children) into every group."""
        for directory in self.dirs:
            (directory / "cgroup.procs").write_text(str(pid))

    def pids(self) -> set[int]:
        found: set[int] = set()
        for directory in self.dirs:
            try:
                text = (directory / "cgroup.procs").read_text()
            except OSError:
                continue
            found.update(int(line) for line in text.split())
        return found

    def kill_all(self, *, timeout: float = 5.0) -> int:
        """Kill every process in the group; return how many remain afterwards."""
        deadline = time.monotonic() + timeout
        while True:
            if self.kill_dir is not None and (self.kill_dir / "cgroup.kill").exists():
                try:
                    (self.kill_dir / "cgroup.kill").write_text("1")
                except OSError:
                    pass
            remaining = self.pids()
            for pid in remaining:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not remaining or time.monotonic() > deadline:
                return len(remaining)
            time.sleep(0.02)

    def oom_killed(self) -> bool:
        if self.memory_dir is None:
            return False
        events = self.memory_dir / ("memory.events" if self.support.layout == "v2" else "memory.oom_control")
        try:
            for line in events.read_text().splitlines():
                key, _, value = line.partition(" ")
                if key == "oom_kill" and int(value) > 0:
                    return True
        except OSError:
            return False
        if self.support.layout == "hybrid":
            try:
                return int((self.memory_dir / "memory.failcnt").read_text()) > 0
            except OSError:
                return False
        return False

    def remove(self) -> None:
        for directory in self.dirs:
            for _ in range(50):
                try:
                    directory.rmdir()
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.02)

"""Linux isolation backend built from kernel namespaces and cgroups.

Why this exists: the blueprint's first Linux backend is rootless Podman,
with bubblewrap as a later option. Neither was installable in the
environment where FORGE was first built, so this backend composes the same
kernel features through standard util-linux tools (`unshare`, `mount`,
`pivot_root`, `setpriv`) plus cgroups. It is admitted only through the
admission checks in tests/test_sandbox.py and advertises exactly what those
checks prove.

Honest limits, also reported in capabilities():
- shared kernel and no seccomp syscall filter: NOT suitable for hostile
  binaries (use a disposable VM profile for those);
- currently requires running FORGE as root (rootless operation through user
  namespaces is not implemented yet);
- memory and process-count limits need cgroup support; without it the
  backend falls back to weaker rlimits and does not claim those capabilities.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from forge.contracts.errors import ErrorCode, ForgeError
from forge.sandbox import cgroups
from forge.sandbox.base import BackendCapabilities, ExecutionObservation, SandboxSpec

LAUNCHER = Path(__file__).with_name("_launcher.py")
SYSTEM_DIRS = ["/usr", "/lib", "/lib64", "/lib32", "/libx32", "/bin", "/sbin"]
SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/scratch",
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
}
ENV_NAME_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
TOOLS = ("unshare", "mount", "umount", "pivot_root", "setpriv")


class _Pipe(threading.Thread):
    """Read a stream to the end, keeping at most `limit` bytes."""

    def __init__(self, stream, limit: int) -> None:  # noqa: ANN001
        super().__init__(daemon=True)
        self.stream, self.limit = stream, limit
        self.kept = bytearray()
        self.total = 0

    def run(self) -> None:
        while chunk := self.stream.read(65536):
            self.total += len(chunk)
            room = self.limit - len(self.kept)
            if room > 0:
                self.kept += chunk[:room]
        self.stream.close()


class LinuxNamespaceBackend:
    backend_id = "linux_namespaces"

    def __init__(self) -> None:
        self._tools = {name: shutil.which(name, path="/usr/bin:/usr/sbin:/bin:/sbin") for name in TOOLS}
        self._cgroups = cgroups.detect() if sys.platform == "linux" else None
        self._running: dict[str, tuple[subprocess.Popen, cgroups.JobCgroup | None]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ----------------------------------------------------------- capability

    def _unavailable_reason(self) -> str | None:
        if sys.platform != "linux":
            return "this backend runs only on Linux"
        if os.geteuid() != 0:
            return "requires root; rootless operation through user namespaces is not implemented yet"
        missing = [name for name, path in self._tools.items() if path is None]
        if missing:
            return "missing system tools: " + ", ".join(missing)
        return None

    def capabilities(self) -> BackendCapabilities:
        reason = self._unavailable_reason()
        caps: list[str] = []
        notes = [
            "shared host kernel and no seccomp filter: not for hostile binaries",
        ]
        if reason is None:
            caps = [
                "network_none", "filesystem_boundary", "readonly_input", "unprivileged_workload",
                "process_tree_kill", "cpu_time_limit", "scratch_limit", "output_limit",
            ]
            support = self._cgroups
            if support and support.memory:
                caps.append("memory_limit")
            else:
                notes.append("no cgroup memory controller: address-space rlimit only")
            if support and support.pids:
                caps.append("process_limit")
            else:
                notes.append("no cgroup pids controller: per-user rlimit only")
            if support:
                notes.append(f"cgroup layout: {support.layout}")
        return BackendCapabilities(
            backend_id=self.backend_id,
            available=reason is None,
            unavailable_reason=reason,
            os="linux",
            arch=platform.machine() or "unknown",
            capabilities=sorted(caps),
            suitable_for_hostile_code=False,
            notes=notes,
        )

    # ------------------------------------------------------------------ run

    def run(self, spec: SandboxSpec, work_dir: Path) -> ExecutionObservation:
        reason = self._unavailable_reason()
        if reason:
            raise ForgeError(ErrorCode.MISSING_CAPABILITY, f"{self.backend_id}: {reason}")
        _check_env(spec.env)
        input_dir = Path(spec.input_dir)
        if not input_dir.is_dir() or input_dir.is_symlink():
            raise ForgeError(ErrorCode.INVALID_REQUEST, "sandbox input must be a real directory")
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        new_root = work_dir / "root"
        output_dir = work_dir / "output"
        new_root.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        os.chmod(input_dir, 0o755)
        if spec.case_dir is not None:
            case_dir = Path(spec.case_dir)
            if not case_dir.is_dir() or case_dir.is_symlink():
                raise ForgeError(ErrorCode.INVALID_REQUEST, "sandbox case input must be a real directory")
            os.chmod(case_dir, 0o755)
        limits = spec.limits
        # Output is a size- and inode-limited tmpfs owned by the workload user,
        # so filling it fails inside the job instead of filling the host disk.
        subprocess.run(
            [self._tools["mount"], "-t", "tmpfs", "-o",
             f"size={max(limits.output_bytes, 4096)},nr_inodes={limits.output_files + 2},"
             "mode=0755,uid=65534,gid=65534,nosuid,nodev", "forge-output", str(output_dir)],
            check=True,
        )
        status_read, status_write = os.pipe()
        config = {
            "new_root": str(new_root),
            "input_dir": str(input_dir),
            "case_dir": spec.case_dir,
            "output_dir": str(output_dir),
            "system_dirs": SYSTEM_DIRS,
            "toolchain_paths": spec.toolchain_paths,
            "scratch_bytes": limits.scratch_bytes,
            "tmp_bytes": min(limits.scratch_bytes, 64 * 1024 * 1024),
            "cwd": spec.cwd,
            "argv": spec.argv,
            "env": {**SAFE_ENV, **spec.env},
            "limits": limits.model_dump(),
            "status_fd": status_write,
            "cgroup_memory": bool(self._cgroups and self._cgroups.memory),
            **{name: path for name, path in self._tools.items()},
        }
        config_path = work_dir / "launcher.json"
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle)

        group = None
        if self._cgroups and (self._cgroups.memory or self._cgroups.pids or self._cgroups.kill):
            group = cgroups.JobCgroup.create(
                self._cgroups, f"{spec.job_key}-{os.getpid()}-{time.monotonic_ns()}",
                memory_bytes=limits.memory_bytes, max_processes=limits.max_processes,
            )
        argv = [
            self._tools["unshare"], "--mount", "--pid", "--net", "--ipc", "--uts", "--fork",
            "--kill-child", "--", os.path.realpath(sys.executable), "-I", "-S", str(LAUNCHER),
            str(config_path),
        ]
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(status_write,),
                env={"PATH": "/usr/bin:/bin"},
                start_new_session=True,
                preexec_fn=(lambda: group.join(os.getpid())) if group else None,
            )
        except BaseException:
            os.close(status_write)
            os.close(status_read)
            if group:
                group.remove()
            raise
        os.close(status_write)
        with self._lock:
            self._running[spec.job_key] = (process, group)
            cancelled_early = spec.job_key in self._cancelled
        if cancelled_early:
            # A cancel arrived before the process was registered: honor it now.
            self._kill(process, group)
        out = _Pipe(process.stdout, limits.stdout_bytes)
        err = _Pipe(process.stderr, limits.stderr_bytes)
        out.start()
        err.start()
        status_lines: list[str] = []
        status_reader = threading.Thread(
            target=lambda: status_lines.extend(_read_all(status_read).splitlines()), daemon=True
        )
        status_reader.start()
        threading.Thread(target=_feed_stdin, args=(process, spec.stdin), daemon=True).start()

        terminated_by = "exited"
        try:
            process.wait(timeout=limits.wall_ms / 1000)
        except subprocess.TimeoutExpired:
            terminated_by = "timeout"
        if terminated_by == "timeout" or spec.job_key in self._cancelled:
            terminated_by = "cancelled" if spec.job_key in self._cancelled else "timeout"
            self._kill(process, group)
        process.wait()
        # The namespace's init has exited, so the kernel has killed the rest;
        # the cgroup confirms it and catches anything that escaped the namespace.
        remaining = group.kill_all() if group else 0
        out.join(5)
        err.join(5)
        status_reader.join(5)
        wall_ms = int((time.monotonic() - started) * 1000)
        with self._lock:
            self._running.pop(spec.job_key, None)
            self._cancelled.discard(spec.job_key)

        exit_code: int | None = None
        signal_number: int | None = None
        for line in status_lines:
            kind, _, value = line.partition(" ")
            if kind == "exit":
                exit_code = int(value)
            elif kind == "signal":
                signal_number = int(value)
            elif kind in ("setup_failed", "exec_failed"):
                terminated_by = "launch_failed"
                err.kept += f"\n[forge launcher] {line}\n".encode()
        if terminated_by == "exited" and exit_code is None and signal_number is None:
            terminated_by = "launch_failed"
        if group and group.oom_killed() and terminated_by == "exited" and signal_number == signal.SIGKILL:
            terminated_by = "memory_limit"
        if group:
            group.remove()
        return ExecutionObservation(
            backend_id=self.backend_id,
            exit_code=exit_code,
            signal=signal_number,
            terminated_by=terminated_by,
            wall_ms=wall_ms,
            stdout=bytes(out.kept),
            stderr=bytes(err.kept),
            stdout_total_bytes=out.total,
            stderr_total_bytes=err.total,
            stdout_truncated=out.total > len(out.kept),
            stderr_truncated=err.total > limits.stderr_bytes,
            processes_remaining=remaining,
            output_dir=str(output_dir),
        )

    def _kill(self, process: subprocess.Popen, group: cgroups.JobCgroup | None) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if group:
            group.kill_all()

    def cancel(self, job_key: str) -> bool:
        """Cancel a running job, or one about to start (it is killed on launch)."""
        with self._lock:
            self._cancelled.add(job_key)
            entry = self._running.get(job_key)
        if entry is not None:
            self._kill(*entry)
        return True

    def destroy(self, work_dir: Path) -> None:
        """Unmount the output area and delete the job directory."""
        output_dir = Path(work_dir) / "output"
        if output_dir.exists():
            subprocess.run([self._tools["umount"], "-l", str(output_dir)], check=False)
        shutil.rmtree(work_dir, ignore_errors=True)


def _read_all(fd: int) -> str:
    chunks = []
    while chunk := os.read(fd, 4096):
        chunks.append(chunk)
        if sum(map(len, chunks)) > 64 * 1024:
            break
    os.close(fd)
    return b"".join(chunks).decode("utf-8", "replace")


def _feed_stdin(process: subprocess.Popen, data: bytes) -> None:
    try:
        if data:
            process.stdin.write(data)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass


def _check_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        if not key or set(key) - ENV_NAME_OK or key.startswith(("LD_", "PYTHONSTARTUP", "PYTHONPATH")):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"environment variable {key!r} is not allowed")
        if "\x00" in value or len(value) > 4096:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"environment value for {key} is not allowed")

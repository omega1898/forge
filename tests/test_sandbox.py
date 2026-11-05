"""Ticket F04: Linux namespace backend admission checks (EX-T03, EX-T04, EX-T05).

Acceptance: an admitted fixture cannot access seeded outside files or the
network; a timeout stops all descendants; each resource limit produces a
bounded, typed failure.

These tests run real workloads under real kernel isolation, so they need
Linux and root. Elsewhere they are skipped, and the backend reports itself
unavailable rather than pretending.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from forge.authority.authorize import DEFAULT_LIMITS
from forge.contracts.errors import ErrorCode, ForgeError
from forge.assessment.service import interpreter
from forge.sandbox.base import SandboxSpec, require_capabilities
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.sandbox.unavailable import MacosVmBackend, PodmanBackend, WindowsVmBackend

BACKEND = LinuxNamespaceBackend()
pytestmark = pytest.mark.skipif(
    not BACKEND.capabilities().available, reason=str(BACKEND.capabilities().unavailable_reason)
)
PYTHON, PYTHON_DIRS = interpreter()  # outside /usr (e.g. a CI tool cache) its tree must be mounted
LIMITS = DEFAULT_LIMITS.model_copy(update={"wall_ms": 20_000})


@pytest.fixture
def job(tmp_path: Path):  # noqa: ANN201
    """Run Python code in the sandbox; clean up the job directories afterwards."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "data.txt").write_text("snapshot bytes\n")
    work_dirs: list[Path] = []

    def run(code: str, *, limits=LIMITS, key: str = "t", stdin: bytes = b""):  # noqa: ANN001, ANN202
        work = tmp_path / f"job-{len(work_dirs)}"
        work_dirs.append(work)
        spec = SandboxSpec(
            job_key=key, input_dir=str(input_dir), argv=[PYTHON, "-I", "-c", code], limits=limits, stdin=stdin,
            toolchain_paths=PYTHON_DIRS,
        )
        return BACKEND.run(spec, work)

    run.input_dir = input_dir  # type: ignore[attr-defined]
    yield run
    for work in work_dirs:
        BACKEND.destroy(work)


def test_capabilities_are_reported_honestly() -> None:
    caps = BACKEND.capabilities()
    assert caps.suitable_for_hostile_code is False
    assert {"network_none", "filesystem_boundary", "unprivileged_workload", "process_tree_kill"} <= set(
        caps.capabilities
    )


def test_workload_is_unprivileged(job) -> None:  # noqa: ANN001
    code = (
        "import os\n"
        "status = open('/proc/self/status').read()\n"
        "field = lambda name: status.split(name + ':')[1].split()[0]\n"
        "print(os.getuid(), os.getgid(), field('CapEff'), field('CapBnd'), field('NoNewPrivs'))\n"
    )
    obs = job(code)
    assert obs.exit_code == 0, obs.stderr
    assert obs.stdout.decode().split() == ["65534", "65534", "0000000000000000", "0000000000000000", "1"]


def test_standard_devices_work_but_no_other_device_can_be_created(job) -> None:  # noqa: ANN001
    code = (
        "import os, subprocess\n"
        "with open('/dev/null', 'r+b') as h: h.write(b'gone'); assert h.read() == b''\n"
        "assert len(open('/dev/urandom', 'rb').read(16)) == 16\n"
        "assert open('/dev/zero', 'rb').read(4) == bytes(4)\n"
        "subprocess.run(['true'], stdout=subprocess.DEVNULL, check=True)\n"
        "try:\n"
        "    os.mknod('/scratch/mem', 0o600 | 0o020000, os.makedev(1, 1))\n"
        "    print('mknod allowed')\n"
        "except OSError:\n"
        "    print('mknod denied')\n"
        "print(sorted(os.listdir('/dev')))\n"
    )
    obs = job(code)
    assert obs.exit_code == 0, obs.stderr
    lines = obs.stdout.decode().splitlines()
    assert lines[0] == "mknod denied"
    assert lines[1] == str(sorted(["full", "null", "random", "urandom", "zero"]))


def test_seeded_host_files_are_invisible_and_writes_are_denied_ex_t03(job, tmp_path: Path) -> None:  # noqa: ANN001
    canary = tmp_path / "host-canary.txt"
    canary.write_text("HOST SECRET")
    home_canary = Path("/root/.forge-test-canary")
    home_canary.write_text("ROOT SECRET")
    # Simulate a permission mistake: make the snapshot world-writable, so the
    # read-only mount (not ordinary file permissions) is what must stop writes.
    os.chmod(job.input_dir, 0o777)
    os.chmod(job.input_dir / "data.txt", 0o666)
    try:
        code = f"""
import os
results = {{}}
for path in [{str(canary)!r}, {str(home_canary)!r}, '/etc/shadow', '/root', '/home', '/var']:
    try:
        open(path).read() if not os.path.isdir(path) else os.listdir(path)
        results[path] = 'READ'
    except OSError as error:
        results[path] = type(error).__name__
for path in ['/input/new.txt', '/input/data.txt', '/usr/evil', '/evil', '/etc/evil']:
    try:
        with open(path, 'a') as handle: handle.write('x')
        results[path] = 'WROTE'
    except OSError as error:
        results[path] = type(error).__name__
for path in ['/scratch/ok.txt', '/output/ok.txt', '/tmp/ok.txt']:
    with open(path, 'w') as handle: handle.write('fine')
    results[path] = 'WROTE'
for line in open('/proc/self/mountinfo'):
    fields = line.split()
    if fields[4] == '/input':
        results['input_mount_options'] = fields[5]
print(results)
"""
        obs = job(code)
        assert obs.exit_code == 0, obs.stderr
        results = eval(obs.stdout.decode())  # noqa: S307 - our own probe output
        assert "ro" in results.pop("input_mount_options").split(",")
        assert "READ" not in {results[key] for key in results if not key.startswith(("/scratch", "/output", "/tmp"))}
        for denied in ("/input/new.txt", "/input/data.txt", "/usr/evil", "/evil", "/etc/evil"):
            assert results[denied] != "WROTE", denied
        assert canary.read_text() == "HOST SECRET"
        assert (job.input_dir / "data.txt").read_text() == "snapshot bytes\n"
        assert not (job.input_dir / "new.txt").exists()
    finally:
        home_canary.unlink()


def test_mounting_is_denied(job) -> None:  # noqa: ANN001
    code = "import subprocess; r = subprocess.run(['/usr/bin/mount', '-t', 'tmpfs', 'x', '/scratch']); print(r.returncode)"
    obs = job(code)
    assert obs.stdout.decode().strip() != "0"


def test_every_network_family_is_blocked_ex_t04(job) -> None:  # noqa: ANN001
    host_ip = _host_ip()
    abstract = f"\0forge-test-{os.getpid()}"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(abstract)
    server.listen(1)
    try:
        code = f"""
import socket
attempts = {{
    'ipv4': (socket.AF_INET, socket.SOCK_STREAM, ('1.1.1.1', 443)),
    'ipv6': (socket.AF_INET6, socket.SOCK_STREAM, ('2606:4700:4700::1111', 443)),
    'dns_udp': (socket.AF_INET, socket.SOCK_DGRAM, ('8.8.8.8', 53)),
    'lan_host': (socket.AF_INET, socket.SOCK_STREAM, ({host_ip!r}, 22)),
    'loopback_host_service': (socket.AF_INET, socket.SOCK_STREAM, ('127.0.0.1', 22)),
    'abstract_unix': (socket.AF_UNIX, socket.SOCK_STREAM, {abstract!r}),
}}
results = {{}}
for name, (family, kind, address) in attempts.items():
    try:
        # Creating the socket can itself fail (an address family with no
        # interface in this namespace); that is a block too, not a crash.
        sock = socket.socket(family, kind)
        sock.settimeout(2)
        if kind == socket.SOCK_DGRAM:
            sock.sendto(b'x', address)
        else:
            sock.connect(address)
        results[name] = 'CONNECTED'
    except OSError as error:
        results[name] = type(error).__name__
try:
    socket.getaddrinfo('example.com', 443)
    results['dns_lookup'] = 'RESOLVED'
except OSError as error:
    results['dns_lookup'] = type(error).__name__
results['interfaces'] = [name for _, name in socket.if_nameindex()]
print(results)
"""
        obs = job(code)
        assert obs.exit_code == 0, obs.stderr
        results = eval(obs.stdout.decode())  # noqa: S307
        interfaces = results.pop("interfaces")
        assert interfaces == ["lo"]
        for family, outcome in results.items():
            assert outcome not in ("CONNECTED", "RESOLVED"), family
    finally:
        server.close()


def test_timeout_stops_all_descendants_ex_t05(job) -> None:  # noqa: ANN001
    marker = f"forge-orphan-{os.getpid()}-{time.monotonic_ns()}"
    code = f"""
import subprocess, time
for _ in range(3):
    subprocess.Popen(['/bin/sh', '-c', 'exec -a {marker} sleep 300'], start_new_session=True)
time.sleep(300)
"""
    limits = LIMITS.model_copy(update={"wall_ms": 1500})
    obs = job(code, limits=limits)
    assert obs.terminated_by == "timeout"
    assert obs.processes_remaining == 0
    time.sleep(0.2)
    survivors = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    assert survivors == []


def test_fork_bomb_is_bounded_ex_t05(job) -> None:  # noqa: ANN001
    code = """
import os, time
children = 0
try:
    for _ in range(500):
        if os.fork() == 0:
            time.sleep(30)
            os._exit(0)
        children += 1
except OSError as error:
    print('stopped', children, type(error).__name__)
"""
    limits = LIMITS.model_copy(update={"max_processes": 20, "wall_ms": 5000})
    obs = job(code, limits=limits)
    assert b"stopped" in obs.stdout, obs.stderr
    assert int(obs.stdout.split()[1]) < 20
    assert obs.processes_remaining == 0


def test_memory_limit_kills_the_job_ex_t05(job) -> None:  # noqa: ANN001
    if "memory_limit" not in BACKEND.capabilities().capabilities:
        pytest.skip("no cgroup memory controller on this host")
    limits = LIMITS.model_copy(update={"memory_bytes": 64 * 1024 * 1024})
    obs = job("blocks = [bytearray(16 * 1024 * 1024) for _ in range(32)]\nprint('survived')", limits=limits)
    assert b"survived" not in obs.stdout
    assert obs.terminated_by == "memory_limit"


def test_output_area_cannot_be_overfilled_ex_t05(job) -> None:  # noqa: ANN001
    limits = LIMITS.model_copy(update={"output_bytes": 1024 * 1024})
    code = """
try:
    with open('/output/big.bin', 'wb') as handle:
        for _ in range(64):
            handle.write(b'x' * 1024 * 1024)
    print('wrote everything')
except OSError as error:
    print('stopped', error.errno)
"""
    obs = job(code, limits=limits)
    assert b"stopped" in obs.stdout, (obs.stdout, obs.stderr)


def test_endless_stdout_is_bounded_ex_t05(job) -> None:  # noqa: ANN001
    limits = LIMITS.model_copy(update={"stdout_bytes": 10_000, "wall_ms": 3000})
    obs = job("import sys\nwhile True: sys.stdout.write('y' * 4096)", limits=limits)
    assert len(obs.stdout) == 10_000
    assert obs.stdout_truncated
    assert obs.stdout_total_bytes > 10_000
    assert obs.terminated_by == "timeout"


def test_exit_codes_signals_and_stdin_are_observed(job) -> None:  # noqa: ANN001
    assert job("import sys; sys.exit(7)").exit_code == 7
    obs = job("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
    assert obs.signal == 15 and obs.exit_code is None
    echoed = job("import sys; print(sys.stdin.read().upper())", stdin=b"packet")
    assert echoed.stdout.strip() == b"PACKET"


def test_cancel_stops_a_running_job(job) -> None:  # noqa: ANN001
    result = {}
    thread = threading.Thread(target=lambda: result.update(obs=job("import time; time.sleep(60)", key="cancel_me")))
    thread.start()
    for _ in range(100):
        if BACKEND.cancel("cancel_me"):
            break
        time.sleep(0.05)
    thread.join(10)
    assert result["obs"].terminated_by == "cancelled"
    assert result["obs"].processes_remaining == 0


def test_dangerous_environment_is_rejected(job, tmp_path: Path) -> None:  # noqa: ANN001
    spec = SandboxSpec(
        job_key="env", input_dir=str(job.input_dir), argv=[PYTHON, "-c", "1"], limits=LIMITS,
        toolchain_paths=PYTHON_DIRS,
        env={"LD_PRELOAD": "/scratch/evil.so"},
    )
    with pytest.raises(ForgeError, match="LD_PRELOAD"):
        BACKEND.run(spec, tmp_path / "env-job")


def test_dispatch_fails_closed_without_required_capabilities() -> None:
    for unavailable in (PodmanBackend(), WindowsVmBackend(), MacosVmBackend()):
        with pytest.raises(ForgeError) as caught:
            require_capabilities(unavailable, ["network_none"])
        assert caught.value.code == ErrorCode.MISSING_CAPABILITY
    with pytest.raises(ForgeError, match="gpu"):
        require_capabilities(BACKEND, ["network_none", "gpu"])
    require_capabilities(BACKEND, ["network_none", "process_tree_kill"])


def _host_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "10.0.0.1"
    finally:
        probe.close()

"""Trusted in-namespace launcher for the Linux namespace backend.

This file runs as process 1 of a fresh PID namespace, inside fresh mount,
network, IPC and UTS namespaces, created by `unshare`. It:

1. builds a new root filesystem on a small tmpfs containing only read-only
   system directories, the read-only snapshot (/input), size-limited scratch
   and output areas, a minimal /dev and a private /proc;
2. switches into it with pivot_root and detaches the host filesystem;
3. starts the workload as user 65534 ("nobody") with no capabilities and
   no_new_privs, under resource limits;
4. reaps processes as namespace init and reports the workload's exit status
   on a private status pipe that the workload cannot write to.

When this process exits, the kernel kills every remaining process in the
namespace, so no descendant can outlive the job.

It imports only the standard library and is run with `python -I -S`, so
nothing from the project or site-packages is loaded into the trusted side.
"""

import json
import os
import resource
import sys

NOBODY = 65534


def status(channel: int, message: str) -> None:
    os.write(channel, (message + "\n").encode("utf-8", "replace"))


def run(argv: list) -> None:
    """Run one setup command; on failure include its (bounded) error output."""
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_end)
            os.dup2(write_end, 1)
            os.dup2(write_end, 2)
            os.execv(argv[0], argv)
        finally:
            os._exit(127)
    os.close(write_end)
    output = b""
    while chunk := os.read(read_end, 4096):
        output = (output + chunk)[:2000]
    os.close(read_end)
    _, code = os.waitpid(pid, 0)
    if code != 0:
        raise RuntimeError(
            "setup step failed: " + " ".join(argv) + ": " + output.decode("utf-8", "replace").strip()
        )


def make_dir(path: str, mode: int = 0o755) -> None:
    os.makedirs(path, mode=mode, exist_ok=True)


def bind(cfg: dict, source: str, target: str, writable: bool = False, device: bool = False) -> None:
    if os.path.islink(source):
        os.symlink(os.readlink(source), target)
        return
    if os.path.isdir(source):
        make_dir(target)
    else:
        make_dir(os.path.dirname(target))
        open(target, "a").close()
    run([cfg["mount"], "--bind", source, target])
    # Device nodes (/dev/null and friends) need a mount without nodev to be usable.
    options = "remount,bind,nosuid" + ("" if device else ",nodev") + ("" if writable else ",ro")
    run([cfg["mount"], "-o", options, target])


def build_root(cfg: dict) -> None:
    root = cfg["new_root"]
    run([cfg["mount"], "--make-rprivate", "/"])
    run([cfg["mount"], "-t", "tmpfs", "-o", "size=16m,mode=0755,nosuid,nodev", "forge-root", root])
    for host_path in cfg["system_dirs"] + cfg["toolchain_paths"]:
        if os.path.lexists(host_path):
            bind(cfg, host_path, root + host_path)
    for name in ("input", "case", "scratch", "output", "tmp", "dev", "proc", "etc", "oldroot"):
        make_dir(os.path.join(root, name))
    bind(cfg, cfg["input_dir"], root + "/input")
    if cfg.get("case_dir"):
        bind(cfg, cfg["case_dir"], root + "/case")
    bind(cfg, cfg["output_dir"], root + "/output", writable=True)
    run([cfg["mount"], "-t", "tmpfs", "-o",
         "size=%d,mode=0700,uid=%d,gid=%d,nosuid,nodev" % (cfg["scratch_bytes"], NOBODY, NOBODY),
         "forge-scratch", root + "/scratch"])
    run([cfg["mount"], "-t", "tmpfs", "-o",
         "size=%d,mode=1777,nosuid,nodev" % cfg["tmp_bytes"], "forge-tmp", root + "/tmp"])
    run([cfg["mount"], "-t", "tmpfs", "-o", "size=64k,mode=0755,nosuid", "forge-dev", root + "/dev"])
    for device in ("null", "zero", "full", "random", "urandom"):
        bind(cfg, "/dev/" + device, root + "/dev/" + device, writable=True, device=True)
    run([cfg["mount"], "-t", "proc", "-o", "nosuid,nodev,noexec", "proc", root + "/proc"])
    with open(root + "/etc/passwd", "w") as handle:
        handle.write("nobody:x:%d:%d:nobody:/scratch:/usr/sbin/nologin\n" % (NOBODY, NOBODY))
    with open(root + "/etc/group", "w") as handle:
        handle.write("nogroup:x:%d:\n" % NOBODY)
    if os.path.exists("/etc/ld.so.cache"):
        bind(cfg, "/etc/ld.so.cache", root + "/etc/ld.so.cache")
    os.chdir(root)
    run([cfg["pivot_root"], ".", "oldroot"])
    os.chdir("/")
    run([cfg["umount"], "-l", "/oldroot"])
    os.rmdir("/oldroot")
    run([cfg["mount"], "-o", "remount,ro,bind", "/"])


def set_limits(cfg: dict) -> None:
    limits = cfg["limits"]
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"] + 1))
    biggest_file = max(limits["scratch_bytes"], limits["output_bytes"], 1)
    resource.setrlimit(resource.RLIMIT_FSIZE, (biggest_file, biggest_file))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits["open_files"], limits["open_files"]))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (limits["max_processes"], limits["max_processes"]))
    if not cfg["cgroup_memory"]:
        # Weaker fallback: limits address space, not resident memory.
        resource.setrlimit(resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"]))


def main() -> None:
    cfg_path = sys.argv[1]
    with open(cfg_path) as handle:
        cfg = json.load(handle)
    channel = cfg["status_fd"]
    os.set_inheritable(channel, False)
    try:
        build_root(cfg)
    except Exception as error:  # noqa: BLE001 - reported on the private channel
        status(channel, "setup_failed " + str(error)[:500])
        os._exit(125)

    child = os.fork()
    if child == 0:
        try:
            os.chdir(cfg["cwd"])
            set_limits(cfg)
            argv = [
                cfg["setpriv"], "--reuid=%d" % NOBODY, "--regid=%d" % NOBODY, "--clear-groups",
                "--no-new-privs", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
                "--", *cfg["argv"],
            ]
            os.execve(argv[0], argv, cfg["env"])
        except Exception as error:  # noqa: BLE001
            status(channel, "exec_failed " + str(error)[:500])
        os._exit(126)
    status(channel, "started")
    while True:
        try:
            pid, code = os.waitpid(-1, 0)
        except ChildProcessError:
            os._exit(126)
        if pid == child:
            if os.WIFSIGNALED(code):
                status(channel, "signal %d" % os.WTERMSIG(code))
            else:
                status(channel, "exit %d" % os.WEXITSTATUS(code))
            os._exit(0)


if __name__ == "__main__":
    main()

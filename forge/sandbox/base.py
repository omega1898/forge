"""The common contract every isolation backend implements.

Lifecycle: prepare -> start -> poll/wait -> (cancel) -> collect -> destroy.

Each backend advertises the boundaries it can *enforce*, measured by
admission checks, not assumed from its name. Dispatch fails closed: a job
that requires a capability the backend lacks is refused, never run with
weaker isolation. A backend may be fine for trusted local development and
still unsuitable for hostile binaries; `suitable_for_hostile_code` records
that distinction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from forge.authority.authorize import ResourceLimits
from forge.contracts.base import Slug, StrictModel
from forge.contracts.errors import ErrorCode, ForgeError

# Capability names used in host policy and job requirements.
CAPABILITIES = (
    "network_none",          # no route to any network, including loopback services on the host
    "filesystem_boundary",   # only the declared mounts are visible
    "readonly_input",        # the snapshot cannot be modified
    "unprivileged_workload", # workload runs without root or capabilities
    "process_tree_kill",     # every descendant is stopped at the end
    "memory_limit",
    "process_limit",
    "cpu_time_limit",
    "scratch_limit",
    "output_limit",
)


class BackendCapabilities(StrictModel):
    backend_id: Slug
    available: bool
    unavailable_reason: str | None = None
    os: Literal["linux", "windows", "macos"]
    arch: str
    capabilities: list[Slug]
    suitable_for_hostile_code: bool
    notes: list[str] = Field(default_factory=list)


class SandboxSpec(StrictModel):
    """One isolated execution. Paths here are host paths owned by the controller."""

    job_key: Slug
    input_dir: str            # materialized snapshot, mounted read-only at /input
    argv: list[str] = Field(min_length=1, max_length=256)
    cwd: Literal["/input", "/scratch"] = "/scratch"
    env: dict[str, str] = Field(default_factory=dict)
    limits: ResourceLimits
    # Extra read-only host directories needed by an admitted toolchain.
    toolchain_paths: list[str] = Field(default_factory=list, max_length=16)
    stdin: bytes = b""


class ExecutionObservation(StrictModel):
    """What the supervisor observed. Not an assessment of correctness."""

    backend_id: Slug
    exit_code: int | None
    signal: int | None
    terminated_by: Literal["exited", "timeout", "memory_limit", "cancelled", "launch_failed"]
    wall_ms: int
    stdout: bytes
    stderr: bytes
    stdout_total_bytes: int
    stderr_total_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    processes_remaining: int
    output_dir: str


class SandboxBackend(Protocol):
    def capabilities(self) -> BackendCapabilities: ...

    def run(self, spec: SandboxSpec, work_dir: Path) -> ExecutionObservation: ...

    def cancel(self, job_key: str) -> bool: ...


def require_capabilities(backend: SandboxBackend, required: list[str]) -> None:
    """Fail closed when the backend cannot enforce what the job needs."""
    advertised = backend.capabilities()
    if not advertised.available:
        raise ForgeError(
            ErrorCode.MISSING_CAPABILITY,
            f"isolation backend {advertised.backend_id} is unavailable: {advertised.unavailable_reason}",
        )
    missing = sorted(set(required) - set(advertised.capabilities))
    if missing:
        raise ForgeError(
            ErrorCode.MISSING_CAPABILITY,
            f"isolation backend {advertised.backend_id} cannot enforce: {', '.join(missing)}",
            details={"missing": missing},
        )

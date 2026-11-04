"""The job contract shared by the controller and every execution host.

A job is one concrete execution attempt. Its identity is (job_id,
attempt_number, lease_epoch) plus the digest of its immutable
specification. The digest covers exactly what was authorized and nothing
that changes afterwards (status, heartbeats, timestamps of lifecycle
events), so a lifecycle update can never alter what was approved.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from forge.authority.authorize import ResourceLimits
from forge.contracts.base import Slug, StrictModel
from forge.contracts.clock import Timestamp
from forge.contracts.digests import Digest, digest_json
from forge.contracts.ids import ControllerId, GrantId, IdOf, ProjectId, TaskId

EffectClass = Literal["read_only", "scratch_only", "workspace_patch", "external_effect"]
RetryClass = Literal["safe_to_repeat", "reconcile_before_retry", "never_automatic"]
ExecutionStatus = Literal["queued", "leased", "running", "succeeded", "failed", "cancelled", "lost"]
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled", "lost"})


class OutputContract(StrictModel):
    max_files: int = Field(default=1000, ge=0)
    max_bytes: int = Field(default=64 * 1024 * 1024, ge=0)


class JobSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: IdOf("job")
    attempt_number: int = Field(ge=1)
    lease_epoch: int = Field(ge=1)
    project_id: ProjectId
    task_id: TaskId
    controller_id: ControllerId
    host_id: Slug
    snapshot_digest: Digest
    tool_ref: str
    tool_digest: Digest | None
    runtime_digest: Digest | None
    # Typed tool inputs. Never a shell command string.
    arguments: dict[str, JsonValue]
    environment_profile_id: Slug = "default"
    grant_ids: list[GrantId]
    grant_digests: list[Digest]
    policy_digest: Digest
    resource_budget: ResourceLimits
    output_contract: OutputContract = Field(default_factory=OutputContract)
    effect_class: EffectClass
    retry_class: RetryClass
    request_nonce: str = Field(min_length=16, max_length=64)
    issued_at: Timestamp
    expires_at: Timestamp

    def digest(self) -> str:
        """Digest of the immutable job-spec projection (all fields above)."""
        return digest_json(self.model_dump(mode="json"))

    @property
    def identity(self) -> tuple[str, int, int]:
        return (self.job_id, self.attempt_number, self.lease_epoch)


class JobReceipt(StrictModel):
    job_id: str
    attempt_number: int
    lease_epoch: int
    job_digest: Digest
    host_id: str
    accepted: bool
    state: ExecutionStatus
    reason: str | None = None
    duplicate: bool = False


class HostJobEvent(StrictModel):
    """One event reported by a host about one attempt, in host sequence order."""

    job_id: str
    attempt_number: int
    lease_epoch: int
    sequence: int = Field(ge=1)
    kind: Literal["accepted", "started", "progress", "finished", "cancel_acknowledged", "lost"]
    state: ExecutionStatus
    payload: dict[str, JsonValue] = Field(default_factory=dict)

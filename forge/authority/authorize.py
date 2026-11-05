"""authorize_action: may this action run, and within which limits?

The effective permission is the intersection of four layers:

    trusted user grant  ∩  controller policy  ∩  host policy  ∩  requested scope

Every layer must allow the action. An allow at one layer never cancels a
denial at another. The result carries stable reason codes so the interface
can explain a denial, and so a model can explain it but never turn it into
an allow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from forge.authority.grants import Grant, GrantAction, TrustStore
from forge.contracts.base import Slug, StrictModel
from forge.contracts.clock import parse_timestamp
from forge.contracts.digests import Digest, digest_json
from forge.contracts.ids import GrantId, ProjectId

ReasonCode = Literal[
    "allowed",
    "grant_missing",
    "grant_expired",
    "grant_revoked",
    "grant_wrong_project",
    "action_not_granted",
    "target_out_of_scope",
    "host_not_enrolled",
    "action_not_permitted_by_host",
    "action_not_permitted_by_controller",
    "capability_unavailable",
    "limit_exceeded",
]


class ResourceLimits(StrictModel):
    """Upper bounds for one job. Durations in milliseconds, sizes in bytes."""

    wall_ms: int = Field(ge=1, le=24 * 3600 * 1000)
    cpu_seconds: int = Field(ge=1, le=24 * 3600)
    memory_bytes: int = Field(ge=16 * 1024 * 1024)
    max_processes: int = Field(ge=1, le=4096)
    scratch_bytes: int = Field(ge=1024 * 1024)
    output_bytes: int = Field(ge=0)
    output_files: int = Field(ge=0, le=100_000)
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    open_files: int = Field(default=256, ge=16, le=65_536)

    def intersect(self, other: ResourceLimits) -> ResourceLimits:
        mine, theirs = self.model_dump(), other.model_dump()
        return ResourceLimits(**{key: min(value, theirs[key]) for key, value in mine.items()})

    def exceeded_by(self, request: ResourceLimits) -> list[str]:
        mine, asked = self.model_dump(), request.model_dump()
        return sorted(key for key, value in asked.items() if value > mine[key])


DEFAULT_LIMITS = ResourceLimits(
    wall_ms=120_000,
    cpu_seconds=60,
    memory_bytes=512 * 1024 * 1024,
    max_processes=64,
    scratch_bytes=256 * 1024 * 1024,
    output_bytes=64 * 1024 * 1024,
    output_files=1000,
    stdout_bytes=1024 * 1024,
    stderr_bytes=1024 * 1024,
)


class HostPolicy(StrictModel):
    """The receiving host's own ceiling. A grant cannot raise it."""

    host_id: Slug
    enrolled_projects: list[ProjectId]
    allowed_actions: list[GrantAction]
    max_limits: ResourceLimits
    backend_capabilities: list[Slug]


class ControllerPolicy(StrictModel):
    allowed_actions: list[GrantAction]
    max_limits: ResourceLimits


class ActionRequest(StrictModel):
    action: GrantAction
    # Project-relative paths the action reads or changes.
    paths: list[str] = Field(default_factory=list, max_length=1000)
    limits: ResourceLimits
    required_capabilities: list[Slug] = Field(default_factory=list)


class AuthorizationDecision(StrictModel):
    allowed: bool
    reasons: list[ReasonCode]
    details: list[str]
    effective_limits: ResourceLimits | None
    grant_digests: list[Digest]
    policy_digest: Digest
    used_grant_ids: list[GrantId]


def grant_digest(grant: Grant) -> str:
    """Digest of the grant's immutable approval content (not its status)."""
    return digest_json(
        {
            "grant_id": grant.grant_id,
            "project_id": grant.project_id,
            "actions": grant.actions,
            "path_scope": grant.path_scope,
            "approved_by": grant.approved_by,
            "approved_at": grant.approved_at,
            "expires_at": grant.expires_at,
            "proposal_digest": grant.proposal_digest,
        }
    )


def _in_scope(path: str, scopes: list[str]) -> bool:
    return any(path == scope or path.startswith(scope + "/") for scope in scopes)


def authorize_action(
    *,
    project_id: str,
    request: ActionRequest,
    grant_ids: list[str],
    trust_store: TrustStore,
    host_policy: HostPolicy,
    controller_policy: ControllerPolicy,
    now: datetime,
) -> AuthorizationDecision:
    reasons: list[ReasonCode] = []
    details: list[str] = []
    policy_digest = digest_json(
        {"host": host_policy.model_dump(mode="json"), "controller": controller_policy.model_dump(mode="json")}
    )

    # Layer 1: trusted user grants. Each grant is judged on its own; the
    # action is allowed only if at least one valid grant covers all of it.
    covering: list[Grant] = []
    if not grant_ids:
        reasons.append("grant_missing")
        details.append("no grant was cited for this action")
    for grant_id in grant_ids:
        grant = trust_store.get(grant_id)
        if grant is None:
            reasons.append("grant_missing")
            details.append(f"{grant_id} does not exist in the trust store")
            continue
        if grant.project_id != project_id:
            reasons.append("grant_wrong_project")
            details.append(f"{grant_id} belongs to another project")
            continue
        if grant.status == "revoked":
            reasons.append("grant_revoked")
            details.append(f"{grant_id} was revoked at {grant.revoked_at}")
            continue
        if parse_timestamp(grant.expires_at) <= now:
            reasons.append("grant_expired")
            details.append(f"{grant_id} expired at {grant.expires_at}")
            continue
        if request.action not in grant.actions:
            reasons.append("action_not_granted")
            details.append(f"{grant_id} does not grant {request.action}")
            continue
        outside = [path for path in request.paths if not _in_scope(path, grant.path_scope)]
        if outside:
            reasons.append("target_out_of_scope")
            details.append(f"{grant_id} does not cover {outside[0]}")
            continue
        covering.append(grant)
    if covering:
        # A valid covering grant settles layer 1, even if other cited grants failed.
        reasons = [reason for reason in reasons if reason not in (
            "grant_missing", "grant_wrong_project", "grant_revoked", "grant_expired",
            "action_not_granted", "target_out_of_scope",
        )]

    # Layer 2: controller policy.
    if request.action not in controller_policy.allowed_actions:
        reasons.append("action_not_permitted_by_controller")
        details.append(f"controller policy does not allow {request.action}")

    # Layer 3: host policy, including enrollment and backend capabilities.
    if project_id not in host_policy.enrolled_projects:
        reasons.append("host_not_enrolled")
        details.append(f"host {host_policy.host_id} is not enrolled for this project")
    if request.action not in host_policy.allowed_actions:
        reasons.append("action_not_permitted_by_host")
        details.append(f"host {host_policy.host_id} does not allow {request.action}")
    missing = sorted(set(request.required_capabilities) - set(host_policy.backend_capabilities))
    if missing:
        reasons.append("capability_unavailable")
        details.append(f"host {host_policy.host_id} lacks: {', '.join(missing)}")

    # Layer 4: the request must fit inside both ceilings.
    ceiling = controller_policy.max_limits.intersect(host_policy.max_limits)
    over = ceiling.exceeded_by(request.limits)
    if over:
        reasons.append("limit_exceeded")
        details.append(f"requested limits exceed policy for: {', '.join(over)}")

    allowed = not reasons
    return AuthorizationDecision(
        allowed=allowed,
        reasons=["allowed"] if allowed else sorted(set(reasons)),
        details=details,
        effective_limits=request.limits if allowed else None,
        grant_digests=sorted(grant_digest(grant) for grant in covering),
        policy_digest=policy_digest,
        used_grant_ids=sorted(grant.grant_id for grant in covering),
    )

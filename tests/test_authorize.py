"""Ticket F04: authorization is the intersection of grant, controller, host and request.

Acceptance (part): expired or absent grants prevent launch; an allow at one
layer cannot cancel a denial at another (EX-T02 covers revocation).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from forge.authority.authorize import (
    DEFAULT_LIMITS,
    ActionRequest,
    ControllerPolicy,
    HostPolicy,
    authorize_action,
)
from forge.authority.grants import GrantProposal
from forge.contracts.clock import format_timestamp
from forge.contracts.ids import new_id

PROJECT = new_id("project")


@pytest.fixture
def grant(trust, clock, human):  # noqa: ANN001, ANN201
    return trust.approve(
        GrantProposal(
            project_id=PROJECT,
            actions=["run_registered_tool"],
            path_scope=["src", "tests"],
            justification="run the tests",
            expires_at=format_timestamp(clock.now() + timedelta(days=1)),
        ),
        human,
    )


def host(**overrides):  # noqa: ANN003, ANN201
    fields = {
        "host_id": "local",
        "enrolled_projects": [PROJECT],
        "allowed_actions": ["run_registered_tool", "read_source"],
        "max_limits": DEFAULT_LIMITS,
        "backend_capabilities": ["network_none", "filesystem_boundary"],
    }
    return HostPolicy(**{**fields, **overrides})


CONTROLLER = ControllerPolicy(allowed_actions=["run_registered_tool", "read_source"], max_limits=DEFAULT_LIMITS)


def decide(trust, clock, grant_ids, *, request=None, host_policy=None, controller=CONTROLLER):  # noqa: ANN001, ANN201
    return authorize_action(
        project_id=PROJECT,
        request=request
        or ActionRequest(
            action="run_registered_tool",
            paths=["src/reader.py", "tests/test_reader.py"],
            limits=DEFAULT_LIMITS,
            required_capabilities=["network_none"],
        ),
        grant_ids=grant_ids,
        trust_store=trust,
        host_policy=host_policy or host(),
        controller_policy=controller,
        now=clock.now(),
    )


def test_all_layers_allow(trust, clock, grant) -> None:  # noqa: ANN001
    decision = decide(trust, clock, [grant.grant_id])
    assert decision.allowed and decision.reasons == ["allowed"]
    assert decision.effective_limits == DEFAULT_LIMITS
    assert decision.used_grant_ids == [grant.grant_id]
    assert decision.grant_digests[0].startswith("sha256:")


def test_absent_grant_denies(trust, clock) -> None:  # noqa: ANN001
    assert decide(trust, clock, []).reasons == ["grant_missing"]
    assert decide(trust, clock, [new_id("grant")]).reasons == ["grant_missing"]


def test_expired_grant_denies(trust, clock, grant) -> None:  # noqa: ANN001
    clock.advance(days=2)
    assert decide(trust, clock, [grant.grant_id]).reasons == ["grant_expired"]


def test_revoked_grant_denies_ex_t02(trust, clock, grant, human) -> None:  # noqa: ANN001
    assert decide(trust, clock, [grant.grant_id]).allowed
    trust.revoke(grant.grant_id, human)  # revoked while the job waits in a queue
    decision = decide(trust, clock, [grant.grant_id])
    assert not decision.allowed
    assert decision.reasons == ["grant_revoked"]
    assert grant.grant_id in decision.details[0]


def test_path_outside_grant_scope_denies(trust, clock, grant) -> None:  # noqa: ANN001
    request = ActionRequest(action="run_registered_tool", paths=["secrets/prod.cfg"], limits=DEFAULT_LIMITS)
    assert decide(trust, clock, [grant.grant_id], request=request).reasons == ["target_out_of_scope"]
    # "src" must not cover "srcfake/..." (prefix confusion).
    request = ActionRequest(action="run_registered_tool", paths=["srcfake/x.py"], limits=DEFAULT_LIMITS)
    assert decide(trust, clock, [grant.grant_id], request=request).reasons == ["target_out_of_scope"]


def test_ungranted_action_denies(trust, clock, grant) -> None:  # noqa: ANN001
    request = ActionRequest(action="apply_change", paths=["src/reader.py"], limits=DEFAULT_LIMITS)
    reasons = decide(trust, clock, [grant.grant_id], request=request).reasons
    assert "action_not_granted" in reasons


def test_host_and_controller_denials_cannot_be_overridden_by_a_grant(trust, clock, grant) -> None:  # noqa: ANN001
    assert decide(trust, clock, [grant.grant_id], host_policy=host(enrolled_projects=[])).reasons == [
        "host_not_enrolled"
    ]
    assert decide(trust, clock, [grant.grant_id], host_policy=host(allowed_actions=["read_source"])).reasons == [
        "action_not_permitted_by_host"
    ]
    strict = ControllerPolicy(allowed_actions=["read_source"], max_limits=DEFAULT_LIMITS)
    assert decide(trust, clock, [grant.grant_id], controller=strict).reasons == [
        "action_not_permitted_by_controller"
    ]


def test_missing_backend_capability_denies(trust, clock, grant) -> None:  # noqa: ANN001
    decision = decide(trust, clock, [grant.grant_id], host_policy=host(backend_capabilities=[]))
    assert decision.reasons == ["capability_unavailable"]


def test_limits_above_either_ceiling_deny(trust, clock, grant) -> None:  # noqa: ANN001
    small_host = host(max_limits=DEFAULT_LIMITS.model_copy(update={"memory_bytes": 64 * 1024 * 1024}))
    decision = decide(trust, clock, [grant.grant_id], host_policy=small_host)
    assert decision.reasons == ["limit_exceeded"]
    assert "memory_bytes" in decision.details[0]


def test_one_valid_grant_among_bad_ones_is_enough(trust, clock, grant) -> None:  # noqa: ANN001
    decision = decide(trust, clock, [new_id("grant"), grant.grant_id])
    assert decision.allowed
    assert decision.used_grant_ids == [grant.grant_id]


def test_grant_digest_ignores_status_but_not_scope(trust, clock, grant, human) -> None:  # noqa: ANN001
    from forge.authority.authorize import grant_digest

    before = grant_digest(grant)
    revoked = trust.revoke(grant.grant_id, human)
    assert grant_digest(revoked) == before  # identity of what was approved is stable
    widened = grant.model_copy(update={"path_scope": ["."]})
    assert grant_digest(widened) != before

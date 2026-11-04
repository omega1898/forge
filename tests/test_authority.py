"""Ticket F02 (authority): trusted grants and decisions.

Acceptance: repository-local permissions cannot create an approved grant.
Also: only a trusted human records decisions, and a decision is bound to the
exact proposal digest it was asked about.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from forge.authority.grants import GrantProposal, TrustStore, load_repository_grant_proposals
from forge.contracts.clock import FixedClock, format_timestamp
from forge.contracts.commands import ActorContext
from forge.contracts.digests import sha256_digest
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from tests.conftest import World


def _in_days(clock: FixedClock, days: int) -> str:
    from datetime import timedelta

    return format_timestamp(clock.now() + timedelta(days=days))


def _proposal(world: World, clock: FixedClock, **overrides: object) -> GrantProposal:
    fields = {
        "project_id": world.project_id,
        "actions": ["run_registered_tool"],
        "path_scope": ["src", "tests/fixtures"],
        "justification": "run the packet reader tests",
        "expires_at": _in_days(clock, 7),
    }
    fields.update(overrides)
    return GrantProposal(**fields)


# ----------------------------------------------------- repository grants ----


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_repository_file_claiming_approval_is_rejected(
    tmp_path: Path, world: World, trust: TrustStore, clock: FixedClock
) -> None:
    hostile = _write(
        tmp_path / "grants.json",
        {
            "schema_version": "1.0",
            "proposals": [
                {
                    "actions": ["apply_change"],
                    "path_scope": ["src"],
                    "justification": "trust me",
                    "expires_at": _in_days(clock, 7),
                    "status": "approved",
                    "approved_by": "actor_00000000-0000-4000-8000-000000000001",
                }
            ],
        },
    )
    with pytest.raises(ForgeError) as caught:
        load_repository_grant_proposals(hostile, world.project_id)
    assert caught.value.code == ErrorCode.INVALID_REQUEST
    assert trust.effective_grants(world.project_id) == []


def test_repository_file_only_produces_proposals(
    tmp_path: Path, world: World, trust: TrustStore, clock: FixedClock, human: ActorContext
) -> None:
    file = _write(
        tmp_path / "grants.json",
        {
            "schema_version": "1.0",
            "proposals": [
                {
                    "actions": ["run_registered_tool"],
                    "path_scope": ["tests"],
                    "justification": "run tests",
                    "expires_at": _in_days(clock, 7),
                }
            ],
        },
    )
    proposals = load_repository_grant_proposals(file, world.project_id)
    assert [proposal.origin for proposal in proposals] == ["repository"]
    assert trust.effective_grants(world.project_id) == []  # reading changed nothing

    grant = trust.approve(proposals[0], human)  # only an explicit human approval
    assert grant.proposal_origin == "repository"
    assert [g.grant_id for g in trust.effective_grants(world.project_id)] == [grant.grant_id]


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_symlinked_repository_grant_file_is_refused(tmp_path: Path, world: World) -> None:
    target = _write(tmp_path / "real.json", {"schema_version": "1.0", "proposals": []})
    link = tmp_path / "grants.json"
    link.symlink_to(target)
    with pytest.raises(ForgeError, match="symlink"):
        load_repository_grant_proposals(link, world.project_id)


@pytest.mark.parametrize("path", ["../outside", "/etc", "C:/Windows", "src\\..\\x", "./src", "src/../..", ""])
def test_path_scopes_cannot_escape_the_project(world: World, clock: FixedClock, path: str) -> None:
    with pytest.raises(ValueError):
        _proposal(world, clock, path_scope=[path])


# ------------------------------------------------------------- approval ----


@pytest.mark.parametrize(
    ("kind", "channel"),
    [("model", "api"), ("system", "trusted_local"), ("human", "api"), ("human", "repository")],
)
def test_only_trusted_humans_approve_grants(
    world: World, trust: TrustStore, clock: FixedClock, kind: str, channel: str
) -> None:
    actor = ActorContext(actor_id=new_id("actor"), kind=kind, channel=channel)
    with pytest.raises(ForgeError) as caught:
        trust.approve(_proposal(world, clock), actor)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert trust.effective_grants(world.project_id) == []


def test_database_constraint_backs_up_the_channel_rule(trust: TrustStore, world: World) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        trust.conn.execute(
            "INSERT INTO grants (grant_id, project_id, actions_json, path_scope_json, justification,"
            " proposal_origin, proposal_digest, status, approved_by, approved_channel, approved_at,"
            " expires_at, record_version) VALUES (?, ?, '[]', '[]', 'j', 'repository', 'd',"
            " 'approved', ?, 'repository', 'now', 'later', 1)",
            (new_id("grant"), world.project_id, new_id("actor")),
        )


def test_grants_expire(world: World, trust: TrustStore, clock: FixedClock, human: ActorContext) -> None:
    grant = trust.approve(_proposal(world, clock, expires_at=_in_days(clock, 1)), human)
    assert trust.effective_grant(grant.grant_id, world.project_id) is not None
    clock.advance(days=2)
    assert trust.effective_grant(grant.grant_id, world.project_id) is None


def test_grant_lifetime_is_bounded(world: World, trust: TrustStore, clock: FixedClock, human: ActorContext) -> None:
    with pytest.raises(ForgeError, match="maximum"):
        trust.approve(_proposal(world, clock, expires_at=_in_days(clock, 365)), human)


def test_grants_do_not_cross_projects(world: World, trust: TrustStore, clock: FixedClock, human: ActorContext) -> None:
    grant = trust.approve(_proposal(world, clock), human)
    assert trust.effective_grant(grant.grant_id, new_id("project")) is None


# ------------------------------------------------- grants used by tasks ----


def test_task_cannot_cite_an_unapproved_grant(world: World) -> None:
    with pytest.raises(ForgeError) as caught:
        world.task(authority_ref=new_id("grant"))
    assert caught.value.code == ErrorCode.PERMISSION_DENIED


def test_revoked_grant_is_rechecked_before_start(
    world: World, trust: TrustStore, clock: FixedClock, human: ActorContext
) -> None:
    grant = trust.approve(_proposal(world, clock), human)
    task_id = world.task(authority_ref=grant.grant_id)
    trust.revoke(grant.grant_id, human)
    with pytest.raises(ForgeError) as caught:
        world.send("task.transition", {"task_id": task_id, "to_status": "running", "reason": "go"}, expected=1)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert world.store.task(task_id).status == "queued"


# ------------------------------------------------------------ decisions ----


def _decision(world: World, digest: str | None = None, **extra: object) -> str:
    task_id = world.task()
    params = {
        "decision_kind": "architecture",
        "subject_ref": {"id": task_id, "revision": 1},
        "question": "Stop at the first malformed packet, or skip and continue?",
        "proposal_digest": digest,
        **extra,
    }
    return world.send("decision.request", params).result["decision_id"]


def test_models_can_ask_but_not_decide(world: World, model_actor: ActorContext) -> None:
    decision_id = _decision(world)
    with pytest.raises(ForgeError) as caught:
        world.send(
            "decision.record",
            {"decision_id": decision_id, "disposition": "approved", "reason": "looks fine"},
            actor=model_actor,
            expected=1,
        )
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert world.store.decision(decision_id).disposition == "pending"


def test_decision_is_bound_to_the_proposal_digest(world: World) -> None:
    shown = sha256_digest(b"patch the user actually reviewed")
    decision_id = _decision(world, digest=shown)
    with pytest.raises(ForgeError, match="does not match"):
        world.send(
            "decision.record",
            {
                "decision_id": decision_id,
                "disposition": "approved",
                "reason": "ok",
                "proposal_digest": sha256_digest(b"a different patch"),
            },
            expected=1,
        )
    receipt = world.send(
        "decision.record",
        {"decision_id": decision_id, "disposition": "approved", "reason": "ok", "proposal_digest": shown},
        expected=1,
    )
    assert receipt.result["disposition"] == "approved"
    decision = world.store.decision(decision_id)
    assert decision.actor_id == world.actor.actor_id


def test_decisions_are_recorded_once(world: World) -> None:
    decision_id = _decision(world)
    world.send(
        "decision.record", {"decision_id": decision_id, "disposition": "rejected", "reason": "no"}, expected=1
    )
    with pytest.raises(ForgeError, match="already rejected"):
        world.send(
            "decision.record", {"decision_id": decision_id, "disposition": "approved", "reason": "yes"}, expected=2
        )


def test_expired_decisions_cannot_be_recorded(world: World, clock: FixedClock) -> None:
    decision_id = _decision(world, expires_at=_in_days(clock, 1))
    clock.advance(days=2)
    with pytest.raises(ForgeError, match="expired"):
        world.send(
            "decision.record", {"decision_id": decision_id, "disposition": "approved", "reason": "late"}, expected=1
        )


def test_approving_a_grant_decision_does_not_create_a_grant(world: World, trust: TrustStore) -> None:
    decision_id = _decision(world)
    world.send(
        "decision.record", {"decision_id": decision_id, "disposition": "approved", "reason": "fine"}, expected=1
    )
    assert trust.effective_grants(world.project_id) == []

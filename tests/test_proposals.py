"""Ticket F12 (proposals and safe apply) and D07 (generate-and-verify).

Acceptance: a user edit after review blocks a stale apply while preserving
both versions. Covers INT-T09 (two patches on one base stay distinct),
INT-T10 (a repair may not change evaluator configuration), INT-T11 (a loop
repeating itself stops with no_progress), and crash recovery of a
multi-file apply.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

from forge.amplifier.loop import TaskAssistant
from forge.amplifier.verify import admit_fix, admit_generated_suite
from forge.assessment.service import AssessmentService
from forge.assessment.suite import EvaluatorSuite
from forge.authority.grants import GrantProposal
from forge.contracts.clock import format_timestamp
from forge.contracts.errors import ErrorCode, ForgeError
from forge.gateway.adapters import FakeModelAdapter
from forge.gateway.contracts import ModelConfig
from forge.gateway.service import ModelGateway
from forge.knowledge.index import SnapshotIndex
from forge.sandbox.linux_ns import LinuxNamespaceBackend
from forge.workspace import apply as apply_module
from forge.workspace.apply import ApplyService
from forge.workspace.candidates import create_candidate
from forge.workspace.patches import apply_file_patch, parse_patch
from tests.conftest import command
from tests.test_evaluator import BUGGY, CORRECT, suite_document

BACKEND = LinuxNamespaceBackend()
needs_sandbox = pytest.mark.skipif(not BACKEND.capabilities().available, reason="needs the Linux sandbox")


def diff(path: str, before: str | None, after: str | None) -> str:
    return "".join(difflib.unified_diff(
        [] if before is None else before.splitlines(keepends=True),
        [] if after is None else after.splitlines(keepends=True),
        fromfile="/dev/null" if before is None else f"a/{path}", tofile="/dev/null" if after is None else f"b/{path}",
    ))


# ----------------------------------------------------------- strict patches --


def test_patches_apply_exactly_or_not_at_all() -> None:
    [patch] = parse_patch(diff("reader.py", BUGGY, CORRECT))
    assert apply_file_patch(BUGGY, patch) == CORRECT
    with pytest.raises(ForgeError, match="does not apply exactly"):
        apply_file_patch(BUGGY.replace("packets", "items"), patch)  # context no longer matches


def test_create_and_delete_patches() -> None:
    [created] = parse_patch(diff("new.py", None, "x = 1\n"))
    assert created.created and apply_file_patch(None, created) == "x = 1\n"
    [deleted] = parse_patch(diff("old.py", "y = 2\n", None))
    assert deleted.deleted and apply_file_patch("y = 2\n", deleted) is None
    with pytest.raises(ForgeError, match="already exists"):
        apply_file_patch("x = 1\n", created)


@pytest.mark.parametrize("bad", [
    "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
    "diff --git a/x b/y\nrename from x\nrename to y\n",
    "GIT binary patch\nliteral 0\n",
    "--- a/x.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+b\n",
    "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\n+b\n",  # line counts lie
    "no diff here",
])
def test_hostile_or_malformed_patches_are_refused(bad: str) -> None:
    with pytest.raises(ForgeError):
        parse_patch(bad)


# --------------------------------------------------------------- fixtures --


@pytest.fixture
def rig(controller, human, trust, clock, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "reader.py").write_text(BUGGY)
    (source / "util.py").write_text("HELPER = 1\n")
    (source / "tests" / "test_reader.py").write_text("def test_placeholder():\n    assert True\n")
    project_id = controller.apply_command(command("project.create", {
        "name": "p", "objective": "o", "source_roots": ["main"]}), human).project_id
    trust.approve_source_root(project_id, "main", source, human)
    base = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                            project_id=project_id), human).result["snapshot_id"]
    admitted = controller.apply_command(command("suite.admit", {"suite": suite_document()},
                                                project_id=project_id), human).result
    criterion_id = controller.apply_command(command("criterion.create", {
        "subject": "boundary cases", "assessment_method": "black_box_cases",
        "parameters": {"suite_artifact_id": admitted["suite_artifact_id"], "suite_digest": admitted["suite_digest"]},
    }, project_id=project_id), human).result["criterion_id"]

    def candidate(files: dict[str, str]) -> str:
        return create_candidate(controller, base, {k: v.encode() for k, v in files.items()})["snapshot_id"]

    def propose(candidate_id: str, kind: str = "implementation") -> dict:
        return controller.apply_command(command("proposal.create", {
            "kind": kind, "base_snapshot_id": base, "candidate_snapshot_id": candidate_id},
            project_id=project_id), controller.system_actor).result

    def approve(proposal: dict) -> str:
        decision_id = controller.apply_command(command("decision.request", {
            "decision_kind": "apply_change", "subject_ref": {"id": proposal["proposal_id"], "revision": 1},
            "question": "Apply this change?", "proposal_digest": proposal["proposal_digest"]},
            project_id=project_id), human).result["decision_id"]
        controller.apply_command(command("decision.record", {
            "decision_id": decision_id, "disposition": "approved", "reason": "reviewed the diff",
            "proposal_digest": proposal["proposal_digest"]}, project_id=project_id, expected=1), human)
        return decision_id

    return {"controller": controller, "human": human, "trust": trust, "clock": clock, "source": source,
            "base": base, "criterion_id": criterion_id, "project_id": project_id, "candidate": candidate,
            "propose": propose, "approve": approve, "suite": admitted,
            "apply": ApplyService(controller, tmp_path / "journal"), "tmp": tmp_path}


# ----------------------------------------------------------------- apply --


def test_approved_proposal_is_applied_and_recorded(rig) -> None:  # noqa: ANN001
    proposal = rig["propose"](rig["candidate"]({"reader.py": CORRECT}))
    rig["approve"](proposal)
    result = rig["apply"].apply(proposal["proposal_id"], proposal["proposal_digest"], rig["human"])
    assert result["outcome"] == "applied"
    assert (rig["source"] / "reader.py").read_text() == CORRECT
    store = rig["controller"].store
    assert store.proposal(proposal["proposal_id"])["status"] == "applied"
    assert [a["outcome"] for a in store.applications(proposal["proposal_id"])] == ["applied"]
    assert not list((rig["source"]).glob(".*forge-stage*"))
    assert store.verify_projections() == []


def test_apply_without_approval_is_refused(rig) -> None:  # noqa: ANN001
    proposal = rig["propose"](rig["candidate"]({"reader.py": CORRECT}))
    with pytest.raises(ForgeError) as caught:
        rig["apply"].apply(proposal["proposal_id"], proposal["proposal_digest"], rig["human"])
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert (rig["source"] / "reader.py").read_text() == BUGGY


def test_user_edit_after_review_blocks_stale_apply_and_keeps_both(rig) -> None:  # noqa: ANN001
    candidate_id = rig["candidate"]({"reader.py": CORRECT})
    proposal = rig["propose"](candidate_id)
    rig["approve"](proposal)
    users_edit = BUGGY + "# my own change while FORGE was working\n"
    (rig["source"] / "reader.py").write_text(users_edit)
    with pytest.raises(ForgeError) as caught:
        rig["apply"].apply(proposal["proposal_id"], proposal["proposal_digest"], rig["human"])
    assert caught.value.details["reason"] == "workspace_conflict"
    assert caught.value.details["conflicts"][0]["path"] == "reader.py"
    assert (rig["source"] / "reader.py").read_text() == users_edit  # the user's work is untouched
    snapshot = rig["controller"].store.snapshot(candidate_id)
    assert snapshot is not None  # the candidate is preserved too
    assert [a["outcome"] for a in rig["controller"].store.applications(proposal["proposal_id"])] == ["conflict"]


def test_approval_not_bound_to_the_reviewed_digest_is_not_enough(rig) -> None:  # noqa: ANN001
    proposal = rig["propose"](rig["candidate"]({"reader.py": CORRECT}))
    controller, human = rig["controller"], rig["human"]
    # An approval for the right proposal ID, but without the digest of the
    # content the person reviewed: it cannot authorize writing that content.
    decision_id = controller.apply_command(command("decision.request", {
        "decision_kind": "apply_change", "subject_ref": {"id": proposal["proposal_id"], "revision": 1},
        "question": "Apply?"}, project_id=rig["project_id"]), human).result["decision_id"]
    controller.apply_command(command("decision.record", {
        "decision_id": decision_id, "disposition": "approved", "reason": "looks fine"},
        project_id=rig["project_id"], expected=1), human)
    with pytest.raises(ForgeError) as caught:
        rig["apply"].apply(proposal["proposal_id"], proposal["proposal_digest"], human)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert (rig["source"] / "reader.py").read_text() == BUGGY


def test_presented_digest_must_match(rig) -> None:  # noqa: ANN001
    proposal = rig["propose"](rig["candidate"]({"reader.py": CORRECT}))
    rig["approve"](proposal)
    with pytest.raises(ForgeError) as caught:
        rig["apply"].apply(proposal["proposal_id"], "sha256:" + "0" * 64, rig["human"])
    assert caught.value.code == ErrorCode.STALE_VERSION


def test_crash_mid_apply_is_rolled_back_on_recovery(rig, monkeypatch) -> None:  # noqa: ANN001
    proposal = rig["propose"](rig["candidate"]({"reader.py": CORRECT, "util.py": "HELPER = 2\n"}))
    rig["approve"](proposal)
    real_replace, calls = os.replace, []

    def crash_on_second(src, dst):  # noqa: ANN001, ANN202
        if str(dst).endswith((".json", ".tmp")):
            return real_replace(src, dst)
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("simulated power loss")
        return real_replace(src, dst)

    monkeypatch.setattr(apply_module.os, "replace", crash_on_second)
    with pytest.raises(OSError, match="power loss"):
        rig["apply"].apply(proposal["proposal_id"], proposal["proposal_digest"], rig["human"])
    monkeypatch.undo()
    assert (rig["source"] / "reader.py").read_text() == CORRECT  # half-applied state on disk
    outcomes = rig["apply"].recover()
    assert [o["outcome"] for o in outcomes] == ["rolled_back"]
    assert (rig["source"] / "reader.py").read_text() == BUGGY       # restored
    assert (rig["source"] / "util.py").read_text() == "HELPER = 1\n"
    assert not list(rig["source"].glob(".*forge-stage*"))
    assert rig["controller"].store.proposal(proposal["proposal_id"])["status"] == "open"


def test_grant_allows_automatic_apply_within_scope_only(rig) -> None:  # noqa: ANN001
    from datetime import timedelta

    rig["trust"].approve(GrantProposal(
        project_id=rig["project_id"], actions=["apply_change"], path_scope=["reader.py"],
        justification="routine edits to the reader", expires_at=format_timestamp(rig["clock"].now() + timedelta(days=1))),
        rig["human"])
    inside = rig["propose"](rig["candidate"]({"reader.py": CORRECT}))
    assert rig["apply"].apply(inside["proposal_id"], inside["proposal_digest"], rig["controller"].system_actor)["outcome"] == "applied"
    outside = rig["propose"](rig["candidate"]({"util.py": "HELPER = 3\n"}))
    with pytest.raises(ForgeError) as caught:
        rig["apply"].apply(outside["proposal_id"], outside["proposal_digest"], rig["controller"].system_actor)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_destination_is_refused(rig, tmp_path) -> None:  # noqa: ANN001
    proposal = rig["propose"](rig["candidate"]({"reader.py": CORRECT}))
    rig["approve"](proposal)
    outside = tmp_path / "outside.py"
    outside.write_text(BUGGY)
    (rig["source"] / "reader.py").unlink()
    (rig["source"] / "reader.py").symlink_to(outside)
    with pytest.raises(ForgeError):
        rig["apply"].apply(proposal["proposal_id"], proposal["proposal_digest"], rig["human"])
    assert outside.read_text() == BUGGY


def test_implementation_may_not_change_tests_int_t10(rig) -> None:  # noqa: ANN001
    sneaky = rig["candidate"]({"reader.py": CORRECT, "tests/test_reader.py": "def test_placeholder():\n    pass\n"})
    with pytest.raises(ForgeError) as caught:
        rig["propose"](sneaky)
    assert caught.value.code == ErrorCode.PERMISSION_DENIED
    assert caught.value.details["protected_paths"] == ["tests/test_reader.py"]
    reviewed = rig["propose"](sneaky, kind="test_change")  # allowed as its own, separately reviewed proposal
    with pytest.raises(ForgeError):  # and a grant can never auto-apply it
        rig["apply"].apply(reviewed["proposal_id"], reviewed["proposal_digest"], rig["controller"].system_actor)


def test_proposal_for_unknown_task_is_a_clean_error(rig) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError) as caught:
        rig["controller"].apply_command(command("proposal.create", {
            "task_id": "task_00000000-0000-4000-8000-000000000001", "base_snapshot_id": rig["base"],
            "candidate_snapshot_id": rig["candidate"]({"reader.py": CORRECT})}, project_id=rig["project_id"]),
            rig["controller"].system_actor)
    assert caught.value.code == ErrorCode.INVALID_REQUEST


def test_two_patches_on_one_base_stay_distinct_int_t09(rig) -> None:  # noqa: ANN001
    first = rig["candidate"]({"reader.py": CORRECT})
    second = rig["candidate"]({"reader.py": CORRECT + "\n# variant\n"})
    store = rig["controller"].store
    assert store.snapshot(first).manifest_digest != store.snapshot(second).manifest_digest
    assert rig["propose"](first)["proposal_digest"] != rig["propose"](second)["proposal_digest"]


# ------------------------------------------------------------------- D07 --


@needs_sandbox
def test_fix_is_admitted_only_if_it_fails_before_and_passes_after(rig) -> None:  # noqa: ANN001
    service = AssessmentService(rig["controller"], BACKEND, rig["tmp"] / "work")
    fixed = rig["candidate"]({"reader.py": CORRECT})
    assert admit_fix(service, rig["criterion_id"], rig["base"], fixed).admitted
    # "Fixing" something that never failed demonstrates nothing:
    no_repro = admit_fix(service, rig["criterion_id"], fixed, rig["candidate"]({"reader.py": CORRECT + "\n"}))
    assert not no_repro.admitted and "does not fail on the base" in no_repro.reason


@needs_sandbox
def test_generated_tests_must_be_able_to_fail(rig) -> None:  # noqa: ANN001
    service = AssessmentService(rig["controller"], BACKEND, rig["tmp"] / "work")
    good = rig["candidate"]({"reader.py": CORRECT})
    full = EvaluatorSuite.model_validate(suite_document())
    weak = EvaluatorSuite.model_validate({**suite_document(), "suite_id": "weak", "cases": suite_document()["cases"][:3]})
    assert admit_generated_suite(service, full, good, rig["base"]).admitted
    verdict = admit_generated_suite(service, weak, good, rig["base"])
    assert not verdict.admitted and "cannot detect the defect" in verdict.reason


# ------------------------------------------------------------ the loop --


def _task(rig) -> str:  # noqa: ANN001
    return rig["controller"].apply_command(command("task.create", {
        "task_type": "prepare", "title": "reject truncated payloads",
        "budget": {"max_wall_ms": 60_000, "max_attempts": 2}}, project_id=rig["project_id"]),
        rig["human"]).result["task_id"]


def _assistant(rig, *responses):  # noqa: ANN001, ANN202
    fake = FakeModelAdapter([{"kind": "text", "text": "ready"}, {"answer": "yes"}, *responses])
    gateway = ModelGateway(rig["controller"], adapters={"fake": fake})
    gateway.register(ModelConfig(model_config_id="m", provider_type="fake", model_name="m", runtime_version="t",
                                 context_capacity=32_768, max_output_tokens=4096))
    gateway.probe("m")
    service = AssessmentService(rig["controller"], BACKEND, rig["tmp"] / "work")
    return TaskAssistant(rig["controller"], gateway, "m", SnapshotIndex(rig["tmp"] / "index"), service,
                         observe_turns=4, max_candidates=2)


def test_repeating_the_same_observation_stops_with_no_progress_int_t11(rig) -> None:  # noqa: ANN001
    look = {"action": "observe", "rationale": "look again", "tool_id": "list_files"}
    # The first call yields new evidence; then the model keeps asking for nothing new.
    assistant = _assistant(rig, look, {"action": "observe", "rationale": "x", "tool_id": "nonexistent"},
                           {"action": "observe", "rationale": "x", "tool_id": "nonexistent"},
                           {"action": "observe", "rationale": "x", "tool_id": "nonexistent"})
    report = assistant.run(_task(rig), rig["base"], rig["criterion_id"], "fix it")
    # Turn 1 lists files (new evidence). Turn 2 asks for an unavailable tool
    # (the refusal is new evidence). Turn 3 repeats it, now knowing it fails
    # (first time this evidence set is seen). Turn 4 repeats the same action on
    # the same evidence: no progress.
    assert report.status == "no_progress"
    assert report.turns == 4
    assert report.candidates == []


@needs_sandbox
def test_loop_turns_observation_and_patch_into_a_reviewable_proposal(rig) -> None:  # noqa: ANN001
    assistant = _assistant(
        rig,
        {"action": "observe", "rationale": "read the parser", "tool_id": "read_file", "tool_arguments": ["reader.py"]},
        {"action": "propose_patch", "rationale": "check payload length", "patch": diff("reader.py", BUGGY, CORRECT),
         "affected_paths": ["reader.py"]},
    )
    report = assistant.run(_task(rig), rig["base"], rig["criterion_id"], "reject truncated payloads")
    assert report.status == "ready_for_review"
    assert "packets.append" in report.observations[0]
    proposal = rig["controller"].store.proposal(report.proposal_id)
    assert [c["path"] for c in proposal["changes"]] == ["reader.py"]

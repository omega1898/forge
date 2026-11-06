"""Ticket F12 (proposals and safe apply) and D07 (generate-and-verify).

Acceptance: a user edit after review blocks a stale apply while preserving
both versions. Covers INT-T09 (two patches on one base stay distinct),
INT-T10 (a repair may not change evaluator configuration), INT-T11 (a loop
repeating itself stops with no_progress), and crash recovery of a
multi-file apply.
"""

from __future__ import annotations

import shutil

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


@pytest.mark.skipif(shutil.which("ruff") is None or not BACKEND.capabilities().available,
                    reason="needs ruff and the Linux sandbox")
def test_the_quality_gate_rejects_a_passing_patch_that_breaks_a_standard(rig) -> None:  # noqa: ANN001
    """Harness as code: tests pass, but eval() (bandit B307, CWE-78, in the 2025 Top 25) is a new blocking finding."""
    from forge.check.service import CheckService
    from forge.quality.guard import QualityGate
    from forge.quality.runner import QualityRunner

    sneaky = CORRECT.replace("print(json.dumps(packets))", "_ = eval('0')\nprint(json.dumps(packets))")
    assistant = _assistant(
        rig,
        {"action": "propose_patch", "rationale": "fix", "patch": diff("reader.py", BUGGY, sneaky),
         "affected_paths": ["reader.py"]},
        {"action": "propose_patch", "rationale": "fix without eval", "patch": diff("reader.py", BUGGY, CORRECT),
         "affected_paths": ["reader.py"]})
    checks = CheckService(rig["controller"], BACKEND, rig["tmp"] / "quality-work")
    assistant.quality_gate = QualityGate(QualityRunner(checks), {"toolchain": "python", "entry": "reader.py"}, None)
    report = assistant.run(_task(rig), rig["base"], rig["criterion_id"], "reject truncated payloads")
    assert report.status == "ready_for_review" and len(report.candidates) == 2
    assert any(o.startswith("quality gate rejected the candidate: 1 new") for o in report.observations)
    second_prompt = [c for c in rig["controller"].store.model_calls() if c["slot_id"] == "next_step"][-1]
    assert second_prompt["status"] == "valid"


# ------------------------------------------------ what the model may see --


def _look(assistant, snapshot_id: str, tool_id: str, *arguments: str) -> str:  # noqa: ANN001
    from forge.gateway.proposals import AgentProposal

    return assistant._observe(AgentProposal(action="observe", rationale="look", tool_id=tool_id,
                                            tool_arguments=list(arguments)), snapshot_id)


def test_no_read_tool_shows_the_model_the_protected_evaluation(rig) -> None:  # noqa: ANN001
    """list_files, read_file and search_code all pass through visibility.py, whatever the letter case."""
    from forge.workspace.visibility import HIDDEN_NOTICE

    suite_bytes = rig["controller"].blobs.read_bytes(rig["suite"]["suite_digest"], max_bytes=10**6)
    snapshot = create_candidate(rig["controller"], rig["base"], {
        "hidden_tests/case_one.txt": b"SECRETHIDDENCASE\n",
        "Evaluation/expected.json": b'{"SECRETEVALUATOR": 1}\n',
        "notes/answers.json": suite_bytes,  # a copy of the admitted suite, under an innocent name
        "forge.toml": b'[build]\ntoolchain = "python"\nentry = "reader.py"\n\n'
                      b'[[tests]]\nid = "short"\nargs = ["x"]\nstdout = "SECRETEXPECTED\\n"\n',
    })["snapshot_id"]
    assistant = _assistant(rig)
    hidden = ("hidden_tests/case_one.txt", "Evaluation/expected.json", "notes/answers.json")

    listing = _look(assistant, snapshot, "list_files").splitlines()
    assert {"reader.py", "forge.toml", "tests/test_reader.py"} <= set(listing)
    assert not set(hidden) & set(listing)
    for path in hidden:
        assert _look(assistant, snapshot, "read_file", path) == f"{path}: {HIDDEN_NOTICE}"
    project = _look(assistant, snapshot, "read_file", "forge.toml")
    assert 'args = ["x"]' in project and "SECRETEXPECTED" not in project
    for query in ("SECRETHIDDENCASE", "SECRETEVALUATOR", "SECRETEXPECTED", "acceptance cases packet reader"):
        hits = _look(assistant, snapshot, "search_code", query)
        assert not any(path in hits for path in (*hidden, "forge.toml")), (query, hits)
    assert "reader.py" in _look(assistant, snapshot, "search_code", "packets")  # search itself still works
    # The index the loop built quarantines the suite copy on its own (one layer, tested alone).
    digest = rig["controller"].store.snapshot(snapshot).manifest_digest
    assert "notes/answers.json" not in [r.path for r in assistant.index.search(digest, "acceptance cases packet")]


def test_the_loop_filters_search_hits_even_from_an_index_that_leaks(rig) -> None:  # noqa: ANN001
    """The other layer alone: whatever the index returns, a hidden path never reaches the model."""
    from forge.knowledge.index import SourceRef

    snapshot = create_candidate(rig["controller"], rig["base"], {"hidden_tests/case.txt": b"x\n"})["snapshot_id"]
    assistant = _assistant(rig)

    class LeakyIndex:
        def build(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            return {}

        def search(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            return [SourceRef(path=path, start_line=1, end_line=1, kind="chunk", name=path, score=1.0, reason="r")
                    for path in ("hidden_tests/case.txt", "reader.py")]

    assistant.index = LeakyIndex()
    assert _look(assistant, snapshot, "search_code", "anything") == "reader.py:1 chunk reader.py"


def test_a_patch_to_tests_or_the_project_file_is_refused_with_feedback_not_a_crash(rig) -> None:  # noqa: ANN001
    test_text = "def test_placeholder():\n    assert True\n"
    weaken = diff("tests/test_reader.py", test_text, "def test_placeholder():\n    pass\n")
    other_case = diff("Tests/test_extra.py", None, "def test_extra():\n    pass\n")  # one folder on Windows and macOS
    project_file = diff("forge.toml", None, '[build]\ntoolchain = "python"\n')
    assistant = _assistant(
        rig,
        {"action": "propose_patch", "rationale": "the test is wrong", "patch": weaken,
         "affected_paths": ["reader.py"]},  # the claim is ignored; the patch's own paths are checked
        {"action": "propose_patch", "rationale": "add a test", "patch": other_case,
         "affected_paths": ["Tests/test_extra.py"]},
        {"action": "propose_patch", "rationale": "configure the build", "patch": project_file,
         "affected_paths": ["forge.toml"]},
        {"action": "needs_user", "rationale": "stuck", "question": "Is the placeholder test wrong?"})
    report = assistant.run(_task(rig), rig["base"], rig["criterion_id"], "fix it")
    assert report.status == "waiting_for_user"
    assert report.candidates == []  # refused before anything was built or run
    refused = [o for o in report.observations if o.startswith("patch rejected: it changes")]
    assert [o.split("[")[1].split("]")[0] for o in refused] == [
        "'tests/test_reader.py'", "'Tests/test_extra.py'", "'forge.toml'"]
    # Each refusal is in the model's next prompt, so it learns why instead of seeing the loop stop.
    prompts = [r.messages[-1].content for r in assistant.gateway.adapters["fake"].requests]
    assert "define what correct means" in prompts[-1] and "needs_user" in prompts[-1]


@needs_sandbox
def test_after_a_refused_test_edit_the_model_can_still_fix_the_code(rig) -> None:  # noqa: ANN001
    weaken = diff("tests/test_reader.py", "def test_placeholder():\n    assert True\n", "")
    assistant = _assistant(
        rig,
        {"action": "propose_patch", "rationale": "delete the test", "patch": weaken,
         "affected_paths": ["tests/test_reader.py"]},
        {"action": "propose_patch", "rationale": "check payload length", "patch": diff("reader.py", BUGGY, CORRECT),
         "affected_paths": ["reader.py"]})
    report = assistant.run(_task(rig), rig["base"], rig["criterion_id"], "reject truncated payloads")
    assert report.status == "ready_for_review" and len(report.candidates) == 1
    proposal = rig["controller"].store.proposal(report.proposal_id)
    assert [c["path"] for c in proposal["changes"]] == ["reader.py"]


@needs_sandbox
def test_a_refused_proposal_is_feedback_for_the_next_turn(rig, monkeypatch) -> None:  # noqa: ANN001
    controller = rig["controller"]
    original = controller.apply_command

    def refuse_proposals(command, actor):  # noqa: ANN001, ANN202
        if command["method"] == "proposal.create":
            raise ForgeError(ErrorCode.STALE_VERSION, "the task was cancelled while the candidate ran")
        return original(command, actor)

    monkeypatch.setattr(controller, "apply_command", refuse_proposals)
    assistant = _assistant(
        rig,
        {"action": "propose_patch", "rationale": "check payload length", "patch": diff("reader.py", BUGGY, CORRECT),
         "affected_paths": ["reader.py"]},
        {"action": "needs_user", "rationale": "cannot propose", "question": "Was the task cancelled?"})
    report = assistant.run(_task(rig), rig["base"], rig["criterion_id"], "reject truncated payloads")
    assert report.status == "waiting_for_user" and report.proposal_id is None
    assert report.observations[-1] == ("the candidate passed its tests but could not become a proposal: "
                                       "the task was cancelled while the candidate ran")


# ------------------------------------------------ files in every format --


def _formats_base(rig) -> str:  # noqa: ANN001
    import zipfile as _zipfile
    from io import BytesIO

    docx = BytesIO()
    with _zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>Plan text</w:t></w:r>'
                                              "</w:p></w:body></w:document>")
    return create_candidate(rig["controller"], rig["base"], {
        ".env": b'DB_HOST=db\nDB_PASSWORD="s3cr3t!"\nPORT=5432\n',
        "run.bat": b"@echo off\r\ngoto :start\r\n:start\r\necho old\r\n",
        "app.properties": "greeting=Grüße\nname=old\n".encode("latin-1"),
        "config.json": b'{"retries": 3}\n',
        "plan.docx": docx.getvalue(),
    })["snapshot_id"]


def test_the_model_reads_every_format_through_its_view(rig) -> None:  # noqa: ANN001
    base = _formats_base(rig)
    assistant = _assistant(rig)
    env = _look(assistant, base, "read_file", ".env")
    assert "s3cr3t!" not in env and "DB_PASSWORD=\"[FORGE-REDACTED-1: value]\"" in env and "PORT=5432" in env
    assert _look(assistant, base, "read_file", "plan.docx").startswith("Plan text")
    assert "CRLF line endings; edits keep them" in _look(assistant, base, "read_file", "run.bat")


def test_edits_keep_each_files_encoding_line_endings_and_secrets(rig) -> None:  # noqa: ANN001
    base = _formats_base(rig)
    assistant = _assistant(rig)
    files = assistant._files(base)

    def edit(path: str, old: str, new: str) -> bytes:
        view = _look(assistant, base, "read_file", path).split("\n-- FORGE notes")[0]
        [patch] = parse_patch(diff(path, view, view.replace(old, new)))
        data, problems, _note = assistant._edit(patch, files[path])
        assert problems == []
        return data

    assert edit("run.bat", "old", "new") == b"@echo off\r\ngoto :start\r\n:start\r\necho new\r\n"
    assert edit("app.properties", "name=old", "name=new") == "greeting=Grüße\nname=new\n".encode("latin-1")
    assert edit(".env", "5432", "6543") == b'DB_HOST=db\nDB_PASSWORD="s3cr3t!"\nPORT=6543\n'


def test_the_syntax_gate_refuses_a_broken_config_before_anything_is_built(rig) -> None:  # noqa: ANN001
    base = _formats_base(rig)
    assistant = _assistant(rig)
    from forge.gateway.proposals import AgentProposal

    def attempt(patch: str, path: str):  # noqa: ANN202
        return assistant.try_proposal(AgentProposal(action="propose_patch", rationale="r", patch=patch,
                                                    affected_paths=[path]), _task(rig), base, rig["criterion_id"])

    broken = attempt(diff("config.json", '{"retries": 3}\n', '{"retries": 3,}\n'), "config.json")
    assert broken.status == "rejected" and broken.candidates == []
    assert "config.json:1: error: JSON syntax error" in broken.detail
    secret = attempt(diff(".env", 'DB_PASSWORD="[FORGE-REDACTED-1: value]"\n', 'DB_PASSWORD="guess"\n'), ".env")
    assert secret.status == "rejected" and "does not apply" in secret.detail  # the real line is not the view's
    view = 'DB_HOST=db\nDB_PASSWORD="[FORGE-REDACTED-1: value]"\nPORT=5432\n'
    rewrite = attempt(diff(".env", view, view.replace("value]", "value] # rotated")), ".env")
    assert rewrite.status == "rejected" and "masked value" in rewrite.detail
    document = attempt(diff("plan.docx", "Plan text\n", "New text\n"), "plan.docx")
    assert document.status == "rejected" and "not edited as text" in document.detail

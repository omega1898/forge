"""D10 (deploy checklist, incidents, postmortems, doc freshness) and D11 (evaluation harness).

D10 acceptance: a document naming a deleted function is flagged; a deploy
item "tests pass" is satisfied only by current assessments for the release
snapshot. D11 acceptance: the report shows small-model-alone versus
small-model-plus-FORGE per capability with intervals; tuning never reads
the held-out set.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from forge.contracts.errors import ForgeError
from forge.evaluation.harness import Assertion, EvalCase, EvalSet, check, compare_variants, run_variant
from forge.knowledge.facts import Fact
from forge.knowledge.index import SnapshotIndex
from forge.ops.runbooks import (
    deploy_checklist,
    incident_timeline,
    render_checklist,
    render_postmortem,
    stale_references,
)
from forge.workspace.snapshot import load_manifest
from tests.conftest import command

# ----------------------------------------------------------------- D10 rig


@pytest.fixture
def release(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    source.mkdir()
    project_id = controller.apply_command(
        command("project.create", {"name": "Shop", "objective": "o", "source_roots": ["main"]}), human).project_id
    trust.approve_source_root(project_id, "main", source, human)

    def capture(files: dict[str, str]) -> str:
        for child in source.iterdir():
            child.unlink()
        for name, text in files.items():
            (source / name).write_text(text)
        return controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]

    def send(method: str, params: dict, actor=None) -> dict:  # noqa: ANN001
        return controller.apply_command(command(method, params, project_id=project_id),
                                        actor or controller.system_actor).result

    def assess(criterion_id: str, snapshot_id: str, result: str) -> str:
        evidence = send("evidence.record_review", {"subject_ref": {"id": criterion_id, "revision": 1},
                                                   "observation": {"result": result}}, human)["evidence_id"]
        return send("assessment.record", {"criterion_ref": {"id": criterion_id, "revision": 1},
                                          "snapshot_id": snapshot_id, "evidence_refs": [evidence],
                                          "assessment_result": result, "assessor_ref": "test",
                                          "limitations": ["test"]})["assessment_id"]

    criterion = send("criterion.create", {"subject": "Checkout works", "assessment_method": "test_suite"},
                     human)["criterion_id"]
    return {"capture": capture, "assess": assess, "criterion": criterion, "store": controller.store,
            "send": send, "human": human, "blobs": controller.blobs}


def test_tests_pass_counts_only_current_assessments_of_the_release_snapshot(release) -> None:
    old = release["capture"]({"shop.py": "def checkout():\n    return 1\n"})
    new = release["capture"]({"shop.py": "def checkout():\n    return 2\n"})
    release["assess"](release["criterion"], old, "pass")
    facts: list[Fact] = []
    [item, *_] = deploy_checklist(release["store"], new, facts, criterion_ids=[release["criterion"]])
    assert not item.satisfied and "no assessment for this snapshot" in item.reason
    release["assess"](release["criterion"], new, "pass")
    release["assess"](release["criterion"], new, "fail")              # the latest result is what counts
    [item, *_] = deploy_checklist(release["store"], new, facts, criterion_ids=[release["criterion"]])
    assert not item.satisfied and "not passing" in item.reason
    current = release["assess"](release["criterion"], new, "pass")
    [item, *_] = deploy_checklist(release["store"], new, facts, criterion_ids=[release["criterion"]])
    assert item.satisfied and item.evidence == [current]


def test_checklist_items_follow_facts_and_open_findings_block(release) -> None:
    snapshot = release["capture"]({"shop.py": "x = 1\n"})
    release["assess"](release["criterion"], snapshot, "pass")
    plain = deploy_checklist(release["store"], snapshot, [], criterion_ids=[release["criterion"]])
    assert [i.item_id for i in plain] == ["tests_pass", "no_blocking_findings"]
    facts = [Fact(name="data.migrations", value=True, evidence=["migrations/0001.sql"])]
    with_migrations = deploy_checklist(release["store"], snapshot, facts, criterion_ids=[release["criterion"]])
    assert [i.item_id for i in with_migrations][-1] == "migration_reviewed" and not with_migrations[-1].satisfied
    release["send"]("finding.record", {"rule_id": "sec.sqli", "claim": "user input reaches SQL | DROP", "severity": "high",
                                       "fingerprint": "sha256:" + "c" * 64, "snapshot_id": snapshot})
    items = deploy_checklist(release["store"], snapshot, [], criterion_ids=[release["criterion"]])
    assert not items[1].satisfied and "sec.sqli" in items[1].reason
    assert "**open**" in render_checklist(items)


def test_a_document_naming_a_deleted_function_is_flagged(release, tmp_path) -> None:
    snapshot = release["capture"]({"shop.py": "def checkout(cart):\n    return sum(cart)\n\nclass Cart:\n    pass\n"})
    manifest_digest = release["store"].snapshot(snapshot).manifest_digest
    index = SnapshotIndex(tmp_path / "index")
    index.build(load_manifest(release["blobs"], manifest_digest), manifest_digest, release["blobs"])
    documents = {"docs/guide.md": "Call `checkout()` with a `Cart`.\nThe old `apply_coupon()` path was removed.\n"
                                  "See `shop.refund()` and `docs/api.md`. Use `--verbose` for detail.\n"}
    flagged = stale_references(documents, index.defined_names(manifest_digest), files={"shop.py"})
    assert {(f["reference"], f["reason"]) for f in flagged} == {
        ("apply_coupon()", "not defined in the current snapshot"), ("shop.refund()", "not defined in the current snapshot"),
        ("docs/api.md", "file does not exist")}


def test_incident_timeline_sorts_deduplicates_and_shows_gaps() -> None:
    entries = [{"at": "2026-11-04T10:50:00Z", "source": "pager", "text": "rollback finished"},
               {"at": "2026-11-04T10:00:00Z", "source": "monitor", "text": "error rate 12%"},
               {"at": "2026-11-04T10:00:00Z", "source": "monitor", "text": "error rate 12% "},
               {"at": "2026-11-04T10:05:00Z", "source": "chat", "text": "deploy paused"}]
    timeline = incident_timeline(entries, gap_minutes=30)
    assert [e if isinstance(e, str) else e.text for e in timeline] == [
        "error rate 12%", "deploy paused", "gap: no recorded activity for 45 minutes", "rollback finished"]


def test_postmortems_are_blameless_and_every_action_has_an_owner() -> None:
    timeline = incident_timeline([{"at": "2026-11-04T10:00:00Z", "source": "monitor", "text": "errors"}])
    sections = {"summary": "Checkout failed for 50 minutes.", "impact": "1,200 failed orders.",
                "root_causes": ["a migration locked the orders table"], "contributing_factors": ["no lock timeout"],
                "what_went_well": ["rollback was rehearsed"],
                "action_items": [{"text": "add a lock timeout", "owner": "data team", "due": "2026-11-20",
                                  "link": "finding_x"}]}
    rendered = render_postmortem("Checkout outage", sections, timeline)
    order = [line for line in rendered.splitlines() if line.startswith("## ")]
    assert order == ["## Summary", "## Impact", "## Timeline", "## Root causes", "## Contributing factors",
                     "## What went well", "## Action items"]
    with pytest.raises(ForgeError) as caught:
        render_postmortem("x", {**sections, "action_items": [{"text": "fix it", "owner": "", "due": "", "link": ""}]},
                          timeline)
    assert "action item 1 has no owner" in caught.value.details["problems"]
    with pytest.raises(ForgeError) as caught:
        render_postmortem("x", {**sections, "summary": "Outage was the fault of the on-call engineer."}, timeline)
    assert "blameless" in caught.value.details["problems"][0]


# ---------------------------------------------------------------- D11 rig


def structured_cases() -> list[EvalCase]:
    cases = []
    for n in range(20):
        cases.append(EvalCase(case_id=f"json_{n}", capability="structured_output", input={"n": n},
                              assertions=[Assertion(kind="json_field_equals", path="square", value=n * n)]))
        cases.append(EvalCase(case_id=f"sum_{n}", capability="arithmetic", input={"n": n},
                              assertions=[Assertion(kind="equals", value=str(n + n))]))
    return cases


def flaky(case: dict, repetition: int, salt: str) -> bool:
    return hashlib.sha256(f"{salt}:{case['n']}:{repetition}".encode()).digest()[0] < 128   # about half


def small_model(case: dict, repetition: int, attempt: int = 0) -> str:
    """A stand-in for a small local model: right content, often wrapped in prose."""
    payload = json.dumps({"square": case["n"] ** 2})
    if flaky(case, repetition * 10 + attempt, "prose"):
        return f"Sure! Here is the JSON you asked for: {payload}"
    return payload


def small_model_arithmetic(case: dict, repetition: int) -> str:
    return str(case["n"] * 2) if not flaky(case, repetition, "math") else str(case["n"] * 2 + 1)


def alone(case: dict, repetition: int) -> str:
    return small_model(case, repetition) if case.get("task") != "sum" else small_model_arithmetic(case, repetition)


def with_forge(case: dict, repetition: int) -> str:
    """FORGE's contribution for structured output: validate against the schema, retry within a budget."""
    if case.get("task") == "sum":
        return small_model_arithmetic(case, repetition)       # FORGE adds nothing here; it must not look like it does
    for attempt in range(3):
        raw = small_model(case, repetition, attempt)
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict) and set(parsed) == {"square"}:
            return raw
    return ""


def test_frozen_split_is_deterministic_and_the_held_out_part_is_sealed() -> None:
    cases = structured_cases()
    first, second = EvalSet("small_model_boost", cases), EvalSet("small_model_boost", cases)
    assert [c.case_id for c in first.development()] == [c.case_id for c in second.development()]
    held = {c.case_id for c in first.held_out(purpose="final_evaluation")}
    dev = {c.case_id for c in first.development()}
    assert not held & dev and len(held) == 16 and {c.split("_")[0] for c in held} == {"json", "sum"}
    with pytest.raises(ForgeError, match="sealed"):
        first.held_out(purpose="prompt_tuning")
    assert "refused:prompt_tuning" in first.access_log
    edited = [*cases[:-1], cases[-1].model_copy(update={"input": {"n": 99}})]
    assert EvalSet("small_model_boost", edited).digest != first.digest


def test_assertions_are_deterministic() -> None:
    assert check(Assertion(kind="json_field_equals", path="a.b", value=2), '{"a": {"b": 2}}')
    assert not check(Assertion(kind="json_field_equals", path="a.b", value=2), "Here: {\"a\": {\"b\": 2}}")
    assert check(Assertion(kind="number_between", low=1, high=2), "1.5")
    assert not check(Assertion(kind="number_between", low=1, high=2), "two")
    assert check(Assertion(kind="regex", value=r"^\d+$"), "42") and check(Assertion(kind="not_contains", value="x"), "y")


def test_report_compares_model_alone_with_model_plus_forge_on_the_held_out_set() -> None:
    evalset = EvalSet("small_model_boost", structured_cases())
    # Tuning (for example choosing the retry budget) sees development cases only.
    assert all(c.case_id in {d.case_id for d in evalset.development()} for c in evalset.development())
    final = evalset.held_out(purpose="final_evaluation")
    tagged = [c.model_copy(update={"input": {**c.input, "task": "sum" if c.capability == "arithmetic" else "json"}})
              for c in final]
    baseline = run_variant("small_model_alone", alone, tagged, repeats=5)
    treatment = run_variant("small_model_plus_forge", with_forge, tagged, repeats=5)
    report = compare_variants(evalset, baseline, treatment, tagged)
    structured = report["capabilities"]["structured_output"]
    arithmetic = report["capabilities"]["arithmetic"]
    assert structured["conclusion"] == "improvement shown" and structured["difference_interval"][0] > 0
    assert structured["treatment"]["rate"] > structured["baseline"]["rate"]
    assert arithmetic["conclusion"] == "difference not shown"          # no boost claimed where none exists
    assert arithmetic["baseline"]["rate"] == arithmetic["treatment"]["rate"]
    assert structured["baseline"]["interval"][0] <= structured["baseline"]["rate"] <= structured["baseline"]["interval"][1]
    assert report["set_digest"] == evalset.digest and evalset.access_log.count("final_evaluation:1") == 1
    assert structured["flaky_cases"]                                     # repetition exposes inconsistency


def test_a_crashing_system_fails_cases_without_stopping_the_run() -> None:
    def broken(case: dict, repetition: int) -> str:
        raise RuntimeError("model server down")

    result = run_variant("down", broken, structured_cases()[:4], repeats=2)
    assert result.counts(structured_cases()[:4]) == (0, 8)


def test_a_small_positive_difference_is_not_claimed_as_an_improvement() -> None:
    from forge.evaluation.harness import VariantResult

    cases = [EvalCase(case_id=f"c{n}", capability="review", input={}, assertions=[Assertion(kind="equals", value="x")])
             for n in range(10)]
    evalset = EvalSet("tiny", cases)
    baseline = VariantResult("alone", {f"c{n}": [n < 6] for n in range(10)})        # 6 of 10
    treatment = VariantResult("forge", {f"c{n}": [n < 7] for n in range(10)})       # 7 of 10
    review = compare_variants(evalset, baseline, treatment, cases)["capabilities"]["review"]
    assert review["difference"] == 0.1 and review["difference_interval"][0] < 0
    assert review["conclusion"] == "difference not shown"

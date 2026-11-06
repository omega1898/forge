"""Ticket F10 (retrieval and context) and D05 (project facts).

Acceptance: context cites exact source snapshots; oversized or unauthorized
source is excluded with a reason. Covers INT-T02 (mandatory material never
silently dropped), INT-T06 (a real citation to an unrelated passage),
INT-T12 (a hidden evaluation suite placed in the repository is quarantined).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from forge.authority.grants import GrantProposal
from forge.contracts.clock import format_timestamp
from forge.contracts.errors import ForgeError
from forge.knowledge.citations import ClaimSupport, check_claims
from forge.knowledge.context import ContextBuilder
from forge.knowledge.facts import detect_facts, fact_value
from forge.knowledge.index import SnapshotIndex
from forge.workspace.snapshot import load_manifest
from tests.conftest import BUDGET, command

READER = '''"""Packet reader."""


class Packet:
    def __init__(self, kind, length):
        self.kind = kind
        self.length = length


def parse_packets(data):
    """Return packets; reject truncated input."""
    packets, offset = [], 0
    while offset < len(data):
''' + "".join(f"        step_{n} = {n}  # long body line {n}\n" for n in range(120)) + '''        offset += 1
    return packets
'''
TEST = '''from packet_reader.reader import parse_packets


def test_empty_input_gives_no_packets():
    assert parse_packets(b"") == []


def test_one_packet():
    assert parse_packets(b"\\x01\\x00\\x00")
'''


@pytest.fixture
def project(controller, human, trust, clock, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    (source / "src" / "packet_reader").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "private").mkdir()
    (source / "migrations").mkdir()
    (source / ".github" / "workflows").mkdir(parents=True)
    (source / "src" / "packet_reader" / "reader.py").write_text(READER)
    (source / "src" / "packet_reader" / "__main__.py").write_text("from .reader import parse_packets\n")
    (source / "tests" / "test_reader.py").write_text(TEST)
    (source / "private" / "parse_packets_notes.py").write_text("def parse_packets(): pass  # private notes\n")
    (source / "README.md").write_text("# Packet reader\nThis project reads packet files from disk.\n")
    (source / "pyproject.toml").write_text('[project]\nname="pr"\n[project.scripts]\npacket-reader="pr:main"\n'
                                           '[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    (source / "uv.lock").write_text("version = 1\n")
    (source / "migrations" / "0001.sql").write_text("CREATE TABLE t (x INTEGER);\n")
    (source / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    (source / "deep.py").write_text("x = " + "(" * 2000 + "1" + ")" * 2000 + "\n")  # breaks the parser

    project_id = controller.apply_command(
        command("project.create", {"name": "Packet reader", "objective": "o", "source_roots": ["main"]}), human
    ).project_id
    trust.approve_source_root(project_id, "main", source, human)
    suite = {"suite_id": "hidden", "revision": 1, "description": "hidden answers", "entrypoint": ["{python}", "x.py"],
             "cases": [{"case_id": "c", "description": "d", "expect": {"exit_code": 0, "stdout_text": "SECRET ANSWER"}}]}
    admitted = controller.apply_command(command("suite.admit", {"suite": suite}, project_id=project_id), human).result
    # Someone copies the hidden evaluation suite into the repository (INT-T12).
    (source / "notes").mkdir()
    (source / "notes" / "answers.json").write_bytes(controller.blobs.read_bytes(admitted["suite_digest"], max_bytes=10**6))

    grant = trust.approve(GrantProposal(project_id=project_id, actions=["read_source"], path_scope=["src", "tests"],
                                        justification="work on the reader",
                                        expires_at=format_timestamp(clock.now() + timedelta(days=1))), human)
    criterion_id = controller.apply_command(command("criterion.create", {
        "subject": "Truncated payloads produce a structured error", "assessment_method": "black_box_cases",
        "parameters": {"suite_artifact_id": admitted["suite_artifact_id"], "suite_digest": admitted["suite_digest"]},
    }, project_id=project_id), human).result["criterion_id"]
    task_id = controller.apply_command(command("task.create", {
        "task_type": "prepare", "title": "Fix parse_packets truncation handling", "budget": BUDGET,
        "authority_ref": grant.grant_id, "criterion_refs": [{"id": criterion_id, "revision": 1}],
    }, project_id=project_id), human).result["task_id"]
    snapshot_id = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                   project_id=project_id), human).result["snapshot_id"]
    index = SnapshotIndex(tmp_path / "index")
    builder = ContextBuilder(controller.store, controller.blobs, index, trust_store=trust)
    return {"task_id": task_id, "snapshot_id": snapshot_id, "builder": builder, "index": index, "source": source,
            "controller": controller, "human": human, "project_id": project_id}


def test_context_cites_exact_snapshot_spans(project) -> None:  # noqa: ANN001
    bundle = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    snapshot = project["controller"].store.snapshot(project["snapshot_id"])
    assert bundle.manifest_digest == snapshot.manifest_digest
    assert [span.kind for span in bundle.spans[:2]] == ["objective", "criterion"]
    definition = next(span for span in bundle.spans if span.kind == "definition" and "def parse_packets" in span.text)
    assert definition.path == "src/packet_reader/reader.py" and definition.start_line == 10
    assert any(span.kind == "test" and span.path == "tests/test_reader.py" for span in bundle.spans)
    assert all(span.text_digest.startswith("sha256:") for span in bundle.spans)


def test_long_definitions_are_marked_as_excerpts(project) -> None:  # noqa: ANN001
    bundle = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    definition = next(span for span in bundle.spans if span.kind == "definition" and "def parse_packets" in span.text)
    assert definition.complete is False
    assert "omitted; this is an excerpt" in definition.text
    assert "EXCERPT" in bundle.render()


def test_unauthorized_source_is_excluded_with_a_reason(project) -> None:  # noqa: ANN001
    bundle = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    assert not any(span.path and span.path.startswith("private/") for span in bundle.spans)
    assert any(o.ref.startswith("private/") and o.reason == "unauthorized" for o in bundle.omissions)


def test_hidden_evaluation_suite_in_the_repository_is_quarantined_int_t12(project) -> None:  # noqa: ANN001
    bundle = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000,
                                      query="SECRET ANSWER hidden answers")
    assert any(o.ref == "notes/answers.json" and o.reason == "evaluation_isolation" for o in bundle.omissions)
    assert "SECRET ANSWER" not in bundle.render()
    snapshot = project["controller"].store.snapshot(project["snapshot_id"])
    paths = [ref.path for ref in project["index"].search(snapshot.manifest_digest, "SECRET ANSWER")]
    assert "notes/answers.json" not in paths


def test_mandatory_material_is_never_silently_dropped_int_t02(project) -> None:  # noqa: ANN001
    with pytest.raises(ForgeError) as caught:
        project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=5)
    assert caught.value.details["reason"] == "context_infeasible"


def test_optional_material_over_budget_is_omitted_with_a_reason(project) -> None:  # noqa: ANN001
    bundle = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=60)
    assert bundle.token_count <= 60
    assert any(o.reason == "budget" for o in bundle.omissions)
    assert set(bundle.mandatory_span_ids) <= {span.span_id for span in bundle.spans}


def test_bundles_are_deterministic(project) -> None:  # noqa: ANN001
    first = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    second = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    assert first.digest() == second.digest()
    # The second build reuses the index and must still report the quarantine.
    assert any(o.reason == "evaluation_isolation" for o in second.omissions)


def test_index_is_bound_to_its_snapshot(project) -> None:  # noqa: ANN001
    controller, human = project["controller"], project["human"]
    old = controller.store.snapshot(project["snapshot_id"])
    (project["source"] / "src" / "packet_reader" / "new_module.py").write_text("def brand_new_function():\n    pass\n")
    new_id = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                              project_id=project["project_id"]), human).result["snapshot_id"]
    new = controller.store.snapshot(new_id)
    index, blobs = project["index"], controller.blobs
    index.build(load_manifest(blobs, old.manifest_digest), old.manifest_digest, blobs)
    index.build(load_manifest(blobs, new.manifest_digest), new.manifest_digest, blobs)
    assert [r.name for r in index.search(new.manifest_digest, "brand_new_function")][:1] == ["brand_new_function"]
    assert not any(r.name == "brand_new_function" for r in index.search(old.manifest_digest, "brand_new_function"))


def test_hostile_queries_and_files_do_not_break_search(project) -> None:  # noqa: ANN001
    snapshot = project["controller"].store.snapshot(project["snapshot_id"])
    project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    for query in ['packet" OR 1=1 --', "NEAR(a b)", "*", "'; DROP TABLE chunks; --", ""]:
        project["index"].search(snapshot.manifest_digest, query)  # must not raise
    # deep.py could not be parsed, but its text is still searchable
    assert any(ref.path == "deep.py" for ref in project["index"].search(snapshot.manifest_digest, "deep"))


# -------------------------------------------------------------- citations --


def test_citation_mechanics_versus_semantic_support_int_t06(project) -> None:  # noqa: ANN001
    bundle = project["builder"].build(project["task_id"], project["snapshot_id"], budget_tokens=4000)
    objective = bundle.spans[0]
    checks = check_claims([
        # A real, exact quote that does not support the claim at all:
        ClaimSupport(statement="The parser rejects truncated payloads", span_id=objective.span_id,
                     quote="Fix parse_packets"),
        ClaimSupport(statement="x", span_id=objective.span_id, quote="words that are not there"),
        ClaimSupport(statement="y", span_id="s999", quote="anything"),
        ClaimSupport(statement="z", span_id=None),
    ], bundle)
    assert [c.mechanical for c in checks] == ["ok", "failed", "failed", "inconclusive"]
    assert all(c.semantic_support == "unresolved" for c in checks)  # relevance is never assumed


# -------------------------------------------------------------- D05 facts --


def test_project_facts_name_their_evidence(project) -> None:  # noqa: ANN001
    controller = project["controller"]
    snapshot = controller.store.snapshot(project["snapshot_id"])
    manifest = load_manifest(controller.blobs, snapshot.manifest_digest)
    facts = detect_facts(manifest, controller.blobs)
    paths = {entry.path for entry in manifest.entries}
    for fact in facts:
        assert set(fact.evidence) <= paths, fact
    assert fact_value(facts, "tests.framework") == "pytest"
    assert fact_value(facts, "entry_points.console_scripts") == ["packet-reader"]
    assert fact_value(facts, "data.migrations") is True
    assert fact_value(facts, "ci.configured") is True
    assert fact_value(facts, "dependencies.lockfiles") == ["uv.lock"]
    assert fact_value(facts, "release.feature_flags") is None  # no flag library: no flag checklist items
    assert fact_value(facts, "language.python") >= 3


def test_an_index_built_with_fewer_protections_is_rebuilt_not_reused(project) -> None:  # noqa: ANN001
    """An index made without the suite's digest must not serve a reader that hides it (visibility.py)."""
    controller, index = project["controller"], project["index"]
    digest = controller.store.snapshot(project["snapshot_id"]).manifest_digest
    manifest = load_manifest(controller.blobs, digest)
    open_build = index.build(manifest, digest, controller.blobs, protected_digests=set())
    assert "notes/answers.json" not in open_build["evaluation_isolation"]
    assert "notes/answers.json" in [r.path for r in index.search(digest, "SECRET ANSWER")]
    suites = {row[0] for row in controller.store.conn.execute(
        "SELECT digest FROM artifacts WHERE artifact_kind = 'evaluator_suite'")}
    strict_build = index.build(manifest, digest, controller.blobs, protected_digests=suites)
    assert "notes/answers.json" in strict_build["evaluation_isolation"]
    assert "notes/answers.json" not in [r.path for r in index.search(digest, "SECRET ANSWER")]


def test_ada_and_fortran_are_searchable_and_the_project_file_is_not(project) -> None:  # noqa: ANN001
    source, controller = project["source"], project["controller"]
    (source / "src" / "stall.adb").write_text("procedure Stall_Warning is\nbegin\n   null;\nend Stall_Warning;\n")
    (source / "src" / "stall.ads").write_text("package Stall_Limits is\n   Critical_Angle : constant := 18;\nend;\n")
    (source / "src" / "lift.f90").write_text("subroutine lift_coefficient(alpha)\n  real :: alpha\nend subroutine\n")
    (source / "forge.toml").write_text('[[tests]]\nid = "t"\nargs = ["1"]\nstdout = "SECRETEXPECTED\\n"\n')
    snapshot_id = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                   project_id=project["project_id"]), project["human"]).result["snapshot_id"]
    digest = controller.store.snapshot(snapshot_id).manifest_digest
    quarantined = project["index"].build(load_manifest(controller.blobs, digest), digest, controller.blobs)
    assert "forge.toml" in quarantined["evaluation_isolation"]
    for query, path in (("Stall_Warning", "src/stall.adb"), ("Critical_Angle", "src/stall.ads"),
                        ("lift_coefficient", "src/lift.f90")):
        assert path in [r.path for r in project["index"].search(digest, query)], query
    assert project["index"].search(digest, "SECRETEXPECTED") == []


def test_the_index_quarantines_evaluator_folders_in_any_letter_case(project) -> None:  # noqa: ANN001
    source, controller = project["source"], project["controller"]
    (source / "Hidden_Tests").mkdir()
    (source / "Hidden_Tests" / "case.txt").write_text("SECRETCASEVALUE\n")
    snapshot_id = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                   project_id=project["project_id"]), project["human"]).result["snapshot_id"]
    digest = controller.store.snapshot(snapshot_id).manifest_digest
    quarantined = project["index"].build(load_manifest(controller.blobs, digest), digest, controller.blobs)
    assert "Hidden_Tests/case.txt" in quarantined["evaluation_isolation"]
    assert project["index"].search(digest, "SECRETCASEVALUE") == []


def test_secrets_never_enter_the_index_or_the_context(project) -> None:  # noqa: ANN001
    source, controller = project["source"], project["controller"]
    (source / "src" / "packet_reader" / "settings.yaml").write_text(
        "database:\n  host: packets.internal\n  password: CorrectHorseBatteryStaple\n")
    (source / "src" / "packet_reader" / "upload.py").write_text(
        'def parse_packets_upload():\n    return "AKIAABCDEFGHIJKLMNOP"\n')
    snapshot_id = controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                   project_id=project["project_id"]), project["human"]).result["snapshot_id"]
    digest = controller.store.snapshot(snapshot_id).manifest_digest
    project["index"].build(load_manifest(controller.blobs, digest), digest, controller.blobs)
    assert project["index"].search(digest, "CorrectHorseBatteryStaple") == []
    assert project["index"].search(digest, "AKIAABCDEFGHIJKLMNOP") == []
    assert "src/packet_reader/settings.yaml" in [r.path for r in project["index"].search(digest, "packets internal")]
    bundle = project["builder"].build(project["task_id"], snapshot_id, budget_tokens=4000,
                                      query="parse_packets_upload database password")
    rendered = bundle.render()
    assert "AKIAABCDEFGHIJKLMNOP" not in rendered and "CorrectHorseBatteryStaple" not in rendered

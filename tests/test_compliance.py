"""Compliance: catalogs, domain profiles, evidence producers, gate, reports, attestations, imports.

The NIST catalogs are the official OSCAL files compacted; the mapping tests
fail if any mapped control label is missing or withdrawn, so a citation in a
report always names a real, current control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.compliance import producers
from forge.compliance.checks import CHECKS
from forge.compliance.oscal import import_catalog, import_profile_ids
from forge.compliance.profiles import (
    BUNDLED_CATALOGS,
    MAPPINGS,
    PROFILES,
    STANDARDS,
    bundled_baselines,
    bundled_catalog,
    resolve,
)
from forge.compliance.report import render_markdown
from forge.compliance.service import ComplianceService, parse_csv_catalog, parse_mapping_csv
from forge.contracts.errors import ForgeError
from forge.contracts.ids import new_id
from forge.engine.completion import evaluate_completion
from tests.conftest import BUDGET, command

# ------------------------------------------------------------------ catalogs


def test_bundled_catalogs_name_their_source_and_digest() -> None:
    for catalog_id in BUNDLED_CATALOGS:
        catalog = bundled_catalog(catalog_id)
        assert catalog.source_url.startswith("https://raw.githubusercontent.com/usnistgov/oscal-content/")
        assert catalog.source_sha256.startswith("sha256:") and len(catalog.source_sha256) == 71
        assert "Public domain" in catalog.license and catalog.controls
    assert len(bundled_catalog("nist-800-53r5").controls) == 1196
    labels = bundled_catalog("nist-800-53r5").by_label()
    assert labels["SA-11(1)"].title == "Static Code Analysis" and labels["SA-11(1)"].parent == "sa-11"


def test_every_mapped_control_exists_and_is_current() -> None:
    for catalog_id, mapping in MAPPINGS.items():
        labels = bundled_catalog(catalog_id).by_label()
        for label, checks in mapping.items():
            assert label in labels, f"{catalog_id} has no {label}"
            assert not labels[label].withdrawn, f"{catalog_id} {label} is withdrawn"
            assert checks and set(checks) <= set(CHECKS)


def test_profiles_and_standards_refer_only_to_real_checks_and_standards() -> None:
    for profile in PROFILES.values():
        assert set(profile.gate_checks) <= set(CHECKS)
        assert set(profile.standards) <= set(STANDARDS)
        assert all(use.catalog_id in BUNDLED_CATALOGS for use in profile.catalogs)
    for standard in STANDARDS.values():
        assert standard.access in ("licensed", "public", "registration")
        assert all(set(checks) <= set(CHECKS) for checks in standard.topics.values())


def test_baselines_select_existing_controls() -> None:
    ids = bundled_catalog("nist-800-53r5").by_id()
    sizes = {name: len(info["control_ids"]) for name, info in bundled_baselines().items()}
    assert sizes == {"low": 149, "moderate": 287, "high": 370, "privacy": 96}
    for info in bundled_baselines().values():
        assert all(control_id in ids for control_id in info["control_ids"])


def test_oscal_import_keeps_labels_groups_parents_and_withdrawal() -> None:
    document = {"catalog": {"metadata": {"title": "Mini", "version": "1", "last-modified": "2026-01-01"}, "groups": [
        {"id": "xx", "title": "Example Family", "controls": [
            {"id": "xx-1", "title": "First", "props": [{"name": "label", "value": "XX-1"},
                                                       {"name": "label", "value": "XX-01", "class": "zero-padded"}],
             "controls": [{"id": "xx-1.2", "title": "Enhancement", "props": [{"name": "label", "value": "XX-1(2)"}]}]},
            {"id": "xx-2", "title": "Old", "props": [{"name": "label", "value": "XX-2"},
                                                     {"name": "status", "value": "withdrawn"}]}]}]}}
    catalog = import_catalog(json.dumps(document).encode(), catalog_id="mini", source_url="file:mini.json",
                             license_note="test")
    labels = catalog.by_label()
    assert set(labels) == {"XX-1", "XX-1(2)", "XX-2"}
    assert labels["XX-1(2)"].parent == "xx-1" and labels["XX-1"].group == "Example Family"
    assert labels["XX-2"].withdrawn and not labels["XX-1"].withdrawn
    with pytest.raises(ForgeError, match="not an OSCAL catalog"):
        import_catalog(b'{"profile": {}}', catalog_id="x", source_url="", license_note="")
    profile = {"profile": {"imports": [{"include-controls": [{"with-ids": ["xx-2", "xx-1", "xx-1"]}]}]}}
    assert import_profile_ids(json.dumps(profile).encode()) == ["xx-1", "xx-2"]


# ------------------------------------------------------------------ profiles


def test_avionics_levels_set_the_structural_coverage_criterion() -> None:
    assert resolve("avionics").check_options["structural_coverage"] == {"coverage": "mcdc", "minimum_percent": 100.0}
    assert resolve("avionics", {"level": "B"}).check_options["structural_coverage"]["coverage"] == "decision"
    assert resolve("avionics", {"level": "C"}).check_options["structural_coverage"]["coverage"] == "statement"
    level_d = resolve("avionics", {"level": "D"})
    assert "structural_coverage" not in level_d.gate_checks and "requirements_traceability" in level_d.gate_checks
    assert resolve("avionics", {"level": "E"}).gate_checks == ["tests_pass"]


def test_options_are_validated_never_guessed() -> None:
    assert resolve("federal", {"baseline": "high"}).catalogs[0].baseline == "high"
    assert resolve("federal").catalogs[0].baseline == "moderate"
    with pytest.raises(ForgeError, match="must be one of"):
        resolve("avionics", {"level": "Z"})
    with pytest.raises(ForgeError, match="has no option"):
        resolve("avionics", {"baseline": "high"})
    with pytest.raises(ForgeError, match="unknown compliance domain"):
        resolve("aerospace-ish")
    medical = resolve("medical", {"coverage": "decision"})
    assert medical.check_options["structural_coverage"]["coverage"] == "decision"


# ----------------------------------------------------------------- producers


def test_inventory_reads_lock_files_and_manifests_into_cyclonedx() -> None:
    files = {
        "requirements.txt": b"requests==2.19.0\nFlask>=2  # not pinned\n-r other.txt\ngit+https://x/y\n",
        "web/package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
            "": {"name": "web"}, "node_modules/lodash": {"version": "4.17.20"},
            "node_modules/@scope/pkg": {"version": "1.0.0"}}}).encode(),
        "web/package.json": json.dumps({"dependencies": {"lodash": "^4"}}).encode(),
        "pom.xml": b'<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency>'
                   b"<groupId>org.example</groupId><artifactId>lib</artifactId><version>1.2.3</version>"
                   b"</dependency></dependencies></project>",
        "go.mod": b"module x\n\nrequire (\n\tgolang.org/x/text v0.3.7 // indirect\n)\nrequire github.com/a/b v1.0.0\n",
        "Cargo.lock": b'[[package]]\nname = "serde"\nversion = "1.0.1"\n',
        "Gemfile.lock": b"GEM\n  specs:\n    rack (2.2.3)\n",
        "app.csproj": b'<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" Version="12.0.1"/>'
                      b"</ItemGroup></Project>",
        "broken/pyproject.toml": b"[project\n",
    }
    digests = {path: "sha256:" + "a" * 64 for path in files}
    document, summary = producers.inventory(files, digests, subject_name="p", subject_version="snapshot_x",
                                            timestamp="2026-01-01T00:00:00Z", tool_version="0")
    assert document["bomFormat"] == "CycloneDX" and document["specVersion"] == "1.6"
    purls = {c["purl"] for c in document["components"] if c["type"] == "library"}
    assert {"pkg:pypi/requests@2.19.0", "pkg:pypi/flask", "pkg:npm/lodash@4.17.20", "pkg:npm/%40scope/pkg@1.0.0",
            "pkg:maven/org.example/lib@1.2.3", "pkg:golang/golang.org/x/text@v0.3.7", "pkg:golang/github.com/a/b@v1.0.0",
            "pkg:cargo/serde@1.0.1", "pkg:gem/rack@2.2.3", "pkg:nuget/Newtonsoft.Json@12.0.1"} == purls
    assert "pkg:npm/lodash" not in purls                     # package.json is superseded by its lock file
    assert summary["unpinned"] == ["PyPI:flask"]
    assert summary["files"] == len(files) and any("broken/pyproject.toml" in p for p in summary["problems"])
    files_listed = [c for c in document["components"] if c["type"] == "file"]
    assert files_listed[0]["hashes"] == [{"alg": "SHA-256", "content": "a" * 64}]
    again, _ = producers.inventory(files, digests, subject_name="p", subject_version="snapshot_x",
                                   timestamp="2026-01-01T00:00:00Z", tool_version="0")
    assert again == document                                 # deterministic


def test_secret_scan_locates_without_copying_the_secret() -> None:
    findings, scanned = producers.scan_secrets({
        "config.py": b"user = 'a'\npassword = 'hunter2hunter2'\n",
        "keys.txt": b"AKIAABCDEFGHIJKLMNOP\n",
        "image.bin": b"\x00\x01password=hunter2hunter2",
        "clean.py": b"print('hello')\n"})
    assert scanned == 3
    assert [(f["path"], f["line"]) for f in findings] == [("config.py", 2), ("keys.txt", 1)]
    assert "hunter2" not in json.dumps(findings) and "AKIA" not in json.dumps(findings)


def test_trace_finds_untested_and_unimplemented_requirements() -> None:
    files = {"stall.adb": b"-- Implements: HLR-1, HLR-2\n", "main.adb": b"-- Implements: HLR-3\n",
             "tests/stall_test.py": b"# Verifies: HLR-1\n# Verifies: HLR-3\n", "helper.c": b"int x;\n"}
    tagged = producers.trace(files)
    assert tagged["requirements"] == ["HLR-1", "HLR-2", "HLR-3"] and tagged["requirements_source"] == "tags"
    assert tagged["gaps"] == [{"requirement": "HLR-2", "problem": "no test is tagged as verifying it"}]
    assert tagged["implemented_by"]["HLR-3"] == ["main.adb:1"] and tagged["untraced_source_files"] == ["helper.c"]
    listed = producers.trace(files, producers.read_requirements(b"id,text\nHLR-1,stall\nHLR-3,x\nHLR-4,y\n"))
    problems = {(g["requirement"], g["problem"]) for g in listed["gaps"]}
    assert ("HLR-4", "no code is tagged as implementing it") in problems
    assert ("HLR-2", "tagged in the code but not in the requirements list") in problems


@pytest.mark.parametrize(("smaller", "larger"), [("1.0a1", "1.0"), ("1.0", "1.0.post1"), ("1.9", "1.10"),
                                                 ("1.0.0-rc.1", "1.0.0"), ("v0.3.6", "v0.3.7"), ("1.0.dev0", "1.0a1")])
def test_version_ordering(smaller: str, larger: str) -> None:
    assert producers.version_key(smaller) < producers.version_key(larger)
    assert producers.version_key("1.0") == producers.version_key("1.0.0")


def test_osv_matching_uses_listed_versions_and_ranges() -> None:
    records = [
        {"id": "TEST-0001", "summary": "range", "affected": [{"package": {"ecosystem": "PyPI", "name": "Requests"},
                                                             "ranges": [{"type": "ECOSYSTEM", "events": [
                                                                 {"introduced": "0"}, {"fixed": "2.20.0"}]}]}]},
        {"id": "TEST-0002", "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}, "versions": ["4.17.20"]}]},
        {"id": "TEST-0003", "affected": [{"package": {"ecosystem": "crates.io", "name": "serde"}, "ranges": [
            {"type": "SEMVER", "events": [{"introduced": "1.0.0"}, {"last_affected": "1.0.0"}]}]}]},
    ]
    components = [{"ecosystem": "PyPI", "name": "requests", "version": "2.19.0"},
                  {"ecosystem": "PyPI", "name": "requests2", "version": "2.19.0"},
                  {"ecosystem": "npm", "name": "lodash", "version": "4.17.20"},
                  {"ecosystem": "crates.io", "name": "serde", "version": "1.0.1"},
                  {"ecosystem": "PyPI", "name": "flask", "version": None}]
    result = producers.match_vulnerabilities(components, records)
    assert [(a["id"], a["matched_by"]) for a in result["affected"]] == [("TEST-0001", "ecosystem range"),
                                                                       ("TEST-0002", "listed version")]
    assert result["unchecked"] == ["PyPI:flask (no exact version)"]
    fixed = producers.match_vulnerabilities([{"ecosystem": "PyPI", "name": "requests", "version": "2.20.0"}], records)
    assert fixed["affected"] == []


# ------------------------------------------------------------------- service


@pytest.fixture
def project(controller, human, trust, tmp_path):  # noqa: ANN001, ANN201
    source = tmp_path / "source"
    (source / "tests").mkdir(parents=True)
    (source / "stall.py").write_text("# Implements: HLR-1\ndef warn(aoa):\n    return aoa > 15\n")
    (source / "tests" / "test_stall.py").write_text("# Verifies: HLR-1\n")
    (source / "requirements.txt").write_text("requests==2.19.0\n")
    (source / "reqs.csv").write_text("id\nHLR-1\nHLR-2\n")
    project_id = controller.apply_command(command("project.create", {
        "name": "Stall warning", "objective": "o", "source_roots": ["main"]}), human).project_id
    trust.approve_source_root(project_id, "main", source, human)

    def capture() -> str:
        return controller.apply_command(command("workspace.capture", {"source_root": "main"},
                                                project_id=project_id), human).result["snapshot_id"]

    def send(method: str, params: dict, actor=None):  # noqa: ANN001, ANN202
        return controller.apply_command(command(method, params, project_id=project_id), actor or human)

    return {"controller": controller, "human": human, "source": source, "capture": capture, "send": send,
            "service": ComplianceService(controller), "snapshot": capture(), "project_id": project_id}


def test_only_a_person_sets_the_domain(project, model_actor) -> None:  # noqa: ANN001
    service = project["service"]
    with pytest.raises(ForgeError, match="only a trusted person"):
        service.set_domain(model_actor, "avionics", {"level": "A"})
    with pytest.raises(ForgeError, match="only a trusted person chooses"):   # not even by calling the command directly
        project["send"]("criterion.create", {"subject": "off", "assessment_method": "compliance_gate",
                                             "parameters": {"profile_id": "none", "options": {}, "catalogs": []}},
                        model_actor)
    with pytest.raises(ForgeError, match="must be one of"):
        project["send"]("criterion.create", {"subject": "bad", "assessment_method": "compliance_gate",
                                             "parameters": {"profile_id": "avionics", "options": {"level": "Q"},
                                                            "catalogs": []}})
    assert service.active_gate() is None


def test_the_gate_kicks_in_for_new_execute_tasks(project) -> None:  # noqa: ANN001
    service, send = project["service"], project["send"]

    def task(task_type: str) -> list[dict]:
        task_id = send("task.create", {"task_type": task_type, "title": "t", "budget": BUDGET}).result["task_id"]
        return project["controller"].store.task(task_id).criterion_refs

    assert task("execute") == []
    gate = service.set_domain(project["human"], "avionics", {"level": "B"})
    refs = task("execute")
    assert [r.id for r in refs] == [gate["criterion_id"]]
    assert task("prepare") == []                              # planning work is not gated
    planned = send("plan.apply", {"base_plan_revision": 0, "summary": "p", "add_tasks": [
        {"key": "build", "task_type": "execute", "title": "build it", "budget": BUDGET}], "add_dependencies": []})
    built = project["controller"].store.task(planned.result["task_ids"]["build"])
    assert [r.id for r in built.criterion_refs] == [gate["criterion_id"]]
    service.set_domain(project["human"], "none")
    assert task("execute") == []                              # switched off by the person
    assert project["controller"].store.verify_projections() == []


def _record(project, observation_type: str, observation: dict, snapshot: str) -> str:  # noqa: ANN001
    return project["send"]("evidence.record_execution", {
        "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": snapshot, "tool_ref": "test@1",
        "environment_digest": "sha256:" + "0" * 64, "observation_type": observation_type, "observation": observation,
        "artifact_refs": []}, project["controller"].system_actor).result["evidence_id"]


def test_check_produces_evidence_assesses_the_gate_and_completion_follows_it(project) -> None:  # noqa: ANN001
    service, store, snapshot = project["service"], project["controller"].store, project["snapshot"]
    gate = service.set_domain(project["human"], "general")
    task_id = project["send"]("task.create", {"task_type": "execute", "title": "t", "budget": BUDGET}).result["task_id"]
    task = store.task(task_id)

    first = service.check(snapshot, requirements_path="reqs.csv")
    assert set(first["produced"]["evidence"]) == {"software_inventory", "secrets_scan", "traceability"}
    assert first["gate"]["result"] == "inconclusive"         # tests and static analysis have not run yet
    report = first["report"]
    assert report["checks"]["no_embedded_secrets"]["status"] == "met"
    assert report["checks"]["tests_pass"]["status"] == "not_run"
    assert report["checks"]["requirements_traceability"]["status"] == "not_met"     # HLR-2 is untraced

    def completion() -> object:
        return evaluate_completion(task, snapshot_id=snapshot, criteria={gate["criterion_id"]: store.criterion(
            gate["criterion_id"])}, assessments=store.assessments(criterion_id=gate["criterion_id"],
                                                                 snapshot_id=snapshot),
            decisions=store.decisions(), acknowledged_by_trusted_actor=True, now=project["controller"].clock.now())

    assert not completion().eligible

    tests_criterion = project["send"]("criterion.create", {"subject": "suite", "assessment_method": "test_suite"})
    run = _record(project, "test_results", {"passed": 3, "failed": 0}, snapshot)
    project["send"]("assessment.record", {
        "criterion_ref": {"id": tests_criterion.result["criterion_id"], "revision": 1}, "snapshot_id": snapshot,
        "evidence_refs": [run], "assessment_result": "pass", "assessor_ref": "test"},
        project["controller"].system_actor)
    _record(project, "analyzer_findings", {"status": "ok", "finding_count": 0}, snapshot)
    second = service.check(snapshot)
    assert second["gate"]["result"] == "pass" and second["report"]["gate_status"] == "pass"
    assert completion().eligible
    assert store.verify_projections() == []


def test_report_cites_controls_from_evidence_and_attestations(project) -> None:  # noqa: ANN001
    service, snapshot = project["service"], project["snapshot"]
    (project["source"] / "settings.py").write_text("password = 'hunter2hunter2'\n")
    leaky = project["capture"]()
    service.produce(leaky)
    report = service.report(leaky, profile_id="federal", options={"baseline": "moderate"})
    nist = report.catalogs[0]
    assert nist.catalog_id == "nist-800-53r5" and len(nist.controls) == 287
    by_label = {c.label: c for c in nist.controls}
    assert by_label["CM-8"].status == "met" and by_label["CM-8"].evidence
    assert by_label["SA-11"].status == "incomplete"          # no tests or analysis have run yet
    assert by_label["AC-1"].status == "needs_attestation"
    assert "IA-5(7)" not in by_label                         # optional enhancement, outside the moderate baseline
    ssdf = {c.label: c for c in report.catalogs[1].controls}
    assert ssdf["PW.5"].status == "gap" and "possible secrets" in " ".join(ssdf["PW.5"].detail)

    with pytest.raises(ForgeError, match="no current control"):
        service.attest(project["human"], snapshot_id=leaky, catalog_id="nist-800-53r5", labels=["AC-999"],
                       statement="x")
    service.attest(project["human"], snapshot_id=snapshot, catalog_id="nist-800-53r5", labels=["AC-1", "AT-1"],
                   statement="Access control policy v3 approved by the CISO on 2026-10-01.")
    service.attest(project["human"], snapshot_id=snapshot, catalog_id="nist-800-53r5", labels=["IR-1"],
                   statement="Incident response plan reviewed for this release.", scope="snapshot")
    later = {c.label: c for c in service.report(leaky, profile_id="federal").catalogs[0].controls}
    assert later["AC-1"].status == "attested"                # process attestations carry to later snapshots
    assert later["AC-1"].attestation["reviewer_id"] == project["human"].actor_id
    assert later["IR-1"].status == "needs_attestation"       # a snapshot attestation does not
    service.attest(project["human"], snapshot_id=leaky, catalog_id="nist-800-53r5", labels=["AC-1"], statement="",
                   withdraw=True)
    assert {c.label: c for c in service.report(leaky, profile_id="federal").catalogs[0].controls}["AC-1"].status \
        == "needs_attestation"

    text = render_markdown(service.report(leaky, profile_id="federal"))
    assert "## Gate: FAIL" in text and bundled_catalog("nist-800-53r5").source_sha256 in text
    assert "| PW.5 |" in text and "Needs your attestation" in text
    assert "produce evidence for" in text                    # next steps say what to run


def test_model_actors_cannot_attest_or_import(project, model_actor) -> None:  # noqa: ANN001
    service = project["service"]
    with pytest.raises(ForgeError, match="only a trusted person"):
        service.attest(model_actor, snapshot_id=None, catalog_id="nist-800-53r5", labels=["AC-1"], statement="ok")
    with pytest.raises(ForgeError, match="only a trusted person"):
        service.import_vulnerabilities(model_actor, [{"id": "TEST-1", "affected": []}], source="x")


def test_a_licensed_standard_is_imported_mapped_and_cited(project) -> None:  # noqa: ANN001
    service, human = project["service"], project["human"]
    raw = b"id,title,group\nA-7.1,Test procedures are correct,Verification\nA-7.5,Structural coverage,Verification\n"
    catalog = parse_csv_catalog(raw, catalog_id="do-178c", title="DO-178C (my copy)", version="2011",
                                source="file:do178c-objectives.csv", license_note="licensed copy")
    mappings = parse_mapping_csv(b"label,checks\nA-7.5,structural_coverage\nA-7.1,tests_pass;requirements_traceability\n")
    with pytest.raises(ForgeError, match="mapping names unknown"):
        service.import_catalog(human, catalog, {"A-7.5": ["certify_it"]})
    with pytest.raises(ForgeError, match="new short name"):
        service.import_catalog(human, {**catalog, "catalog_id": "nist-800-53r5"}, {})
    result = service.import_catalog(human, catalog, mappings)
    assert (result["catalog_id"], result["controls"], result["mapped"]) == ("do-178c", 2, 2)
    assert project["controller"].store.artifact(result["artifact_id"]).sensitivity == "sensitive"
    with pytest.raises(ForgeError, match="import it first"):
        service.set_domain(human, "avionics", catalogs=["do-330"])
    service.set_domain(human, "avionics", {"level": "A"}, catalogs=["do-178c"])
    report = service.report(project["snapshot"])
    section = next(s for s in report.catalogs if s.catalog_id == "do-178c")
    statuses = {c.label: c.status for c in section.controls}
    assert statuses == {"A-7.1": "incomplete", "A-7.5": "incomplete"}      # no tests, trace or coverage yet
    assert section.source_sha256.startswith("sha256:") and section.license == "licensed copy"
    assert report.profile.check_options["structural_coverage"]["coverage"] == "mcdc"


def test_imported_vulnerabilities_feed_the_scan(project) -> None:  # noqa: ANN001
    service = project["service"]
    service.import_vulnerabilities(project["human"], [
        {"id": "TEST-0001", "summary": "test advisory", "affected": [{"package": {"ecosystem": "PyPI", "name": "requests"},
                                                                     "ranges": [{"type": "ECOSYSTEM", "events": [
                                                                         {"introduced": "0"}, {"fixed": "2.20.0"}]}]}]},
        {"bad": "record"}], source="osv-test")
    produced = service.produce(project["snapshot"])
    assert "vulnerability_scan" in produced["evidence"] and produced["vulnerability_database"].startswith("osv: 1 ")
    result = CHECKS["vulnerability_scan"].run(project["controller"].store, project["snapshot"], {})
    assert result.status == "not_met" and "TEST-0001" in result.detail


# ------------------------------------------------------------------ CLI / API


@pytest.mark.skipif(__import__("os").name == "nt", reason="the API's connection file is POSIX-private only")
def test_cli_end_to_end(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    from forge.api.client import ForgeClient
    from forge.api.launcher import launch
    from forge.client_cli import main as cli
    from forge.contracts.clock import FixedClock

    root, source = tmp_path / "forge-root", tmp_path / "src"
    source.mkdir()
    (source / "stall.adb").write_text("-- Implements: HLR-1\n")
    (source / "stall_test.py").write_text("# Verifies: HLR-1\n")
    launched = launch(root, clock=FixedClock())
    try:
        client = ForgeClient(launched.server.url, launched.token)
        client.call("project.create", {"idempotency_key": "c", "params": {"name": "Stall", "objective": "o",
                                                                         "source_roots": ["main"]}})
        client.call("authority.approve_source_root", {"label": "main", "path": str(source)})
        assert cli(["capture", "--root", str(root)]) == 0
        assert cli(["comply", "domains", "--root", str(root)]) == 0
        assert "avionics" in capsys.readouterr().out
        assert cli(["comply", "set", "avionics", "--level", "A", "--root", str(root)]) == 0
        assert "structural coverage required: mcdc at 100%" in capsys.readouterr().out
        assert cli(["comply", "set", "avionics", "--level", "Q", "--root", str(root)]) == 1
        assert "must be one of" in capsys.readouterr().err
        report_path = tmp_path / "report.md"
        assert cli(["comply", "check", "--root", str(root), "--output", str(report_path)]) == 0
        out = capsys.readouterr().out
        assert "gate INCOMPLETE" in out and "next:" in out
        assert "Airborne software (DO-178C)" in report_path.read_text()
        attestations = tmp_path / "attest.csv"
        attestations.write_text("catalog,label,statement\nnist-800-218,PO.1,Security requirements are defined.\n")
        assert cli(["comply", "attest", "--file", str(attestations), "--root", str(root)]) == 0
        assert "1 attestation(s)" in capsys.readouterr().out
        assert cli(["comply", "report", "--json", "--root", str(root)]) == 0
        report = json.loads(capsys.readouterr().out)
        ssdf = next(s for s in report["catalogs"] if s["catalog_id"] == "nist-800-218")
        assert next(c for c in ssdf["controls"] if c["label"] == "PO.1")["status"] == "attested"
        osv = tmp_path / "osv"
        osv.mkdir()
        (osv / "TEST-9.json").write_text(json.dumps({"id": "TEST-9", "affected": []}))
        assert cli(["comply", "import-vulns", str(osv), "--root", str(root)]) == 0
        assert "1 vulnerability records" in capsys.readouterr().out
    finally:
        launched.close()


def test_a_gate_with_only_test_assessments_still_cites_evidence(project) -> None:  # noqa: ANN001
    service, snapshot = project["service"], project["snapshot"]
    service.set_domain(project["human"], "avionics", {"level": "E"})
    criterion = project["send"]("criterion.create", {"subject": "suite", "assessment_method": "test_suite"})
    run = _record(project, "test_results", {"passed": 1}, snapshot)
    project["send"]("assessment.record", {
        "criterion_ref": {"id": criterion.result["criterion_id"], "revision": 1}, "snapshot_id": snapshot,
        "evidence_refs": [run], "assessment_result": "pass", "assessor_ref": "test"}, project["controller"].system_actor)
    outcome = service.assess_gate(snapshot)
    assert outcome["result"] == "pass"
    assessment = project["controller"].store.assessment(outcome["assessment_id"])
    cited = [project["controller"].store.evidence(e) for e in assessment.evidence_refs]
    assert cited[0].observation_type == "compliance_gate_evaluation"
    assert cited[0].observation["checks"]["tests_pass"]["status"] == "met"


def test_no_requirements_is_not_evidence_of_traceability(project) -> None:  # noqa: ANN001
    for path in ("stall.py", "tests/test_stall.py"):
        (project["source"] / path).write_text("x = 1\n")
    untagged = project["capture"]()
    project["service"].produce(untagged)
    result = CHECKS["requirements_traceability"].run(project["controller"].store, untagged, {})
    assert result.status == "not_run" and "Implements: ID" in result.detail

"""Compliance service: domain gate, evidence producers, control-by-control reports, attestations, imports.

How a person uses it (each step is one CLI command; see `forge comply --help`):

1. `forge comply set avionics --level A` records the domain as a gate
   criterion. From then on, every new execute task carries the gate, so it
   cannot complete until the gate's checks are met on the snapshot (or a
   person waives them with a recorded decision).
2. `forge comply check` produces the evidence FORGE can produce without
   running project code (SBOM, secrets scan, requirements trace, known
   vulnerabilities), evaluates every check, records the gate's assessment and
   prints where the project stands.
3. `forge comply report` writes the control-by-control report for the
   domain's catalogs, citing each catalog's source and digest.
4. `forge comply attest` records a person's statement for the controls FORGE
   cannot check (policies, training, incident response...). Only a trusted
   person can attest; the attestation names them.

Imported catalogs (a licensed standard converted to OSCAL or listed in CSV)
and vulnerability databases are stored in the project's artifact store and
never leave the machine (catalogs are marked sensitive: they are licensed
text).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Literal

from pydantic import Field

from forge import __version__
from forge.compliance import producers
from forge.compliance.checks import CHECKS, CheckResult
from forge.compliance.oscal import CompactCatalog, Control
from forge.compliance.profiles import (
    BUNDLED_CATALOGS,
    MAPPINGS,
    PROFILES,
    ResolvedProfile,
    bundled_baselines,
    bundled_catalog,
    resolve,
)
from forge.contracts.base import StrictModel
from forge.contracts.clock import format_timestamp
from forge.contracts.commands import ActorContext
from forge.contracts.digests import canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.workspace.snapshot import load_manifest

GATE_METHOD = "compliance_gate"
ATTESTATION = "compliance_attestation"
TOOL_REF = f"forge-compliance@{__version__}"
MAX_TOTAL_SCAN_BYTES = 1024 * 1024 * 1024
MAX_IMPORT_BYTES = 64 * 1024 * 1024
CATALOG_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
ControlStatus = Literal["met", "gap", "incomplete", "attested", "needs_attestation"]


class ControlResult(StrictModel):
    catalog_id: str
    label: str
    title: str
    group: str
    status: ControlStatus
    checks: list[str] = Field(default_factory=list)
    detail: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    attestation: dict[str, Any] | None = None


class CatalogSection(StrictModel):
    catalog_id: str
    title: str
    version: str
    last_modified: str
    source_url: str
    source_sha256: str
    license: str
    selection: str
    counts: dict[str, int]
    controls: list[ControlResult]


class ComplianceReport(StrictModel):
    snapshot_id: str
    generated_at: str
    profile: ResolvedProfile
    gate_status: Literal["pass", "fail", "incomplete", "none"]
    checks: dict[str, dict[str, Any]]
    catalogs: list[CatalogSection]
    standards: list[dict[str, Any]]
    limitations: list[str]


class ComplianceService:
    def __init__(self, controller) -> None:  # noqa: ANN001 - forge.controller.service.Controller
        self.controller = controller
        self.store = controller.store
        self.blobs = controller.blobs

    # ------------------------------------------------------------ commands

    def _send(self, method: str, params: dict[str, Any], actor: ActorContext | None = None) -> Any:
        project = self.store.project()
        if project is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no project: run `forge init` first")
        return self.controller.apply_command({"schema_version": "1.0", "method": method,
                                              "project_id": project.project_id, "idempotency_key": new_id("request"),
                                              "params": params}, actor or self.controller.system_actor).result

    def _artifact(self, data: bytes, kind: str, sensitivity: str = "project") -> str:
        blob = self.blobs.put_bytes(data)
        return self._send("artifact.register", {"digest": blob.digest, "size": blob.size,
                                                "content_type": "application/json", "artifact_kind": kind,
                                                "sensitivity": sensitivity})["artifact_id"]

    def _record(self, snapshot_id: str, observation_type: str, observation: dict[str, Any],
                artifact_refs: list[str] | None = None) -> str:
        return self._send("evidence.record_execution", {
            "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": TOOL_REF,
            "environment_digest": digest_json({"producer": observation_type, "tool": TOOL_REF}),
            "observation_type": observation_type, "observation": observation,
            "artifact_refs": artifact_refs or []})["evidence_id"]

    @staticmethod
    def _require_human(actor: ActorContext, what: str) -> None:
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, f"only a trusted person can {what}")

    # -------------------------------------------------------------- domain

    def active_gate(self) -> dict[str, Any] | None:
        """The newest compliance gate criterion, or None. Profile "none" means the gate is switched off."""
        return active_gate(self.store)

    def set_domain(self, actor: ActorContext, profile_id: str, options: dict[str, str] | None = None,
                   catalogs: list[str] | None = None) -> dict[str, Any]:
        self._require_human(actor, "choose the compliance domain")
        known = set(BUNDLED_CATALOGS) | set(self.imported_catalogs())
        missing = sorted(set(catalogs or []) - known)
        if missing:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"no catalog {missing}; import it first "
                             "(forge comply import-catalog)")
        resolved = resolve(profile_id, options, extra_catalogs=catalogs)
        parameters = {"profile_id": profile_id, "options": resolved.options, "catalogs": list(catalogs or [])}
        subject = (f"Compliance gate: {resolved.title}" + (f" ({', '.join(f'{k} {v}' for k, v in resolved.options.items())})"
                                                           if resolved.options else ""))
        criterion = self._send("criterion.create", {"subject": subject[:2000], "assessment_method": GATE_METHOD,
                                                    "parameters": parameters,
                                                    "waiver_policy": "allowed_with_decision"}, actor)
        requirement = self._send("requirement.create", {
            "statement": f"The software meets FORGE's {resolved.title} compliance gate: "
                         f"{', '.join(resolved.gate_checks) or 'no checks'}"[:4000],
            "criterion_ids": [criterion["criterion_id"]]}, actor)
        return {"criterion_id": criterion["criterion_id"], "requirement_id": requirement["requirement_id"],
                "profile": resolved.model_dump(mode="json")}

    # ------------------------------------------------------------ evidence

    def _snapshot_files(self, snapshot_id: str) -> tuple[dict[str, bytes], dict[str, str], list[str]]:
        snapshot = self.store.snapshot(snapshot_id)
        if snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        manifest = load_manifest(self.blobs, snapshot.manifest_digest)
        files: dict[str, bytes] = {}
        skipped: list[str] = []
        total = 0
        for entry in manifest.entries:
            if entry.size > producers.MAX_SCAN_BYTES or total + entry.size > MAX_TOTAL_SCAN_BYTES:
                skipped.append(entry.path)
                continue
            files[entry.path] = self.blobs.read_bytes(entry.digest, max_bytes=producers.MAX_SCAN_BYTES)
            total += entry.size
        return files, {e.path: e.digest for e in manifest.entries}, skipped

    def latest_snapshot(self) -> str:
        snapshots = self.store.snapshots()
        if not snapshots:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no snapshot yet: run `forge capture` first")
        return snapshots[-1].snapshot_id

    def produce(self, snapshot_id: str, *, requirements_path: str | None = None) -> dict[str, Any]:
        """Run every producer that needs no project code execution and record their evidence."""
        files, digests, skipped = self._snapshot_files(snapshot_id)
        project = self.store.project()
        recorded: dict[str, Any] = {}
        sbom, summary = producers.inventory(files, digests, subject_name=project.name if project else "project",
                                            subject_version=snapshot_id,
                                            timestamp=format_timestamp(self.controller.clock.now()),
                                            tool_version=__version__)
        sbom_id = self._artifact(canonical_json_bytes(sbom), "sbom_cyclonedx")
        recorded["software_inventory"] = self._record(snapshot_id, "software_inventory",
                                                      {**summary, "format": "CycloneDX 1.6", "sbom_artifact": sbom_id},
                                                      [sbom_id])
        findings, scanned = producers.scan_secrets(files)
        recorded["secrets_scan"] = self._record(snapshot_id, "secrets_scan", {
            "files": scanned, "findings": findings[:200], "skipped_large_files": len(skipped)})
        requirements = None
        if requirements_path is not None:
            if requirements_path not in files:
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"{requirements_path} is not in {snapshot_id}")
            requirements = producers.read_requirements(files[requirements_path])
        declared, project_note = {}, None
        if "forge.toml" in files:
            from forge.check.project import parse_project

            try:
                declared = parse_project(files["forge.toml"]).verifies()
            except ForgeError as error:
                project_note = f"forge.toml was not used for the trace: {error.message}"[:500]
        trace = producers.trace({p: d for p, d in files.items() if p not in (requirements_path, "forge.toml")},
                                requirements, verified_by_tests=declared)
        trace["project_file_note"] = project_note
        recorded["traceability"] = self._record(snapshot_id, "traceability",
                                                {**trace, "requirements_file": requirements_path})
        database = self.vulnerability_database()
        if database["records"]:
            matched = producers.match_vulnerabilities(producers.components_of(sbom), database["records"])
            recorded["vulnerability_scan"] = self._record(snapshot_id, "vulnerability_scan", {
                **matched, "database": database["identity"]})
        return {"evidence": recorded, "inventory": summary, "secrets": len(findings), "trace_gaps": len(trace["gaps"]),
                "requirements": len(trace["requirements"]), "vulnerability_database": database["identity"]}

    # -------------------------------------------------------------- checks

    def evaluate(self, snapshot_id: str, profile: ResolvedProfile) -> dict[str, CheckResult]:
        """Every FORGE check, evaluated from the record (the gate uses its subset; reports show them all)."""
        return {check_id: check.run(self.store, snapshot_id, profile.check_options.get(check_id, {}))
                for check_id, check in CHECKS.items()}

    @staticmethod
    def gate_status(profile: ResolvedProfile, results: dict[str, CheckResult]) -> str:
        if not profile.gate_checks:
            return "none"
        statuses = [results[c].status for c in profile.gate_checks]
        if "not_met" in statuses:
            return "fail"
        if "not_run" in statuses:
            return "incomplete"
        return "pass"

    def assess_gate(self, snapshot_id: str) -> dict[str, Any] | None:
        """Record the active gate's result on this snapshot, as FORGE (only FORGE records assessments)."""
        gate = self.active_gate()
        if gate is None or gate["parameters"]["profile_id"] == "none":
            return None
        parameters = gate["parameters"]
        profile = resolve(parameters["profile_id"], parameters.get("options"),
                          extra_catalogs=parameters.get("catalogs"))
        results = self.evaluate(snapshot_id, profile)
        status = self.gate_status(profile, results)
        outcome = {"pass": "pass", "fail": "fail", "incomplete": "inconclusive"}[status]
        limitations = [f"{c}: {results[c].status}: {results[c].detail} (produce with: {CHECKS[c].produced_by})"[:1000]
                       for c in profile.gate_checks if results[c].status in ("not_met", "not_run")][:32]
        # The evaluation itself is evidence: which checks ran, their results, and the records they rest on.
        evaluation = self._record(snapshot_id, "compliance_gate_evaluation", {
            "profile_id": profile.profile_id, "options": profile.options,
            "checks": {c: {"status": results[c].status, "detail": results[c].detail[:500],
                           "records": results[c].evidence[:20]} for c in profile.gate_checks}})
        evidence = [evaluation] + [e for c in profile.gate_checks for e in results[c].evidence
                                   if e.startswith("evidence_")]
        assessment = self._send("assessment.record", {
            "criterion_ref": {"id": gate["criterion_id"], "revision": gate["revision"]}, "snapshot_id": snapshot_id,
            "evidence_refs": list(dict.fromkeys(evidence))[:64], "assessment_result": outcome,
            "assessor_ref": f"{TOOL_REF}:{profile.profile_id}", "limitations": limitations,
            "summary": {"profile_id": profile.profile_id, "options": profile.options,
                        "checks": {c: results[c].status for c in profile.gate_checks}}})
        return {"assessment_id": assessment["assessment_id"], "result": outcome, "gate_status": status,
                "criterion_id": gate["criterion_id"]}

    def check(self, snapshot_id: str | None = None, *, requirements_path: str | None = None,
              profile_id: str | None = None, options: dict[str, str] | None = None) -> dict[str, Any]:
        """The one-command path: produce evidence, assess the gate, build the report."""
        snapshot_id = snapshot_id or self.latest_snapshot()
        produced = self.produce(snapshot_id, requirements_path=requirements_path)
        gate = self.assess_gate(snapshot_id)
        report = self.report(snapshot_id, profile_id=profile_id, options=options)
        return {"snapshot_id": snapshot_id, "produced": produced, "gate": gate,
                "report": report.model_dump(mode="json")}

    # ------------------------------------------------------------- reports

    def _mapping(self, catalog_id: str) -> dict[str, list[str]]:
        if catalog_id in MAPPINGS:
            return MAPPINGS[catalog_id]
        imported = self.imported_catalogs().get(catalog_id)
        return dict(imported["mappings"]) if imported else {}

    def catalog(self, catalog_id: str) -> CompactCatalog:
        if catalog_id in BUNDLED_CATALOGS:
            return bundled_catalog(catalog_id)
        imported = self.imported_catalogs().get(catalog_id)
        if imported is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"no catalog {catalog_id!r}")
        return CompactCatalog.model_validate(imported["catalog"])

    def _selection(self, catalog: CompactCatalog, baseline: str | None) -> tuple[list[Control], str]:
        current = [c for c in catalog.controls if not c.withdrawn]
        if baseline is not None:
            selected = set(bundled_baselines()[baseline]["control_ids"])
            info = bundled_baselines()[baseline]
            return ([c for c in current if c.control_id in selected],
                    f"{baseline} baseline ({len(selected)} controls; {info['source_url']} {info['source_sha256']})")
        if catalog.catalog_id == "nist-800-218":
            return [c for c in current if c.parent is None], "all practices"
        return current, "all current controls"

    def attestations(self, snapshot_id: str) -> dict[tuple[str, str], dict[str, Any]]:
        """Latest attestation per (catalog, label) that applies to this snapshot."""
        rows = self.store.conn.execute(
            "SELECT evidence_id, reviewer_id, subject_ref_json, observation_json, created_at FROM evidence "
            "WHERE origin = 'review' AND observation_type = ? ORDER BY rowid", (ATTESTATION,)).fetchall()
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for evidence_id, reviewer, subject, observation, created_at in rows:
            item = json.loads(observation)
            subject_id = json.loads(subject)["id"]
            if item.get("scope") == "snapshot" and subject_id != snapshot_id:
                continue
            key = (str(item.get("catalog_id")), str(item.get("label")))
            if item.get("withdrawn"):
                found.pop(key, None)
                continue
            found[key] = {"evidence_id": evidence_id, "reviewer_id": reviewer, "statement": item.get("statement"),
                          "scope": item.get("scope", "process"), "snapshot_id": subject_id, "created_at": created_at}
        return found

    def report(self, snapshot_id: str | None = None, *, profile_id: str | None = None,
               options: dict[str, str] | None = None) -> ComplianceReport:
        snapshot_id = snapshot_id or self.latest_snapshot()
        if self.store.snapshot(snapshot_id) is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        gate = self.active_gate()
        if profile_id is None:
            if gate is None:
                profile_id, extra = "general", []
            else:
                profile_id, extra = gate["parameters"]["profile_id"], gate["parameters"].get("catalogs", [])
                options = options or gate["parameters"].get("options")
        else:
            extra = []
        profile = resolve(profile_id, options, extra_catalogs=extra)
        results = self.evaluate(snapshot_id, profile)
        attested = self.attestations(snapshot_id)
        sections = [self._section(use.catalog_id, use.baseline, results, attested) for use in profile.catalogs]
        standards = []
        for standard in profile.standards:
            topics = {topic: _combine([results[c] for c in checks if c in results])
                      for topic, checks in standard.topics.items()}
            standards.append({**standard.model_dump(mode="json"), "topic_status": topics})
        limitations = [
            "FORGE's checks are evidence about this snapshot, not a certification or an auditor's opinion.",
            "A control marked 'met' is supported by FORGE's evidence; its organizational parts (policy, "
            "training, staffing, operations) still need the attestation of the person responsible.",
            "Licensed standards are cited by topic only; import your licensed copy to cite their clauses.",
        ]
        return ComplianceReport(snapshot_id=snapshot_id, generated_at=format_timestamp(self.controller.clock.now()),
                                profile=profile, gate_status=self.gate_status(profile, results),
                                checks={k: {"status": v.status, "detail": v.detail, "evidence": v.evidence,
                                            "title": CHECKS[k].title, "produced_by": CHECKS[k].produced_by}
                                        for k, v in results.items()},
                                catalogs=sections, standards=standards, limitations=limitations)

    def _section(self, catalog_id: str, baseline: str | None, results: dict[str, CheckResult],
                 attested: dict[tuple[str, str], dict[str, Any]]) -> CatalogSection:
        catalog = self.catalog(catalog_id)
        controls, selection = self._selection(catalog, baseline)
        mapping = self._mapping(catalog_id)
        rows = []
        for control in controls:
            checks = [c for c in mapping.get(control.label, []) if c in results]
            attestation = attested.get((catalog_id, control.label))
            relevant = [results[c] for c in checks if results[c].status != "not_applicable"]
            if relevant:
                combined = _combine(relevant)
                status: ControlStatus = {"met": "met", "not_met": "gap", "not_run": "incomplete"}[combined]
            else:
                status = "attested" if attestation else "needs_attestation"
            rows.append(ControlResult(catalog_id=catalog_id, label=control.label, title=control.title,
                                      group=control.group, status=status, checks=checks,
                                      detail=[f"{c}: {results[c].detail}" for c in checks],
                                      evidence=[e for c in checks for e in results[c].evidence][:20],
                                      attestation=attestation))
        counts = Counter(row.status for row in rows)
        return CatalogSection(catalog_id=catalog_id, title=catalog.title, version=catalog.version,
                              last_modified=catalog.last_modified, source_url=catalog.source_url,
                              source_sha256=catalog.source_sha256, license=catalog.license, selection=selection,
                              counts={k: counts.get(k, 0) for k in ("met", "gap", "incomplete", "attested",
                                                                    "needs_attestation")}, controls=rows)

    # -------------------------------------------------------- attestations

    def attest(self, actor: ActorContext, *, snapshot_id: str | None, catalog_id: str, labels: list[str],
               statement: str, scope: str = "process", withdraw: bool = False) -> list[str]:
        """A person's statement about controls FORGE cannot check. Recorded as attributed review evidence."""
        self._require_human(actor, "attest to a control")
        if scope not in ("process", "snapshot"):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "scope must be 'process' or 'snapshot'")
        if not statement.strip() and not withdraw:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "an attestation needs a statement saying what is in place")
        snapshot_id = snapshot_id or self.latest_snapshot()
        snapshot = self.store.snapshot(snapshot_id)
        if snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        known = {c.label for c in self.catalog(catalog_id).controls if not c.withdrawn}
        unknown = [label for label in labels if label not in known]
        if unknown:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{catalog_id} has no current control {unknown[:5]}")
        ids = []
        for label in labels:
            ids.append(self._send("evidence.record_review", {
                "subject_ref": {"id": snapshot_id, "revision": 1}, "observation_type": ATTESTATION,
                "observation": {"catalog_id": catalog_id, "label": label, "statement": statement[:4000],
                                "scope": scope, "withdrawn": withdraw}}, actor)["evidence_id"])
        return ids

    # ------------------------------------------------------------- imports

    def imported_catalogs(self) -> dict[str, dict[str, Any]]:
        """Imported catalogs by id; a later import of the same id supersedes the earlier one."""
        found: dict[str, dict[str, Any]] = {}
        for (digest,) in self.store.conn.execute(
                "SELECT digest FROM artifacts WHERE artifact_kind = 'compliance_catalog' AND tombstoned_at IS NULL "
                "ORDER BY rowid"):
            item = json.loads(self.blobs.read_bytes(digest, max_bytes=MAX_IMPORT_BYTES))
            found[item["catalog"]["catalog_id"]] = item
        return found

    def import_catalog(self, actor: ActorContext, catalog: dict[str, Any],
                       mappings: dict[str, list[str]] | None = None) -> dict[str, Any]:
        """Store a compact catalog (from oscal.import_catalog or parse_csv_catalog) with its check mappings."""
        self._require_human(actor, "import a catalog")
        parsed = CompactCatalog.model_validate(catalog)
        if parsed.catalog_id in BUNDLED_CATALOGS or not CATALOG_ID.match(parsed.catalog_id):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "catalog id must be a new short name: lowercase letters, "
                             "digits and dashes, not one of FORGE's bundled catalogs")
        labels = {c.label for c in parsed.controls}
        mappings = {str(k): [str(c) for c in v] for k, v in (mappings or {}).items()}
        bad_labels = sorted(set(mappings) - labels)
        bad_checks = sorted({c for v in mappings.values() for c in v} - set(CHECKS))
        if bad_labels or bad_checks:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"mapping names unknown controls {bad_labels[:5]} or "
                             f"checks {bad_checks[:5]}; checks are {sorted(CHECKS)}")
        payload = {"catalog": parsed.model_dump(mode="json"), "mappings": mappings, "imported_by": actor.actor_id}
        artifact_id = self._artifact(canonical_json_bytes(payload), "compliance_catalog", "sensitive")
        return {"artifact_id": artifact_id, "catalog_id": parsed.catalog_id, "controls": len(parsed.controls),
                "mapped": len(mappings)}

    def import_vulnerabilities(self, actor: ActorContext, records: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
        """Add OSV records to the project's offline vulnerability database."""
        self._require_human(actor, "import a vulnerability database")
        valid = [r for r in records if isinstance(r, dict) and isinstance(r.get("id"), str)
                 and isinstance(r.get("affected", []), list)]
        if not valid:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no OSV records (each needs an 'id' and 'affected' list)")
        payload = {"format": "osv", "source": source[:500], "records": valid}
        artifact_id = self._artifact(canonical_json_bytes(payload), "vulnerability_database")
        return {"artifact_id": artifact_id, "records": len(valid), "ignored": len(records) - len(valid)}

    def vulnerability_database(self) -> dict[str, Any]:
        records: dict[str, dict[str, Any]] = {}
        digests = []
        for (digest,) in self.store.conn.execute(
                "SELECT digest FROM artifacts WHERE artifact_kind = 'vulnerability_database' AND tombstoned_at IS NULL "
                "ORDER BY rowid"):
            digests.append(digest)
            for record in json.loads(self.blobs.read_bytes(digest, max_bytes=MAX_IMPORT_BYTES))["records"]:
                records[record["id"]] = record
        identity = (f"osv: {len(records)} records from {len(digests)} imports, {digest_json(digests)}" if digests
                    else "none imported")
        return {"records": list(records.values()), "identity": identity}

    # --------------------------------------------------------------- lists

    def domains(self) -> dict[str, Any]:
        return {"domains": [p.model_dump(mode="json") for p in PROFILES.values()],
                "catalogs": {**{c: {"title": bundled_catalog(c).title, "version": bundled_catalog(c).version,
                                    "bundled": True} for c in BUNDLED_CATALOGS},
                             **{c: {"title": i["catalog"]["title"], "version": i["catalog"]["version"],
                                    "bundled": False} for c, i in self.imported_catalogs().items()}},
                "active": self.active_gate()}


def _combine(results: list[CheckResult]) -> str:
    statuses = [r.status for r in results if r.status != "not_applicable"]
    if not statuses:
        return "not_applicable"
    if "not_met" in statuses:
        return "not_met"
    if "not_run" in statuses:
        return "not_run"
    return "met"


def active_gate(store) -> dict[str, Any] | None:  # noqa: ANN001 - RecordReader
    row = store.conn.execute(
        "SELECT record_id, revision, body_json FROM records WHERE record_kind = 'criterion' "
        "AND json_extract(body_json, '$.assessment_method') = ? ORDER BY rowid DESC LIMIT 1", (GATE_METHOD,)).fetchone()
    if row is None:
        return None
    body = json.loads(row[2])
    return {"criterion_id": row[0], "revision": row[1], "parameters": body.get("parameters", {}),
            "subject": body.get("subject", "")}


def parse_csv_catalog(raw: bytes, *, catalog_id: str, title: str, version: str, source: str,
                      license_note: str) -> dict[str, Any]:
    """A catalog listed as CSV: columns id, title, and optionally group and parent (header row required)."""
    import csv
    import hashlib
    import io

    if len(raw) > MAX_IMPORT_BYTES:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "catalog file is too large")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace")))
    fields = {name.strip().lower(): name for name in reader.fieldnames or []}
    if "id" not in fields or "title" not in fields:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "the CSV needs a header row with at least 'id' and 'title'")
    controls = []
    for row in reader:
        label = (row.get(fields["id"]) or "").strip()
        if not label:
            continue
        parent = (row.get(fields["parent"]) or "").strip() if "parent" in fields else ""
        controls.append({"control_id": label, "label": label, "title": (row.get(fields["title"]) or "").strip()[:300],
                         "group": (row.get(fields["group"]) or "").strip()[:200] if "group" in fields else "",
                         "parent": parent or None, "withdrawn": False})
    if not controls:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "the CSV lists no controls")
    return {"catalog_id": catalog_id, "title": title, "version": version, "last_modified": "", "source_url": source,
            "source_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(), "license": license_note,
            "controls": controls}


def parse_mapping_csv(raw: bytes) -> dict[str, list[str]]:
    """Mapping file: one row per control, `label,check_id[;check_id...]` (header optional)."""
    import csv
    import io

    mappings: dict[str, list[str]] = {}
    for row in csv.reader(io.StringIO(raw.decode("utf-8-sig", "replace"))):
        if len(row) < 2 or not row[0].strip() or row[0].strip().lower() in ("label", "id", "control"):
            continue
        checks = [c.strip() for c in re.split(r"[;|]", row[1]) if c.strip()]
        mappings.setdefault(row[0].strip(), []).extend(c for c in checks if c not in mappings.get(row[0].strip(), []))
    return mappings

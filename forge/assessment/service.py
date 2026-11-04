"""AssessmentService: measure a snapshot and record what was observed.

Order of operations for a black-box criterion:

1. Resolve the criterion and the protected suite it pins (artifact ID plus
   digest); verify the suite bytes against that digest.
2. Materialize the snapshot into a fresh directory.
3. If the isolation backend is unavailable, record `not_run` with the reason.
   Never run candidate code with weaker isolation.
4. Run the suite in the sandbox; compare outputs in trusted code.
5. Record the full report as an artifact, then execution evidence that
   cites it, then the assessment that cites the evidence, all through the
   controller as the trusted system actor.

Expensive work (steps 2 to 4) happens before any database write.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from forge.assessment.analyzers import AnalyzerRun, run_ruff, verdict
from forge.assessment.evaluator import TOOL_REF, SuiteRun, run_suite
from forge.assessment.suite import EvaluatorSuite
from forge.authority.authorize import DEFAULT_LIMITS, ResourceLimits
from forge.contracts.digests import canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.versions import SCHEMA_VERSION
from forge.sandbox.base import SandboxBackend
from forge.tools.registry import ToolDefinition
from forge.workspace.snapshot import load_manifest, materialize


def interpreter() -> tuple[str, list[str]]:
    """The host Python for black-box runs, plus any directory to mount for it."""
    path = os.path.realpath(sys.executable)
    if path.startswith(("/usr/", "/bin/", "/lib/")):
        return path, []
    return path, [os.path.realpath(sys.base_prefix)]


class AssessmentService:
    def __init__(
        self,
        controller,  # noqa: ANN001 - forge.controller.service.Controller
        backend: SandboxBackend,
        work_root: Path,
        *,
        limits: ResourceLimits = DEFAULT_LIMITS,
    ) -> None:
        self.controller = controller
        self.backend = backend
        self.work_root = Path(work_root)
        self.limits = limits
        self.python_path, self.python_paths = interpreter()

    # ------------------------------------------------------------- helpers

    def _send(self, method: str, params: dict[str, Any]) -> Any:
        project = self.controller.store.project()
        return self.controller.apply_command(
            {
                "schema_version": SCHEMA_VERSION,
                "method": method,
                "project_id": project.project_id,
                "idempotency_key": new_id("request"),
                "params": params,
            },
            self.controller.system_actor,
        )

    def _materialize(self, snapshot_id: str) -> Path:
        snapshot = self.controller.store.snapshot(snapshot_id)
        if snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        manifest = load_manifest(self.controller.blobs, snapshot.manifest_digest)
        destination = self.work_root / f"snapshot-{snapshot_id}-{new_id('job').removeprefix('job_')[:8]}"
        materialize(manifest, self.controller.blobs, destination)
        return destination

    def load_suite(self, artifact_id: str, digest: str) -> EvaluatorSuite:
        artifact = self.controller.store.artifact(artifact_id)
        if artifact is None or artifact.artifact_kind != "evaluator_suite" or artifact.digest != digest:
            raise ForgeError(ErrorCode.ARTIFACT_MISSING, f"{artifact_id} is not the admitted suite {digest}")
        data = self.controller.blobs.read_bytes(digest, max_bytes=64 * 1024 * 1024)
        suite = EvaluatorSuite.model_validate_json(data)
        if suite.digest() != digest:
            raise ForgeError(ErrorCode.ARTIFACT_MISSING, "suite bytes are not canonical for their digest")
        return suite

    def _record_report(self, kind: str, report: dict[str, Any]) -> str:
        blob = self.controller.blobs.put_bytes(canonical_json_bytes(report))
        receipt = self._send(
            "artifact.register",
            {"digest": blob.digest, "size": blob.size, "content_type": "application/json",
             "artifact_kind": kind, "sensitivity": "project"},
        )
        return receipt.result["artifact_id"]

    # ---------------------------------------------------- black-box criteria

    def assess_black_box(self, criterion_id: str, snapshot_id: str, *, repeat: int = 1) -> dict[str, Any]:
        criterion = self.controller.store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "black_box_cases":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not a black_box_cases criterion")
        criterion_ref = {"id": criterion.criterion_id, "revision": criterion.revision}
        suite = self.load_suite(criterion.parameters["suite_artifact_id"], criterion.parameters["suite_digest"])

        capabilities = self.backend.capabilities()
        if not capabilities.available:
            receipt = self._send(
                "assessment.record",
                {
                    "criterion_ref": criterion_ref,
                    "snapshot_id": snapshot_id,
                    "assessment_result": "not_run",
                    "assessor_ref": TOOL_REF,
                    "limitations": [f"isolation backend unavailable: {capabilities.unavailable_reason}"],
                },
            )
            return {"assessment_id": receipt.result["assessment_id"], "result": "not_run", "evidence_id": None}

        snapshot_dir = self._materialize(snapshot_id)
        job_id = new_id("job")
        try:
            run: SuiteRun = run_suite(
                suite, snapshot_dir, self.backend, self.work_root / job_id,
                python_path=self.python_path, limits=self.limits, repeat=repeat,
                toolchain_paths=self.python_paths,
            )
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)

        report_id = self._record_report("evaluation_report", run.model_dump(mode="json"))
        evidence = self._send(
            "evidence.record_execution",
            {
                "job_id": job_id,
                "attempt_number": 1,
                "snapshot_id": snapshot_id,
                "tool_ref": TOOL_REF,
                "environment_digest": run.environment_digest,
                "evaluator_digest": run.evaluator_digest,
                "observation_type": "black_box_case_results",
                "observation": {
                    "suite_id": run.suite_id,
                    "suite_digest": run.suite_digest,
                    "counts": run.counts,
                    "failed_cases": [c.case_id for c in run.cases if c.outcome == "fail"][:50],
                    "error_cases": [c.case_id for c in run.cases if c.outcome == "error"][:50],
                },
                "artifact_refs": [report_id],
            },
        )
        evidence_id = evidence.result["evidence_id"]
        assessment = self._send(
            "assessment.record",
            {
                "criterion_ref": criterion_ref,
                "snapshot_id": snapshot_id,
                "evidence_refs": [evidence_id],
                "assessment_result": run.result,
                "assessor_ref": TOOL_REF,
                "limitations": run.limitations,
                "summary": {"counts": run.counts},
            },
        )
        return {
            "assessment_id": assessment.result["assessment_id"],
            "result": run.result,
            "evidence_id": evidence_id,
            "report_artifact_id": report_id,
            "run": run,
        }

    def suite_can_fail(self, suite: EvaluatorSuite, known_bad_snapshot_id: str) -> bool:
        """Probe (D03): a suite that passes a known-bad candidate cannot detect the defect."""
        snapshot_dir = self._materialize(known_bad_snapshot_id)
        try:
            run = run_suite(
                suite, snapshot_dir, self.backend, self.work_root / new_id("job"),
                python_path=self.python_path, limits=self.limits, toolchain_paths=self.python_paths,
            )
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        return run.counts["fail"] > 0

    # ------------------------------------------------------------ analyzers

    def analyze(self, snapshot_id: str, tool: ToolDefinition) -> dict[str, Any]:
        snapshot_dir = self._materialize(snapshot_id)
        job_id = new_id("job")
        try:
            run: AnalyzerRun = run_ruff(snapshot_dir, self.backend, self.work_root / job_id, tool, self.limits)
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        report_id = self._record_report("analyzer_report", run.model_dump(mode="json"))
        evidence = self._send(
            "evidence.record_execution",
            {
                "job_id": job_id,
                "attempt_number": 1,
                "snapshot_id": snapshot_id,
                "tool_ref": f"{tool.tool_id}@{tool.version}",
                "environment_digest": digest_json(self.backend.capabilities().model_dump(mode="json")),
                "observation_type": "analyzer_findings",
                "observation": {"status": run.status, "error": run.error, "finding_count": len(run.findings)},
                "artifact_refs": [report_id],
            },
        )
        evidence_id = evidence.result["evidence_id"]
        if run.status == "error":
            return {"status": "error", "error": run.error, "evidence_id": evidence_id, "verdict": None}
        finding_ids = []
        for item in run.findings:
            receipt = self._send(
                "finding.record",
                {
                    "rule_id": item.rule_id,
                    "claim": item.message or item.rule_id,
                    "severity": item.severity,
                    "path": item.path,
                    "line": item.line,
                    "fingerprint": item.fingerprint,
                    "snapshot_id": snapshot_id,
                    "evidence_refs": [evidence_id],
                },
            )
            finding_ids.append(receipt.result["finding_id"])
        return {
            "status": "ok",
            "evidence_id": evidence_id,
            "finding_ids": finding_ids,
            "findings": run.findings,
            "verdict": verdict(run.findings),
        }

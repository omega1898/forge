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



PACKAGE_TOOL_REF = "forge.packaging.wheel_install@1"
_SCRIPT_NAME = __import__("re").compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Runs inside the sandbox. The console-script name arrives as argv[1]; it is
# never interpolated into code. Exit codes: 10 build failed, 11 install
# failed, 12 the installed command did not run.
_PACKAGE_SCRIPT = r"""
import glob, shutil, subprocess, sys
name = sys.argv[1]
shutil.copytree("/input", "/scratch/src")
subprocess.run([sys.executable, "-m", "venv", "/scratch/build"], check=True)
deps = sorted(glob.glob("/case/*.whl"))
if deps and subprocess.run(["/scratch/build/bin/python", "-m", "pip", "install", "--no-index", "-q", *deps]).returncode:
    sys.exit(13)
if subprocess.run(["/scratch/build/bin/python", "-m", "pip", "wheel", "--no-index", "--no-build-isolation",
                   "--no-deps", "-q", "-w", "/output", "."], cwd="/scratch/src").returncode:
    sys.exit(10)
subprocess.run([sys.executable, "-m", "venv", "/scratch/venv"], check=True)
wheels = glob.glob("/output/*.whl")
if not wheels or subprocess.run(["/scratch/venv/bin/python", "-m", "pip", "install", "--no-index", "-q",
                                 *wheels]).returncode:
    sys.exit(11)
sys.exit(0 if subprocess.run(["/scratch/venv/bin/" + name, "--help"], capture_output=True).returncode == 0 else 12)
"""
_PACKAGE_FAILURES = {10: "the wheel did not build", 11: "the wheel did not install into a clean environment",
                     13: "an admitted build dependency did not install",
                     12: "the installed command did not run"}


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
        # Build backends for packaging checks, pinned by digest. Offline builds
        # never use whatever happens to be installed on the host.
        self.build_dependencies: dict[str, str] = {}

    def admit_build_dependency(self, wheel_path: Path) -> str:
        """Pin a build-backend wheel (for example setuptools) into the artifact store."""
        path = Path(wheel_path)
        if not path.name.endswith(".whl") or "/" in path.name:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "build dependencies must be wheel files")
        stored = self.controller.blobs.put_bytes(path.read_bytes(), max_bytes=64 * 1024 * 1024)
        self.build_dependencies[path.name] = stored.digest
        return stored.digest

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

    def assess_analyzer(self, criterion_id: str, snapshot_id: str, tool: ToolDefinition) -> dict[str, Any]:
        """Static review as a criterion: approve -> pass, request_changes -> fail."""
        criterion = self.controller.store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "analyzer_verdict":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not an analyzer_verdict criterion")
        outcome = self.analyze(snapshot_id, tool)
        result = {"approve": "pass", "request_changes": "fail", "needs_discussion": "inconclusive", None: "error"}[
            outcome["verdict"]]
        limitations = ["static analysis checks the selected rule families only; it does not prove absence of defects"]
        if result == "inconclusive":
            limitations.append("high-severity findings need a reproducer or a reviewer decision")
        receipt = self._send("assessment.record", {
            "criterion_ref": {"id": criterion.criterion_id, "revision": criterion.revision},
            "snapshot_id": snapshot_id, "evidence_refs": [outcome["evidence_id"]], "assessment_result": result,
            "assessor_ref": f"forge.analyzer_verdict@{tool.tool_id}", "limitations": limitations,
            "summary": {"verdict": outcome["verdict"], "findings": len(outcome.get("findings") or [])},
        })
        return {"assessment_id": receipt.result["assessment_id"], "result": result, **outcome}


    def assess_package(self, criterion_id: str, snapshot_id: str) -> dict[str, Any]:
        """Build a wheel, install it into a clean environment, run its command. All offline."""
        from forge.execution.collector import collect_outputs
        from forge.sandbox.base import SandboxSpec

        criterion = self.controller.store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "package_install":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not a package_install criterion")
        script_name = str(criterion.parameters.get("console_script", ""))
        if not _SCRIPT_NAME.match(script_name):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "console_script must be a simple command name")
        criterion_ref = {"id": criterion.criterion_id, "revision": criterion.revision}
        if not self.backend.capabilities().available:
            receipt = self._send("assessment.record", {
                "criterion_ref": criterion_ref, "snapshot_id": snapshot_id, "assessment_result": "not_run",
                "assessor_ref": PACKAGE_TOOL_REF,
                "limitations": [f"isolation backend unavailable: {self.backend.capabilities().unavailable_reason}"]})
            return {"assessment_id": receipt.result["assessment_id"], "result": "not_run"}
        if not self.build_dependencies:
            receipt = self._send("assessment.record", {
                "criterion_ref": criterion_ref, "snapshot_id": snapshot_id, "assessment_result": "not_run",
                "assessor_ref": PACKAGE_TOOL_REF,
                "limitations": ["no build backend has been admitted for offline packaging"]})
            return {"assessment_id": receipt.result["assessment_id"], "result": "not_run"}
        snapshot_dir = self._materialize(snapshot_id)
        job_id = new_id("job")
        work = self.work_root / job_id
        deps_dir = self.work_root / f"{job_id}-deps"
        deps_dir.mkdir(parents=True)
        for name, digest in self.build_dependencies.items():
            with open(deps_dir / name, "wb") as handle:
                for chunk in self.controller.blobs.iter_verified(digest):
                    handle.write(chunk)
        limits = self.limits.model_copy(update={"wall_ms": max(self.limits.wall_ms, 180_000),
                                                "cpu_seconds": max(self.limits.cpu_seconds, 120)})
        try:
            observation = self.backend.run(
                SandboxSpec(job_key="package_check", input_dir=str(snapshot_dir), case_dir=str(deps_dir), cwd="/input",
                            argv=[self.python_path, "-I", "-c", _PACKAGE_SCRIPT, script_name], limits=limits,
                            toolchain_paths=self.python_paths),
                work,
            )
            collected = collect_outputs(Path(observation.output_dir), self.controller.blobs, max_files=10,
                                        max_bytes=limits.output_bytes)
        finally:
            destroy = getattr(self.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            shutil.rmtree(deps_dir, ignore_errors=True)
        artifact_refs = []
        for item in collected.files if collected.accepted else []:
            receipt = self._send("artifact.register", {"digest": item.digest, "size": item.size,
                                                       "content_type": "application/zip", "artifact_kind": "python_wheel",
                                                       "sensitivity": "project"})
            artifact_refs.append(receipt.result["artifact_id"])
        if observation.terminated_by in ("launch_failed", "cancelled"):
            result, reason = "error", f"measurement incomplete: {observation.terminated_by}"
        elif observation.terminated_by != "exited":
            result, reason = "fail", f"packaging check {observation.terminated_by}"
        elif observation.exit_code == 0:
            result, reason = "pass", "built, installed into a clean environment, and the command ran"
        elif observation.exit_code == 13:
            result, reason = "error", _PACKAGE_FAILURES[13]
        else:
            result, reason = "fail", _PACKAGE_FAILURES.get(observation.exit_code, f"exit {observation.exit_code}")
        evidence = self._send("evidence.record_execution", {
            "job_id": job_id, "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": PACKAGE_TOOL_REF,
            "environment_digest": digest_json(self.backend.capabilities().model_dump(mode="json")),
            "observation_type": "package_install",
            "observation": {"exit_code": observation.exit_code, "terminated_by": observation.terminated_by,
                            "reason": reason, "stderr_tail": observation.stderr[-1500:].decode("utf-8", "replace"),
                            "wheels": [item.path for item in collected.files]},
            "artifact_refs": artifact_refs,
        })
        receipt = self._send("assessment.record", {
            "criterion_ref": criterion_ref, "snapshot_id": snapshot_id, "evidence_refs": [evidence.result["evidence_id"]],
            "assessment_result": result, "assessor_ref": PACKAGE_TOOL_REF,
            "limitations": ["installed and run on this Linux host only; other platforms are not_run"],
            "summary": {"reason": reason},
        })
        return {"assessment_id": receipt.result["assessment_id"], "result": result, "reason": reason,
                "wheel_artifact_ids": artifact_refs}

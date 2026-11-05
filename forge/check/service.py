"""`forge check`: build, test, measure coverage and analyze a project from its forge.toml.

    forge check            everything below, then the compliance gate
    forge check tests      the accepted test cases against a fresh build
    forge check coverage   statement, branch and MC/DC coverage (Ada, Fortran, C, C++ with GCC 14)
    forge check analyze    compiler warnings and the C analyzer (or ruff for Python) as findings
    forge check sanitize   C and C++ tests under AddressSanitizer and UndefinedBehaviorSanitizer

Every step runs inside the sandbox and records evidence and assessments
about the exact snapshot, which the compliance checks then read. Nothing
here trusts the project's own claims about itself.

Protected tests: the first run accepts the tests in forge.toml as a
protected suite in the trusted person's name. After that, the suite that
runs is the accepted one. If forge.toml changes (whoever changed it), FORGE
says so and keeps running the accepted tests until a trusted person accepts
the change (`--accept-tests`). A model can run checks but cannot accept
tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from forge.assessment.service import DEFAULT_LIMITS, AssessmentService
from forge.check import coverage as cov
from forge.check.project import COMPILED, COVERAGE, NATIVE, PROJECT_FILE, ProjectSpec, parse_project
from forge.contracts.commands import ActorContext
from forge.contracts.digests import digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.execution.collector import collect_outputs
from forge.packs.compiled import _BUILD_SHELL, GCOV_TOOLS, TOOLCHAINS, CompiledAssessor, admit_compiled_tools
from forge.packs.native import NativeAssessor, admit_native_tools, parse_diagnostics
from forge.packs.python_pack import admit_python_tools
from forge.sandbox.base import SandboxSpec
from forge.tools.registry import ToolRegistry
from forge.workspace.snapshot import load_manifest

CHECK_TOOL_REF = "forge.check@1"
MAX_PROJECT_FILE = 1024 * 1024
DEFAULT_STANDARD = {"c": "c11", "cpp": "c++17"}
STEPS = ("tests", "analyze", "coverage", "sanitize")


def default_registry() -> ToolRegistry:
    """Every toolchain installed on this machine, pinned by digest at admission."""
    registry = ToolRegistry()
    admit_python_tools(registry)
    admit_native_tools(registry)
    admit_compiled_tools(registry)
    return registry


class CheckService:
    def __init__(self, controller, backend, work_root: Path, *, registry: ToolRegistry | None = None,  # noqa: ANN001
                 limits=DEFAULT_LIMITS) -> None:  # noqa: ANN001
        self.controller = controller
        self.store = controller.store
        self.blobs = controller.blobs
        self.assessment = AssessmentService(controller, backend, Path(work_root), limits=limits)
        self.backend = backend
        self.registry = registry or default_registry()

    # ------------------------------------------------------------- helpers

    def _send(self, method: str, params: dict[str, Any], actor: ActorContext | None = None) -> dict[str, Any]:
        project = self.store.project()
        return self.controller.apply_command({"schema_version": "1.0", "method": method,
                                              "project_id": project.project_id, "idempotency_key": new_id("request"),
                                              "params": params}, actor or self.controller.system_actor).result

    def _manifest(self, snapshot_id: str):  # noqa: ANN202
        snapshot = self.store.snapshot(snapshot_id)
        if snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        return load_manifest(self.blobs, snapshot.manifest_digest)

    def _files(self, snapshot_id: str, suffixes: tuple[str, ...] | None = None,
               max_bytes: int = 4 * 1024 * 1024) -> dict[str, bytes]:
        return {e.path: self.blobs.read_bytes(e.digest, max_bytes=max_bytes) for e in self._manifest(snapshot_id).entries
                if (suffixes is None or e.path.lower().endswith(suffixes)) and e.size <= max_bytes}

    def latest_snapshot(self) -> str:
        snapshots = self.store.snapshots()
        if not snapshots:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "no snapshot yet: run `forge capture` (or `forge check`)")
        return snapshots[-1].snapshot_id

    def project_file(self, snapshot_id: str) -> ProjectSpec | None:
        entry = next((e for e in self._manifest(snapshot_id).entries if e.path == PROJECT_FILE), None)
        if entry is None:
            return None
        return parse_project(self.blobs.read_bytes(entry.digest, max_bytes=MAX_PROJECT_FILE))

    # ----------------------------------------------------- protected tests

    def accepted(self) -> Any:  # noqa: ANN401 - Criterion | None
        row = self.store.conn.execute(
            "SELECT record_id FROM records WHERE record_kind = 'criterion' "
            "AND json_extract(body_json, '$.parameters.forge_project') = 1 ORDER BY rowid DESC LIMIT 1").fetchone()
        return None if row is None else self.store.criterion(row[0])

    def _accept(self, actor: ActorContext, spec: ProjectSpec) -> Any:  # noqa: ANN401
        if not actor.is_trusted_human:
            raise ForgeError(ErrorCode.PERMISSION_DENIED, "only a trusted person accepts tests as the protected suite")
        admitted = self._send("suite.admit", {"suite": spec.suite()}, actor)
        build = spec.build
        parameters: dict[str, Any] = {"forge_project": True, "spec_digest": spec.digest(),
                                      "project_build": build.model_dump(mode="json"),
                                      "suite_artifact_id": admitted["suite_artifact_id"],
                                      "suite_digest": admitted["suite_digest"]}
        if build.toolchain in COMPILED:
            method = "compiled_cases"
            parameters["build"] = {"toolchain": build.toolchain, "sources": build.sources}
        elif build.toolchain in NATIVE:
            method = "native_cases"
            parameters["build"] = {
                "language": build.toolchain, "compiler": "gcc" if build.toolchain == "c" else "gxx",
                "standard": build.standard or DEFAULT_STANDARD[build.toolchain], "sources": build.sources,
                "include_dirs": build.include_dirs, "defines": build.defines, "warnings_as_errors": False,
                "sanitizers": ["address", "undefined"], "output": "program"}
        else:
            method = "black_box_cases"
        created = self._send("criterion.create", {
            "subject": f"{PROJECT_FILE} tests: {len(spec.tests)} cases ({build.toolchain})",
            "assessment_method": method, "parameters": parameters}, actor)
        return self.store.criterion(created["criterion_id"])

    def criterion_for(self, actor: ActorContext, snapshot_id: str, *, accept: bool) -> tuple[Any, list[str]]:
        notices: list[str] = []
        current = self.accepted()
        spec = self.project_file(snapshot_id)
        if spec is None:
            if current is None:
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"no {PROJECT_FILE} in this snapshot: create one with "
                                 "`forge check init`, then capture again")
            notices.append(f"this snapshot has no {PROJECT_FILE}; the accepted tests ran")
            return current, notices
        if current is not None and current.parameters.get("spec_digest") == spec.digest():
            return current, notices
        if current is None or accept:
            accepted = self._accept(actor, spec)
            notices.append(f"accepted {len(spec.tests)} test case(s) from {PROJECT_FILE} as the protected suite"
                           + (" (replacing the earlier accepted tests)" if current is not None else ""))
            return accepted, notices
        notices.append(f"{PROJECT_FILE} changed since its tests were accepted; the accepted tests ran. Review the "
                       "change, then run with --accept-tests to use it")
        return current, notices

    # --------------------------------------------------------------- tests

    @staticmethod
    def _cases(run: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - SuiteRun | None
        if run is None:
            return []
        return [{"case_id": c.case_id, "outcome": c.outcome, "exit_code": c.exit_code,
                 "reasons": c.reasons[:3], "stderr": c.stderr_excerpt[-300:]} for c in run.cases]

    def tests(self, actor: ActorContext, snapshot_id: str | None = None, *, accept: bool = False) -> dict[str, Any]:
        snapshot_id = snapshot_id or self.latest_snapshot()
        criterion, notices = self.criterion_for(actor, snapshot_id, accept=accept)
        method = criterion.assessment_method
        if method == "compiled_cases":
            outcome = CompiledAssessor(self.assessment, self.registry).assess(criterion.criterion_id, snapshot_id)
        elif method == "native_cases":
            outcome = NativeAssessor(self.assessment, self.registry).assess(criterion.criterion_id, snapshot_id)
        else:
            outcome = self.assessment.assess_black_box(criterion.criterion_id, snapshot_id)
        result: dict[str, Any] = {
            "step": "tests", "result": outcome["result"], "assessment_id": outcome.get("assessment_id"),
            "criterion_id": criterion.criterion_id, "toolchain": criterion.parameters["project_build"]["toolchain"],
            "cases": self._cases(outcome.get("run")), "notices": notices,
            "reason": outcome.get("reason") or (outcome.get("stderr", "") or "")[-1500:] or None}
        if outcome.get("reports"):
            result["sanitizer_reports"] = [f"{case}: {r.sanitizer}.{r.kind} at {r.path}:{r.line}"
                                           for case, r in outcome["reports"]][:20]
        if outcome.get("diagnostics"):
            result["diagnostics"] = [f"{d.path}:{d.line}: {d.severity}: {d.message}" for d in outcome["diagnostics"]][:20]
        return result

    def sanitize(self, actor: ActorContext, snapshot_id: str | None = None, *, accept: bool = False) -> dict[str, Any]:
        snapshot_id = snapshot_id or self.latest_snapshot()
        criterion, notices = self.criterion_for(actor, snapshot_id, accept=accept)
        toolchain = criterion.parameters["project_build"]["toolchain"]
        if toolchain not in NATIVE:
            return {"step": "sanitize", "result": "not_applicable", "notices": notices,
                    "reason": f"sanitizers apply to C and C++ builds; this project is {toolchain}"}
        result = self.tests(actor, snapshot_id, accept=accept)
        return {**result, "step": "sanitize", "sanitizers": ["address", "undefined"]}

    # ------------------------------------------------------------ coverage

    def _coverage_command(self, toolchain: str, executable: str, build: dict[str, Any]) -> list[str]:
        out = f"{cov.SCRATCH_BUILD}/program"
        if toolchain == "ada":
            return [executable, "-q", "-o", out, *build["sources"], "-cargs", "-O0", "--coverage",
                    "-fcondition-coverage", "-largs", "--coverage"]
        if toolchain == "fortran":
            return [executable, "-O0", "--coverage", "-fcondition-coverage", "-o", out, *build["sources"]]
        standard = build.get("standard") or DEFAULT_STANDARD[toolchain]
        return [executable, f"-std={standard}", "-O0", "--coverage", "-fcondition-coverage",
                *[f"-I{d}" for d in build.get("include_dirs", [])], *[f"-D{d}" for d in build.get("defines", [])],
                *build["sources"], "-o", out]

    def _record(self, snapshot_id: str, observation_type: str, observation: dict[str, Any], tool_ref: str,
                environment: dict[str, Any], artifact_refs: list[str] | None = None) -> str:
        return self._send("evidence.record_execution", {
            "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": tool_ref,
            "environment_digest": digest_json(environment), "observation_type": observation_type,
            "observation": observation, "artifact_refs": artifact_refs or []})["evidence_id"]

    def _cleanup(self, work: Path) -> None:
        """Sandbox directories are removed by the backend that made them, then the job folder."""
        destroy = getattr(self.backend, "destroy", None)
        for name in ("run", "gcov-run"):
            if destroy is not None and (work / name).exists():
                destroy(work / name)
        shutil.rmtree(work, ignore_errors=True)

    def coverage(self, actor: ActorContext, snapshot_id: str | None = None, *, accept: bool = False) -> dict[str, Any]:
        snapshot_id = snapshot_id or self.latest_snapshot()
        criterion, notices = self.criterion_for(actor, snapshot_id, accept=accept)
        build = criterion.parameters["project_build"]
        toolchain = build["toolchain"]
        base = {"step": "coverage", "notices": notices, "toolchain": toolchain}
        if toolchain not in COVERAGE:
            return {**base, "result": "not_applicable",
                    "reason": f"GCC coverage covers Ada, Fortran, C and C++; this project is {toolchain}"}
        chain = TOOLCHAINS[toolchain]
        try:
            compiler = self.registry.resolve(chain.compiler)
            gcov = self.registry.resolve(GCOV_TOOLS[chain.gcov or ""])
        except (ForgeError, KeyError) as error:
            return {**base, "result": "not_run", "reason": f"needs GCC 14 and gcov-14: {error}"}
        caps = self.backend.capabilities()
        if not caps.available:
            return {**base, "result": "not_run", "reason": f"isolation backend unavailable: {caps.unavailable_reason}"}
        suite = self.assessment.load_suite(criterion.parameters["suite_artifact_id"],
                                           criterion.parameters["suite_digest"])
        problems = cov.integrity_problems(self._files(snapshot_id, cov.CODE_SUFFIXES))
        command = self._coverage_command(toolchain, compiler.executable, build)
        python_path, python_dirs = self.assessment.python_path, self.assessment.python_paths
        work = self.assessment.work_root / new_id("job")
        case_dir, gcov_dir = work / "case", work / "gcov"
        case_dir.mkdir(parents=True)
        try:
            spec = {"build_dir": cov.SCRATCH_BUILD, "compile": command,
                    "entry": [cov.SCRATCH_BUILD + "/program" if part == "/input/program" else part
                              for part in suite.entrypoint],
                    "cases": [{"case_id": c.case_id, "args": c.args, "stdin_b64": c.stdin_b64, "files": c.files}
                              for c in suite.cases],
                    "env": {"LC_ALL": "C", "TMPDIR": "/tmp", "HOME": "/scratch", "PATH": "/usr/bin:/bin"},
                    "build_timeout_s": 300, "case_timeout_s": max(1, suite.case_wall_ms // 1000)}
            (case_dir / "driver.py").write_text(cov.DRIVER, encoding="utf-8")
            (case_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            for path in (work, case_dir, case_dir / "driver.py", case_dir / "spec.json"):
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            wall = min(30 * 60_000, 300_000 + len(suite.cases) * suite.case_wall_ms)
            limits = self.assessment.limits.model_copy(update={"wall_ms": wall, "cpu_seconds": max(120, wall // 1000),
                                                               "output_bytes": 64 * 1024 * 1024, "output_files": 5000})
            snapshot_dir = self.assessment._materialize(snapshot_id)
            environment = {"compiler": compiler.executable_digest, "gcov": gcov.executable_digest, "command": command,
                           "suite": criterion.parameters["suite_digest"]}
            try:
                observation = self.backend.run(SandboxSpec(
                    job_key="coverage", input_dir=str(snapshot_dir), case_dir=str(case_dir), cwd="/input",
                    argv=[python_path, "-I", "/case/driver.py", "/case/spec.json"], limits=limits,
                    toolchain_paths=[*python_dirs, *compiler.toolchain_paths], env=spec["env"]), work / "run")
                collected = collect_outputs(Path(observation.output_dir), self.blobs, max_files=5000,
                                            max_bytes=limits.output_bytes) if observation.output_dir else None
            finally:
                shutil.rmtree(snapshot_dir, ignore_errors=True)
            files = {f.path: f.digest for f in collected.files} if collected is not None else {}
            driver = json.loads(self.blobs.read_bytes(files["driver.json"], max_bytes=4 * 1024 * 1024)) \
                if "driver.json" in files else None
            if driver is None or not driver.get("build") or driver["build"].get("exit_code") != 0:
                reason = ("the instrumented build failed: " + (driver or {}).get("build", {}).get("stderr_tail", "")[-800:]
                          if driver else f"the coverage job ended without a result ({observation.terminated_by})")
                if "-fcondition-coverage" in reason:
                    reason += " (MC/DC needs GCC 14 or newer)"
                evidence = self._record(snapshot_id, "structural_coverage_error", {"reason": reason[:2000]},
                                        f"{compiler.tool_id}@{compiler.version}", environment)
                return {**base, "result": "error", "reason": reason, "evidence_id": evidence}
            counters = {path.removeprefix("counters/"): digest for path, digest in files.items()
                        if path.startswith("counters/")}
            for relative, digest in counters.items():
                target = gcov_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.blobs.read_bytes(digest, max_bytes=64 * 1024 * 1024))
            gcov_dir.mkdir(parents=True, exist_ok=True)
            for path in [gcov_dir, *gcov_dir.rglob("*")]:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            data_files = sorted(p for p in counters if p.endswith(".gcda"))
            documents: list[dict[str, Any]] = []
            report_digest, report_size = None, 0
            if data_files:
                report = self.backend.run(SandboxSpec(
                    job_key="gcov", input_dir=str(gcov_dir), cwd="/input",
                    argv=["/bin/sh", "-c", 'exec "$0" "$@" > /output/report.json', gcov.executable, "--conditions",
                          "--branch-probabilities", "--json-format", "--stdout", *data_files],
                    limits=limits, toolchain_paths=gcov.toolchain_paths, env={"LC_ALL": "C"}), work / "gcov-run")
                reported = collect_outputs(Path(report.output_dir), self.blobs, max_files=1,
                                           max_bytes=limits.output_bytes) if report.output_dir else None
                if reported is not None and reported.files:
                    report_digest, report_size = reported.files[0].digest, reported.files[0].size
                    documents = cov.parse_reports(self.blobs.read_bytes(report_digest, max_bytes=limits.output_bytes)
                                                  .decode("utf-8", "replace"))
            snapshot_paths = {e.path for e in self._manifest(snapshot_id).entries}
            summary = cov.summarize(documents, snapshot_paths)
            artifact_refs = []
            if report_digest is not None:
                artifact_refs.append(self._send("artifact.register", {
                    "digest": report_digest, "size": report_size, "content_type": "application/json",
                    "artifact_kind": "gcov_report", "sensitivity": "project"})["artifact_id"])
            exits = {c["case_id"]: c.get("exit_code", "timeout") for c in driver.get("cases", [])}
            observation_body = {
                **{k: summary[k] for k in ("statement_percent", "decision_percent", "mcdc_percent", "totals", "files",
                                           "excluded_generated", "decision_measure", "mcdc_measure")},
                "uncovered_conditions": summary["uncovered_conditions"][:100],
                "uncovered_branch_lines": summary["uncovered_branch_lines"][:100],
                "uncovered_lines": {k: v[:50] for k, v in list(summary["uncovered_lines"].items())[:50]},
                "integrity_problems": problems, "toolchain": toolchain, "compiler": compiler.executable,
                "compiler_digest": compiler.executable_digest, "gcov_digest": gcov.executable_digest,
                "suite_digest": criterion.parameters["suite_digest"], "cases_run": len(exits),
                "case_exit_codes": dict(list(exits.items())[:200]),
                "limitations": ["coverage counters are written by the instrumented program itself",
                                "measured on an instrumented -O0 build of the same sources and accepted tests"],
            }
            evidence_id = self._record(snapshot_id, "structural_coverage", observation_body,
                                       f"{compiler.tool_id}@{compiler.version}", environment, artifact_refs)
            result = "inconclusive" if problems else "measured"
            return {**base, "result": result, "evidence_id": evidence_id, "integrity_problems": problems,
                    **{k: summary[k] for k in ("statement_percent", "decision_percent", "mcdc_percent", "totals",
                                               "excluded_generated")},
                    "uncovered_conditions": summary["uncovered_conditions"][:20],
                    "uncovered_branch_lines": summary["uncovered_branch_lines"][:20], "cases_run": len(exits)}
        finally:
            self._cleanup(work)

    # ------------------------------------------------------------- analyze

    def analyze(self, actor: ActorContext, snapshot_id: str | None = None, *, accept: bool = False) -> dict[str, Any]:
        snapshot_id = snapshot_id or self.latest_snapshot()
        criterion, notices = self.criterion_for(actor, snapshot_id, accept=accept)
        build = criterion.parameters["project_build"]
        toolchain = build["toolchain"]
        base = {"step": "analyze", "notices": notices, "toolchain": toolchain}
        if toolchain == "python":
            try:
                ruff = self.registry.resolve("ruff")
            except ForgeError:
                return {**base, "result": "not_run", "reason": "ruff is not installed"}
            outcome = self.assessment.analyze(snapshot_id, ruff)
            return {**base, "result": "analyzed" if outcome["status"] == "ok" else "error",
                    "analyzer": "ruff", "findings": [f"{f.path}:{f.line}: {f.rule_id} {f.message}"
                                                     for f in outcome.get("findings", [])][:20],
                    "finding_count": len(outcome.get("findings", [])), "evidence_id": outcome["evidence_id"]}
        chain = TOOLCHAINS.get(toolchain)
        if chain is None or chain.analyze_arguments is None:
            return {**base, "result": "not_run", "reason": f"FORGE has no analyzer for {toolchain} yet"}
        try:
            tool = self.registry.resolve(chain.compiler)
        except ForgeError:
            fallback = {"c": "gcc", "cpp": "gxx"}.get(toolchain)
            if fallback is None:
                return {**base, "result": "not_run", "reason": f"{chain.program} is not installed"}
            tool = self.registry.resolve(fallback)
        caps = self.backend.capabilities()
        if not caps.available:
            return {**base, "result": "not_run", "reason": f"isolation backend unavailable: {caps.unavailable_reason}"}
        arguments = list(chain.analyze_arguments)
        if toolchain in NATIVE:
            arguments = [a for a in arguments if not a.startswith("-std=")]
            arguments += [f"-std={build.get('standard') or DEFAULT_STANDARD[toolchain]}",
                          *[f"-I{d}" for d in build.get("include_dirs", [])],
                          *[f"-D{d}" for d in build.get("defines", [])]]
        arguments += ["-fdiagnostics-color=never"] if toolchain in ("c", "cpp", "fortran", "c_arm") else []
        arguments += build["sources"]
        snapshot_dir = self.assessment._materialize(snapshot_id)
        work = self.assessment.work_root / new_id("job")
        limits = self.assessment.limits.model_copy(update={"wall_ms": max(self.assessment.limits.wall_ms, 300_000),
                                                           "cpu_seconds": max(self.assessment.limits.cpu_seconds,
                                                                              240)})
        helpers = {name: os.path.realpath(found) for name, program in chain.helper_env.items()
                   if (found := shutil.which(program))}
        try:
            observation = self.backend.run(SandboxSpec(
                job_key=f"analyze_{toolchain}", input_dir=str(snapshot_dir), cwd="/input",
                argv=["/bin/sh", "-c", _BUILD_SHELL, tool.executable, *arguments], limits=limits,
                toolchain_paths=tool.toolchain_paths,
                env={"LC_ALL": "C", "TMPDIR": "/tmp", "HOME": "/scratch", "PATH": "/usr/bin:/bin", **helpers}), work)
        finally:
            destroy = getattr(self.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        stderr = observation.stderr.decode("utf-8", "replace")
        diagnostics = [d for d in parse_diagnostics(stderr) if d.severity in ("error", "warning")]
        status = "ok" if observation.terminated_by == "exited" else "error"
        evidence_id = self._record(snapshot_id, "analyzer_findings", {
            "status": status, "error": None if status == "ok" else f"analyzer {observation.terminated_by}",
            "finding_count": len(diagnostics), "analyzer": f"{tool.tool_id}@{tool.version}", "arguments": arguments,
            "exit_code": observation.exit_code}, f"{tool.tool_id}@{tool.version}",
            {"tool": tool.executable_digest, "arguments": arguments})
        finding_ids = []
        for item in diagnostics[:500]:
            serious = item.severity == "error" or (item.flag or "").startswith("-Wanalyzer")
            rule = (item.flag or f"compiler-{item.severity}")[:100]
            finding_ids.append(self._send("finding.record", {
                "rule_id": rule, "claim": item.message[:2000], "severity": "high" if serious else "medium",
                "path": item.path, "line": item.line, "snapshot_id": snapshot_id, "evidence_refs": [evidence_id],
                "fingerprint": "sha256:" + hashlib.sha256(json.dumps([rule, item.path, item.message]).encode())
                .hexdigest()})["finding_id"])
        return {**base, "result": "analyzed" if status == "ok" else "error", "analyzer": tool.tool_id,
                "finding_count": len(diagnostics), "evidence_id": evidence_id, "finding_ids": finding_ids,
                "findings": [f"{d.path}:{d.line}: {d.severity}: {d.message}"
                             + (f" [{d.flag}]" if d.flag else "") for d in diagnostics][:20]}

    # ----------------------------------------------------------------- all

    def run(self, actor: ActorContext, step: str = "all", snapshot_id: str | None = None, *,
            accept: bool = False) -> dict[str, Any]:
        """One step, or every step that applies followed by the compliance check. A failing step never hides the rest."""
        snapshot_id = snapshot_id or self.latest_snapshot()
        if step != "all":
            if step not in STEPS:
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"unknown check {step!r}; choose one of {STEPS} or all")
            return {"snapshot_id": snapshot_id, "steps": [getattr(self, step)(actor, snapshot_id, accept=accept)]}
        steps: list[dict[str, Any]] = []
        for name in ("tests", "analyze", "coverage"):
            try:
                steps.append(getattr(self, name)(actor, snapshot_id, accept=accept))
            except ForgeError as error:
                steps.append({"step": name, "result": "error", "reason": error.message})
                if name == "tests" and error.code in (ErrorCode.INVALID_REQUEST, ErrorCode.PERMISSION_DENIED):
                    break                                    # no usable forge.toml: the other steps need it too
            accept = False                                   # accepted at most once per run
        from forge.compliance.report import next_steps
        from forge.compliance.service import ComplianceReport, ComplianceService

        checked = ComplianceService(self.controller).check(snapshot_id)
        report = ComplianceReport.model_validate(checked["report"])
        compliance = {"gate": checked["gate"], "gate_status": report.gate_status, "profile": report.profile.title,
                      "checks": {c: {"status": report.checks[c]["status"], "detail": report.checks[c]["detail"]}
                                 for c in report.profile.gate_checks},
                      "next": next_steps(report)}
        return {"snapshot_id": snapshot_id, "steps": steps, "compliance": compliance}

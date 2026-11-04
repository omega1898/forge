"""The Java pack, the COBOL/JCL pack's status, and the pack status table (ticket F19).

Java: `javac` and `java` are admitted by digest. The JDK reads configuration
through symlinks that leave its install directory (on Debian, into
/etc/java-NN-openjdk); admission finds those directories and mounts exactly
them read-only, so the sandbox never sees the rest of the host's /etc.
Candidates are compiled inside the sandbox; the produced classes are
collected and the protected suite runs them through the `{java}`
placeholder. The JVM runs with the serial collector and a single compiler
tier to stay inside the sandbox's process limit.

COBOL/JCL: no COBOL compiler (GnuCOBOL `cobc`) or JCL runner is installed
here, so the pack reports itself unavailable and its checks are `not_run`.
Its deterministic half works without a compiler: copybook layouts, EBCDIC,
zoned and packed decimals, COBOL rounding and exact record comparison live
in `forge.packs.cobol_layout`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from forge.assessment.evaluator import SuiteRun, run_suite
from forge.contracts.base import StrictModel
from forge.contracts.digests import digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.execution.collector import collect_outputs
from forge.packs.python_pack import PackDescriptor
from forge.platforms import environment_of, where
from forge.sandbox.base import SandboxSpec
from forge.tools.registry import ToolDefinition, ToolRegistry

JAVA_TOOL_REF = "forge.java_cases@1"
JVM_OPTIONS = "-XX:+UseSerialGC -XX:TieredStopAtLevel=1 -XX:CICompilerCount=1 -Xss1m"

JAVA_PACK = PackDescriptor(
    pack_id="java", version=1, languages=["java"], tools=["javac", "java"], evaluators=["java_cases"],
    task_templates=["build_tool", "review", "bug_fix", "test_strategy"], selected_by_facts=["language.java"],
    limitations=["compiles with javac only; Maven and Gradle builds need their repositories staged offline",
                 "the JVM runs with reduced threads to fit the sandbox process limit",
                 "the JDK's configuration directory outside /usr is mounted read-only into the sandbox"])
COBOL_PACK = PackDescriptor(
    pack_id="cobol", version=1, languages=["cobol", "jcl"], tools=["cobc"], evaluators=["record_comparison"],
    task_templates=["migration"], selected_by_facts=["language.cobol"],
    limitations=["no COBOL compiler or JCL runner on this host: COBOL programs cannot be built or run",
                 "copybook layouts, EBCDIC, zoned and packed decimals and record comparison work without one",
                 "REDEFINES, OCCURS DEPENDING ON, binary and floating-point usages are refused"])
OTHER_PACKS = {
    "c": ("gcc", "clang"), "cpp": ("g++", "clang++"), "java": ("javac", "java"), "cobol": ("cobc",),
    "bash": ("bash",), "powershell": ("pwsh",), "ocaml": ("ocamlfind", "ocaml"), "python": ("python3",),
}


def pack_status() -> list[dict[str, Any]]:
    """Which packs this host can actually run, by whether their tools exist. Never assumed."""
    rows = []
    for pack_id, tools in sorted(OTHER_PACKS.items()):
        found = {tool: shutil.which(tool) for tool in tools}
        available = any(found.values()) if pack_id not in ("cobol",) else all(found.values())
        missing = sorted(tool for tool, path in found.items() if path is None)
        rows.append({"pack_id": pack_id, "available": available,
                     "reason": "" if available else f"not installed: {', '.join(missing)}",
                     "tools": {tool: path for tool, path in found.items() if path}})
    return rows


def _external_config_dirs(java_home: Path, limit: int = 2000) -> list[str]:
    """Top-level directories outside the system tree that the JDK's symlinks point into."""
    found: set[str] = set()
    seen = 0
    for root, _, files in os.walk(java_home):
        for name in files:
            seen += 1
            if seen > limit:
                return sorted(found)
            path = Path(root) / name
            if path.is_symlink():
                target = os.path.realpath(path)
                if not target.startswith(("/usr/", "/lib/", "/bin/")) and target.startswith("/etc/"):
                    parts = Path(target).parts
                    if len(parts) >= 3:
                        found.add(str(Path(*parts[:3])))
    return sorted(found)


def admit_java_tools(registry: ToolRegistry) -> list[str]:
    admitted = []
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not (javac and java):
        return admitted
    real_javac = Path(os.path.realpath(javac))
    java_home = real_javac.parent.parent
    try:
        done = subprocess.run([str(real_javac), "-version"], capture_output=True, text=True, timeout=30,
                              env={"PATH": "/usr/bin:/bin"}, check=False)
        version = (done.stdout or done.stderr).strip().split()[-1]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return admitted
    paths = ([] if str(java_home).startswith("/usr/") else [str(java_home)]) + _external_config_dirs(java_home)
    registry.admit("javac", real_javac, version=version, toolchain_paths=paths, side_effect_class="scratch_only")
    registry.admit("java", Path(os.path.realpath(java)), version=version, toolchain_paths=paths)
    return ["javac", "java"]


class JavaBuildSpec(StrictModel):
    sources: list[str] = Field(min_length=1, max_length=200)
    release: int = Field(default=17, ge=8, le=25)
    lint_as_errors: bool = True

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: list[str]) -> list[str]:
        for path in value:
            check_relative_path(path)
            if not path.endswith(".java") or path.startswith("-"):
                raise ValueError(f"{path!r} is not a Java source path")
        return value


class JavaAssessor:
    def __init__(self, service, registry: ToolRegistry) -> None:  # noqa: ANN001 - AssessmentService
        self.service, self.registry = service, registry

    def assess(self, criterion_id: str, snapshot_id: str, *, on_case=None) -> dict[str, Any]:  # noqa: ANN001
        service = self.service
        criterion = service.controller.store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "java_cases":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not a java_cases criterion")
        spec = JavaBuildSpec.model_validate(criterion.parameters["build"])
        suite = service.load_suite(criterion.parameters["suite_artifact_id"], criterion.parameters["suite_digest"])
        if suite.entrypoint[:1] != ["{java}"]:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "a Java suite runs through the {java} placeholder")
        caps = service.backend.capabilities()
        criterion_ref = {"id": criterion.criterion_id, "revision": criterion.revision}

        def record(result: str, limitations: list[str], evidence: list[str], summary: dict[str, Any]) -> str:
            return service._send("assessment.record", {
                "criterion_ref": criterion_ref, "snapshot_id": snapshot_id, "evidence_refs": evidence,
                "assessment_result": result, "assessor_ref": JAVA_TOOL_REF, "limitations": limitations,
                "summary": {**summary, "environment": environment_of(caps), "executed_on": where(caps)}}
            ).result["assessment_id"]

        if criterion.required_environments:
            plan = service.platform_plan(criterion, snapshot_id, JAVA_TOOL_REF)
            if not plan["run_here"]:
                return {"assessment_id": plan["not_run_ids"][-1], "result": "not_run"}
        try:
            javac, java = self.registry.resolve("javac"), self.registry.resolve("java")
        except ForgeError as error:
            return {"assessment_id": record("not_run", [str(error)], [], {}), "result": "not_run"}
        if not caps.available:
            reason = f"isolation backend unavailable: {caps.unavailable_reason}"
            return {"assessment_id": record("not_run", [reason], [], {}), "result": "not_run"}

        build = self._build(spec, javac, snapshot_id)
        if build["outcome"] != "built":
            result = "fail" if build["outcome"] == "compile_failed" else "error"
            return {"assessment_id": record(result, [build["reason"]], [build["evidence_id"]], {"build": build["outcome"]}),
                    "result": result, **build}
        run_dir = service.work_root / f"{new_id('job')}-classes"
        try:
            for item in build["files"]:
                target = run_dir / "classes" / item.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "wb") as handle:
                    for chunk in service.controller.blobs.iter_verified(item.digest):
                        handle.write(chunk)
                os.chmod(target, 0o644)
            for directory in [run_dir, *[p for p in run_dir.rglob("*") if p.is_dir()]]:
                os.chmod(directory, 0o755)
            job_id = new_id("job")
            run: SuiteRun = run_suite(
                suite, run_dir, service.backend, service.work_root / job_id, python_path=service.python_path,
                limits=service.limits, toolchain_paths=java.toolchain_paths,
                env={"JAVA_TOOL_OPTIONS": JVM_OPTIONS}, placeholders={"{java}": java.executable},
                on_observation=on_case)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        evidence_id = service._send("evidence.record_execution", {
            "job_id": job_id, "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": JAVA_TOOL_REF,
            "environment_digest": run.environment_digest, "evaluator_digest": run.evaluator_digest,
            "observation_type": "java_case_results",
            "observation": {"counts": run.counts, "java": f"{java.tool_id}@{java.version}",
                            "failed_cases": [c.case_id for c in run.cases if c.outcome == "fail"][:50]},
        }).result["evidence_id"]
        assessment_id = record(run.result, run.limitations, [build["evidence_id"], evidence_id], {"counts": run.counts})
        return {"assessment_id": assessment_id, "result": run.result, "run": run, "build_evidence_id": build["evidence_id"]}

    def _build(self, spec: JavaBuildSpec, javac: ToolDefinition, snapshot_id: str) -> dict[str, Any]:
        service = self.service
        snapshot_dir = service._materialize(snapshot_id)
        work = service.work_root / new_id("job")
        args = [f"-J{option}" for option in JVM_OPTIONS.split()] + ["--release", str(spec.release),
                                                                     "-encoding", "UTF-8", "-Xlint:all"]
        if spec.lint_as_errors:
            args.append("-Werror")
        argv = [javac.executable, *args, "-d", "/output", *spec.sources]
        limits = service.limits.model_copy(update={"wall_ms": max(service.limits.wall_ms, 120_000),
                                                   "cpu_seconds": max(service.limits.cpu_seconds, 90)})
        collected = None
        try:
            observation = service.backend.run(SandboxSpec(
                job_key="java_build", input_dir=str(snapshot_dir), cwd="/input", argv=argv, limits=limits,
                toolchain_paths=javac.toolchain_paths, env={"LC_ALL": "C"}), work)
            if observation.terminated_by == "exited" and observation.exit_code == 0:
                collected = collect_outputs(Path(observation.output_dir), service.controller.blobs, max_files=2000,
                                            max_bytes=limits.output_bytes)
        finally:
            destroy = getattr(service.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        stderr = observation.stderr.decode("utf-8", "replace")
        diagnostics = [{"path": m[1], "line": int(m[2]), "severity": m[3], "message": m[4][:300]}
                       for m in re.finditer(r"^([^:\n]+\.java):(\d+): (error|warning): (.*)$", stderr, re.M)]
        if observation.terminated_by != "exited":
            outcome, reason = "error", f"javac {observation.terminated_by}"
        elif observation.exit_code != 0:
            outcome, reason = "compile_failed", "the candidate does not compile with the protected options"
        elif collected is None or not collected.accepted or not collected.files or \
                not all(item.path.endswith(".class") for item in collected.files):
            outcome, reason = "error", "the build output was rejected or contains files other than classes"
        else:
            outcome, reason = "built", "compiled"
        evidence_id = service._send("evidence.record_execution", {
            "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": snapshot_id,
            "tool_ref": f"{javac.tool_id}@{javac.version}",
            "environment_digest": digest_json({"javac": javac.executable_digest, "args": args}),
            "observation_type": "java_build",
            "observation": {"outcome": outcome, "exit_code": observation.exit_code, "arguments": args,
                            "diagnostics": diagnostics[:50], "stderr_tail": stderr[-1000:]},
        }).result["evidence_id"]
        return {"outcome": outcome, "reason": reason, "diagnostics": diagnostics, "evidence_id": evidence_id,
                "files": collected.files if collected is not None and outcome == "built" else []}

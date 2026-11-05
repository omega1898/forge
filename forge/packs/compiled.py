"""Compiled-language packs on one assessor: COBOL, OCaml, and C for an emulated ARM target.

Each toolchain compiles candidate source inside the sandbox, in a scratch
copy of the snapshot (some compilers write intermediate files next to the
sources, and the snapshot is mounted read-only). The single produced
program is collected through the output collector and the protected suite
runs it. Compiler, flags, sources and the program are recorded by digest.

Evidence carries a tier. Programs for the host run natively (`host`); the
ARM build runs under QEMU user-mode emulation (`emulator`). A criterion that
asks for another tier is recorded `not_run`: an emulator pass is never a
hardware pass (F22).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from forge.assessment.evaluator import SuiteRun, run_suite
from forge.contracts.base import StrictModel
from forge.contracts.digests import digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.execution.collector import collect_outputs
from forge.packs.embedded import Tier, tier_problem
from forge.platforms import environment_of, where
from forge.sandbox.base import SandboxSpec
from forge.tools.registry import ToolDefinition, ToolRegistry

COMPILED_TOOL_REF = "forge.compiled_cases@1"
# Copies the snapshot to scratch, then runs the compiler with the arguments
# that follow; nothing is interpolated into the shell text.
_BUILD_SHELL = 'cp -R /input/. /scratch/build && cd /scratch/build && exec "$0" "$@"'


class Toolchain(StrictModel):
    pack_id: str
    compiler: str                    # tool id in the registry
    program: str                     # executable name to admit
    suffixes: tuple[str, ...]
    arguments: list[str]             # before the sources; "{output}" is the program path
    after_sources: list[str] = Field(default_factory=list)
    runner: str | None = None        # tool id that runs the program (an emulator), else run natively
    tier: Tier = "host"
    env: dict[str, str] = Field(default_factory=dict)
    # Configuration the compiler reads outside /usr; mounted read-only, nothing else of /etc is visible.
    config_paths: list[str] = Field(default_factory=list)
    # Environment variables naming helper programs, resolved to real paths at build time
    # (for example COBOL's C compiler, whose /usr/bin/gcc is a symlink into /etc/alternatives).
    helper_env: dict[str, str] = Field(default_factory=dict)


TOOLCHAINS: dict[str, Toolchain] = {
    "cobol": Toolchain(pack_id="cobol", compiler="cobc", program="cobc", suffixes=(".cbl", ".cob"),
                       arguments=["-x", "-O", "-o", "{output}"], config_paths=["/etc/gnucobol"],
                       helper_env={"COB_CC": "gcc"}),
    "ocaml": Toolchain(pack_id="ocaml", compiler="ocamlfind", program="ocamlfind", suffixes=(".ml",),
                       arguments=["ocamlopt", "-package", "str", "-linkpkg", "-o", "{output}"],
                       config_paths=["/etc/ocamlfind.conf"]),
    "c_arm": Toolchain(pack_id="c_arm", compiler="arm_gcc", program="arm-linux-gnueabihf-gcc", suffixes=(".c",),
                       arguments=["-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-static", "-o", "{output}"],
                       runner="qemu_arm", tier="emulator"),
}
# Newer distributions ship the static emulator as plain qemu-arm (package qemu-user).
RUNNERS = {"qemu_arm": ("qemu-arm-static", "qemu-arm")}


def admit_compiled_tools(registry: ToolRegistry) -> list[str]:
    admitted = []
    for toolchain in TOOLCHAINS.values():
        found = shutil.which(toolchain.program)
        if found:
            registry.admit(toolchain.compiler, Path(os.path.realpath(found)), version="host",
                           toolchain_paths=[path for path in toolchain.config_paths if os.path.exists(path)],
                           side_effect_class="scratch_only")
            admitted.append(toolchain.compiler)
    for tool_id, programs in RUNNERS.items():
        found = next((path for program in programs if (path := shutil.which(program))), None)
        if found:
            registry.admit(tool_id, Path(os.path.realpath(found)), version="host")
            admitted.append(tool_id)
    return admitted


class CompiledBuild(StrictModel):
    toolchain: Literal["cobol", "ocaml", "c_arm"]
    sources: list[str] = Field(min_length=1, max_length=64)

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: list[str]) -> list[str]:
        for path in value:
            check_relative_path(path)
            if path.startswith("-"):
                raise ValueError(f"{path!r} would be read as an option")
        return value


class CompiledAssessor:
    def __init__(self, service, registry: ToolRegistry) -> None:  # noqa: ANN001 - AssessmentService
        self.service, self.registry = service, registry

    def assess(self, criterion_id: str, snapshot_id: str, *, on_case=None) -> dict[str, Any]:  # noqa: ANN001
        service = self.service
        criterion = service.controller.store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "compiled_cases":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not a compiled_cases criterion")
        build = CompiledBuild.model_validate(criterion.parameters["build"])
        toolchain = TOOLCHAINS[build.toolchain]
        for source in build.sources:
            if not source.endswith(toolchain.suffixes):
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"{source} is not a {toolchain.pack_id} source")
        suite = service.load_suite(criterion.parameters["suite_artifact_id"], criterion.parameters["suite_digest"])
        if suite.entrypoint[:1] != ["/input/program"]:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "a compiled suite runs /input/program")
        caps = service.backend.capabilities()
        criterion_ref = {"id": criterion.criterion_id, "revision": criterion.revision}

        def record(result: str, limitations: list[str], evidence: list[str], summary: dict[str, Any]) -> str:
            return service._send("assessment.record", {
                "criterion_ref": criterion_ref, "snapshot_id": snapshot_id, "evidence_refs": evidence,
                "assessment_result": result, "assessor_ref": COMPILED_TOOL_REF, "limitations": limitations,
                "summary": {**summary, "tier": toolchain.tier, "environment": environment_of(caps),
                            "executed_on": where(caps)}}).result["assessment_id"]

        required_tier = criterion.parameters.get("required_tier")
        if required_tier and tier_problem(required_tier, toolchain.tier):
            reason = tier_problem(required_tier, toolchain.tier)
            return {"assessment_id": record("not_run", [reason], [], {}), "result": "not_run", "reason": reason}
        try:
            compiler = self.registry.resolve(toolchain.compiler)
            runner = self.registry.resolve(toolchain.runner) if toolchain.runner else None
        except ForgeError as error:
            return {"assessment_id": record("not_run", [str(error)], [], {}), "result": "not_run", "reason": str(error)}
        if not caps.available:
            reason = f"isolation backend unavailable: {caps.unavailable_reason}"
            return {"assessment_id": record("not_run", [reason], [], {}), "result": "not_run", "reason": reason}

        built = self._build(toolchain, compiler, build.sources, snapshot_id)
        if built["outcome"] != "built":
            result = "fail" if built["outcome"] == "compile_failed" else "error"
            return {"assessment_id": record(result, [built["reason"]], [built["evidence_id"]], {"build": built["outcome"]}),
                    "result": result, **built}
        run_dir = service.work_root / f"{new_id('job')}-program"
        run_dir.mkdir(parents=True)
        os.chmod(run_dir, 0o755)
        with open(run_dir / "program", "wb") as handle:
            for chunk in service.controller.blobs.iter_verified(built["program_digest"]):
                handle.write(chunk)
        os.chmod(run_dir / "program", 0o755)
        if runner is not None:
            # Run through the emulator: the suite's /input/program becomes an argument to it.
            suite = suite.model_copy(update={"entrypoint": [runner.executable, *suite.entrypoint]})
        job_id = new_id("job")
        try:
            run: SuiteRun = run_suite(suite, run_dir, service.backend, service.work_root / job_id,
                                      python_path=service.python_path, limits=service.limits,
                                      toolchain_paths=[*compiler.toolchain_paths], env=toolchain.env,
                                      on_observation=on_case)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        evidence_id = service._send("evidence.record_execution", {
            "job_id": job_id, "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": COMPILED_TOOL_REF,
            "environment_digest": run.environment_digest, "evaluator_digest": run.evaluator_digest,
            "observation_type": "compiled_case_results",
            "observation": {"counts": run.counts, "tier": toolchain.tier, "toolchain": toolchain.pack_id,
                            "admitted_suite_digest": criterion.parameters["suite_digest"],
                            "program_digest": built["program_digest"],
                            "runner": runner.executable_digest if runner else None,
                            "failed_cases": [c.case_id for c in run.cases if c.outcome == "fail"][:50]},
        }).result["evidence_id"]
        limitations = list(run.limitations)
        if toolchain.tier == "emulator":
            limitations.append("ran under user-mode emulation; timing, peripherals and hardware faults are not modelled")
        assessment_id = record(run.result, limitations, [built["evidence_id"], evidence_id], {"counts": run.counts})
        return {"assessment_id": assessment_id, "result": run.result, "run": run, "tier": toolchain.tier,
                "build_evidence_id": built["evidence_id"]}

    def _build(self, toolchain: Toolchain, compiler: ToolDefinition, sources: list[str],
               snapshot_id: str) -> dict[str, Any]:
        service = self.service
        snapshot_dir = service._materialize(snapshot_id)
        work = service.work_root / new_id("job")
        arguments = [a.replace("{output}", "/output/program") for a in toolchain.arguments] + sources + \
            toolchain.after_sources
        limits = service.limits.model_copy(update={"wall_ms": max(service.limits.wall_ms, 180_000),
                                                   "cpu_seconds": max(service.limits.cpu_seconds, 120)})
        helpers = {name: os.path.realpath(found) for name, program in toolchain.helper_env.items()
                   if (found := shutil.which(program))}
        collected = None
        try:
            observation = service.backend.run(SandboxSpec(
                job_key=f"build_{toolchain.pack_id}", input_dir=str(snapshot_dir), cwd="/input",
                argv=["/bin/sh", "-c", _BUILD_SHELL, compiler.executable, *arguments], limits=limits,
                toolchain_paths=compiler.toolchain_paths,
                env={"LC_ALL": "C", "TMPDIR": "/tmp", "HOME": "/scratch", **helpers}), work)
            if observation.terminated_by == "exited" and observation.exit_code == 0:
                collected = collect_outputs(Path(observation.output_dir), service.controller.blobs, max_files=1,
                                            max_bytes=limits.output_bytes)
        finally:
            destroy = getattr(service.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        stderr = observation.stderr.decode("utf-8", "replace")
        program_digest = None
        if observation.terminated_by != "exited":
            outcome, reason = "error", f"compiler {observation.terminated_by}"
        elif observation.exit_code != 0:
            outcome, reason = "compile_failed", "the candidate does not compile"
        elif collected is None or not collected.accepted or [f.path for f in collected.files] != ["program"]:
            outcome, reason = "error", "the build did not produce exactly one program"
        else:
            outcome, reason = "built", "compiled"
            program_digest = collected.files[0].digest
        evidence_id = service._send("evidence.record_execution", {
            "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": snapshot_id,
            "tool_ref": f"{compiler.tool_id}@{compiler.version}",
            "environment_digest": digest_json({"compiler": compiler.executable_digest, "arguments": arguments}),
            "observation_type": f"{toolchain.pack_id}_build",
            "observation": {"outcome": outcome, "exit_code": observation.exit_code, "arguments": arguments,
                            "compiler_digest": compiler.executable_digest, "program_digest": program_digest,
                            "stderr_tail": stderr[-1500:]},
        }).result["evidence_id"]
        return {"outcome": outcome, "reason": reason, "program_digest": program_digest, "evidence_id": evidence_id,
                "stderr": stderr}

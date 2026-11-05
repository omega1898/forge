"""Run the checkers a set of standard profiles calls for, in the sandbox, and merge their findings.

Each tool runs in its own sandbox job on a scratch copy of the snapshot, so
its exit status and output stay separate. Output goes through the tool's
adapter, then every finding is classified against the chosen profiles,
anchored to a GitHub-compatible line hash and merged across tools. The
Power of Ten analyzer reads the source text directly (it runs no code).

A tool that is not installed is reported as missing; a tool whose output
cannot be read, or that times out, makes the scan `error`. Neither is ever
reported as a clean result.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.contracts.errors import ForgeError
from forge.contracts.ids import new_id
from forge.quality import adapters, knowledge, power_of_ten
from forge.quality import registry as standards
from forge.quality.findings import QualityFinding, anchor, merge, to_sarif
from forge.sandbox.base import SandboxSpec
from forge.tools.registry import ToolRegistry

_SHELL = 'cp -R /input/. /scratch/build && cd /scratch/build && exec "$0" "$@"'
SCRIPT_SUFFIXES = (".sh", ".bash")
DEFAULT_STANDARD = {"c": "c11", "cpp": "c++17", "c_arm": "c11"}
QUALITY_TOOLS = {"clang_tidy": ("clang-tidy-18", "clang-tidy"), "cppcheck": ("cppcheck",),
                 "shellcheck": ("shellcheck",)}


def admit_quality_tools(registry: ToolRegistry) -> list[str]:
    admitted = []
    for tool_id, programs in QUALITY_TOOLS.items():
        found = next((path for program in programs if (path := shutil.which(program))), None)
        if found is None:
            continue
        try:
            registry.resolve(tool_id)
        except ForgeError:
            registry.admit(tool_id, Path(os.path.realpath(found)), version=Path(found).name)
        admitted.append(tool_id)
    return admitted


@dataclass
class ScanResult:
    findings: list[QualityFinding]
    profiles: list[str]
    tools: dict[str, str] = field(default_factory=dict)          # tool -> version used
    runs: list[dict[str, Any]] = field(default_factory=list)     # per tool: status, exit code, problem
    missing: list[str] = field(default_factory=list)
    attestations: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "error" if any(r["status"] == "error" for r in self.runs) else "ok"

    def sarif(self) -> dict[str, Any]:
        return to_sarif(self.findings, tools=self.tools)


class QualityRunner:
    def __init__(self, check_service) -> None:  # noqa: ANN001 - forge.check.service.CheckService
        self.service = check_service
        self.assessment = check_service.assessment
        self.backend = check_service.backend
        self.registry = check_service.registry
        admit_quality_tools(self.registry)

    def _tool(self, tool_id: str):  # noqa: ANN202 - ToolDefinition | None
        try:
            return self.registry.resolve(tool_id)
        except ForgeError:
            return None

    def _run(self, snapshot_dir: Path, name: str, argv: list[str], toolchain_paths: list[str],
             case_dir: Path | None = None, env: dict[str, str] | None = None) -> tuple[str, str, Any]:
        work = self.assessment.work_root / new_id("job")
        limits = self.assessment.limits.model_copy(update={"wall_ms": max(self.assessment.limits.wall_ms, 300_000),
                                                           "cpu_seconds": max(self.assessment.limits.cpu_seconds, 240),
                                                           "stdout_bytes": 16 * 1024 * 1024,
                                                           "stderr_bytes": 16 * 1024 * 1024})
        try:
            observation = self.backend.run(SandboxSpec(
                job_key=re.sub(r"[^a-z0-9_]", "_", f"quality_{name}".lower())[:60], input_dir=str(snapshot_dir), cwd="/input",
                case_dir=str(case_dir) if case_dir else None, argv=["/bin/sh", "-c", _SHELL, *argv], limits=limits,
                toolchain_paths=toolchain_paths,
                env={"LC_ALL": "C", "TMPDIR": "/tmp", "HOME": "/scratch", "PATH": "/usr/bin:/bin", **(env or {})}),
                work)
        finally:
            destroy = getattr(self.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(work, ignore_errors=True)
        return (observation.stdout.decode("utf-8", "replace"), observation.stderr.decode("utf-8", "replace"),
                observation)

    def scan(self, snapshot_id: str, build: dict[str, Any], chosen: list[str] | None) -> ScanResult:
        toolchain = build["toolchain"]
        profiles = standards.resolve(toolchain, chosen)
        result = ScanResult(findings=[], profiles=[p.profile_id for p in profiles])
        wanted = {tool for profile in profiles for tool in profile.tools}
        files = self.service._files(snapshot_id)
        sources = list(build.get("sources", []))
        language_std = build.get("standard") or DEFAULT_STANDARD.get(toolchain, "")
        includes = [f"-I{d}" for d in build.get("include_dirs", [])] + [f"-D{d}" for d in build.get("defines", [])]
        raw: list[QualityFinding] = []
        for profile in profiles:
            result.attestations.extend(profile.attestations)
        snapshot_dir = self.assessment._materialize(snapshot_id)
        case_dir = None
        try:
            def run(name: str, tool_id: str, argv: list[str], parse, *, use: str = "stdout",  # noqa: ANN001
                    case: Path | None = None) -> None:
                tool = self._tool(tool_id)
                if tool is None:
                    result.missing.append(name)
                    return
                stdout, stderr, observation = self._run(snapshot_dir, name, [tool.executable, *argv],
                                                        list(tool.toolchain_paths), case)
                result.tools[name] = tool.version
                record = {"tool": name, "exit_code": observation.exit_code, "status": "ok"}
                if observation.terminated_by != "exited":
                    record.update(status="error", problem=f"{name} {observation.terminated_by}")
                else:
                    try:
                        raw.extend(parse(stdout if use == "stdout" else stderr))
                    except adapters.AdapterError as error:
                        record.update(status="error", problem=str(error)[:300])
                result.runs.append(record)

            c_like = toolchain in ("c", "cpp", "c_arm")
            if c_like and "clang-tidy" in wanted and sources:
                checks = sorted({c for p in profiles for c in p.clang_tidy_checks})
                run("clang-tidy", "clang_tidy", [f"-checks=-*,{','.join(checks)}", "--quiet", *sources, "--",
                                                 f"-std={language_std}", *includes], adapters.clang_tidy)
            if c_like and "cppcheck" in wanted and sources:
                run("cppcheck", "cppcheck", ["--enable=warning,style,performance,portability", "--xml",
                                             "--xml-version=2", "-q", "--inline-suppr",
                                             "--suppress=missingIncludeSystem", f"--std={language_std}",
                                             *includes, *sources], adapters.cppcheck_xml, use="stderr")
            if toolchain in ("c", "c_arm") and ("gcc-analyzer" in wanted or "compiler-warnings" in wanted) \
                    and sources:
                flags = sorted({f for p in profiles for f in p.compiler_flags.get(toolchain, ())})
                analyzer = ["-fanalyzer"] if "gcc-analyzer" in wanted else []
                run("gcc", "gcc14", [*analyzer, *flags, "-fdiagnostics-format=sarif-stderr", "-c",
                                     f"-std={language_std}", *includes, *sources],
                    lambda text: adapters.sarif_stream(text, "gcc"), use="stderr")
            if toolchain == "python" and "ruff" in wanted:
                select = sorted({s for p in profiles for s in p.ruff_select})
                run("ruff", "ruff", ["check", "--no-cache", "--exit-zero", "--output-format", "json", "--select",
                                     ",".join(select), "."],
                    lambda text: adapters.ruff_json(text, knowledge.bandit_cwe()))
            if toolchain in ("ada", "fortran", "cobol") and "compiler-warnings" in wanted and sources:
                from forge.packs.compiled import TOOLCHAINS

                chain = TOOLCHAINS[toolchain]
                flags = [f for p in profiles for f in p.compiler_flags.get(toolchain, ())]
                if any(p.profile_id == "ada-restricted" for p in profiles):
                    case_dir = self.assessment.work_root / new_id("job")
                    case_dir.mkdir(parents=True)
                    (case_dir / "forge.adc").write_text(standards.ADA_RESTRICTIONS, encoding="utf-8")
                    os.chmod(case_dir, 0o755)
                    os.chmod(case_dir / "forge.adc", 0o644)
                    flags.append("-gnatec=/case/forge.adc")
                arguments = [*(chain.analyze_arguments or []), *flags, *sources]
                run(chain.pack_id, chain.compiler, arguments, lambda text: adapters.compiler_text(text, chain.pack_id),
                    use="stderr", case=case_dir)
            scripts = sorted(p for p in files if p.endswith(SCRIPT_SUFFIXES))
            if "shellcheck" in wanted and scripts:
                run("shellcheck", "shellcheck", ["-f", "json1", *scripts], adapters.shellcheck_json)
            if "power-of-ten" in wanted:
                raw.extend(power_of_ten.analyze(files))
                result.tools["power-of-ten"] = "forge"
                result.runs.append({"tool": "power-of-ten", "exit_code": 0, "status": "ok"})
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            if case_dir is not None:
                shutil.rmtree(case_dir, ignore_errors=True)
        own = [f for f in raw if f.path in files]          # never report on system headers or generated files
        result.findings = merge(anchor([standards.classify(f, profiles) for f in own], files))
        result.attestations = list(dict.fromkeys(result.attestations))
        return result

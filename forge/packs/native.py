"""The C and C++ packs (ticket F16).

What the packs add on top of the protected black-box evaluator:

- **Exact build context.** Every build records the compiler driver and its
  helper programs (compiler proper, assembler, linker) by digest, the target
  triple, the full argument list and the digest of every source file. The
  same context is exported as a `compile_commands.json` entry, so editor
  tooling sees exactly what the evaluator compiled.
- **Build in the sandbox.** Candidate source is untrusted; it is compiled
  inside the isolation backend with read-only input, and the one produced
  binary is collected through the output collector.
- **Protected flags.** Warnings, standard and sanitizers come from the
  criterion (written by the trusted user), never from the candidate, so a
  candidate cannot pass by switching its sanitizers off.
- **Runtime checks.** Cases run against an AddressSanitizer +
  UndefinedBehaviorSanitizer build; reports are parsed into structured,
  confirmed findings with the input that triggered them. A memory error
  fails the criterion even when every observable output was right.
- **Malformed-input cases.** `malformed_variants` derives inputs from valid
  seeds deterministically (every truncation, a trailing byte, each declared
  length field set to 0, one past and the maximum); the expected result for
  each comes from a reference oracle the user approved, never from the
  program under test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from forge.assessment.evaluator import SuiteRun, run_suite
from forge.assessment.suite import BlackBoxCase, ExpectedResult
from forge.contracts.base import Slug, StrictModel
from forge.contracts.digests import Digest, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.execution.collector import collect_outputs
from forge.packs.python_pack import PackDescriptor
from forge.sandbox.base import ExecutionObservation, SandboxSpec
from forge.tools.registry import ToolDefinition, ToolRegistry, file_digest
from forge.workspace.snapshot import load_manifest

NATIVE_TOOL_REF = "forge.native_cases@1"
SOURCE_SUFFIXES = {"c": (".c",), "cpp": (".cc", ".cpp", ".cxx")}
HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")

C_PACK = PackDescriptor(
    pack_id="c", version=1, languages=["c"], tools=["gcc", "clang"],
    evaluators=["native_cases"], task_templates=["build_tool", "review", "bug_fix", "test_strategy"],
    selected_by_facts=["language.c"],
    limitations=[
        "sanitizers find errors on the inputs that were run; they do not prove other inputs safe",
        "MemorySanitizer, ThreadSanitizer and static analyzers (clang-tidy, cppcheck) are not admitted yet",
        "builds and runs on the Linux backend only; other platforms are not_run",
    ],
)
CPP_PACK = C_PACK.model_copy(update={"pack_id": "cpp", "languages": ["cpp"], "tools": ["gxx", "clangxx"],
                                     "selected_by_facts": ["language.cpp"]})

SANITIZER_FLAGS = {"address": "address", "undefined": "undefined"}
RUN_ENV = {
    # Fixed by the evaluator. Leak checks stay on; any report ends the run.
    "ASAN_OPTIONS": "detect_leaks=1:abort_on_error=0:halt_on_error=1:allocator_may_return_null=0",
    "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=1",
}


# ------------------------------------------------------------------ toolchain


def admit_native_tools(registry: ToolRegistry) -> list[str]:
    """Admit host compilers that exist, pinned by digest, with version and target."""
    admitted = []
    for tool_id, program in (("gcc", "gcc"), ("gxx", "g++"), ("clang", "clang"), ("clangxx", "clang++")):
        found = shutil.which(program)
        if not found:
            continue
        real = Path(os.path.realpath(found))
        version = _probe(real, ["-dumpversion"])
        if not version:
            continue
        paths = [] if str(real).startswith(("/usr/", "/bin/")) else [str(real.parent.parent)]
        registry.admit(tool_id, real, version=version, toolchain_paths=paths, side_effect_class="scratch_only")
        admitted.append(tool_id)
    return admitted


def _probe(executable: Path, args: list[str]) -> str:
    try:
        done = subprocess.run([str(executable), *args], capture_output=True, text=True, timeout=20, check=False,
                              env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def helper_programs(tool: ToolDefinition) -> dict[str, dict[str, str]]:
    """The programs the driver will run, by digest (the driver alone is not the toolchain)."""
    helpers: dict[str, dict[str, str]] = {}
    for name in ("cc1", "cc1plus", "as", "ld", "collect2"):
        printed = _probe(Path(tool.executable), [f"-print-prog-name={name}"])
        path = printed if os.path.isabs(printed) else shutil.which(printed or name, path="/usr/bin:/bin") or ""
        if path and os.path.isfile(path):
            real = os.path.realpath(path)
            helpers[name] = {"path": real, "digest": file_digest(Path(real))}
    return helpers


# ----------------------------------------------------------------- build spec


class NativeBuildSpec(StrictModel):
    """How the evaluator compiles a candidate. Lives in the protected criterion."""

    language: Literal["c", "cpp"]
    compiler: Slug
    standard: Literal["c99", "c11", "c17", "c++17", "c++20"]
    sources: list[str] = Field(min_length=1, max_length=64)
    include_dirs: list[str] = Field(default_factory=list, max_length=16)
    defines: list[str] = Field(default_factory=list, max_length=32)
    warnings: list[Literal["all", "extra", "pedantic", "shadow", "conversion"]] = Field(
        default_factory=lambda: ["all", "extra"])
    warnings_as_errors: bool = True
    optimization: Literal["0", "1", "2", "g"] = "1"
    sanitizers: list[Literal["address", "undefined"]] = Field(default_factory=lambda: ["address", "undefined"])
    output: Slug = "program"

    @field_validator("sources", "include_dirs")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        for path in value:
            check_relative_path(path)
            if path.startswith("-"):
                raise ValueError(f"path {path!r} would be read as a compiler option")
        return value

    @field_validator("defines")
    @classmethod
    def _defines(cls, value: list[str]) -> list[str]:
        for item in value:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}(=[A-Za-z0-9_.]{0,64})?", item):
                raise ValueError(f"define {item!r} is not a plain NAME or NAME=value")
        return value

    def check_sources(self) -> None:
        allowed = SOURCE_SUFFIXES[self.language]
        for path in self.sources:
            if not path.endswith(allowed):
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"{path} is not a {self.language} source file")

    def preprocess_arguments(self, *, sanitized: bool) -> list[str]:
        """Preprocess only. Run with and without sanitizers to expose sanitizer-aware code."""
        args = [f"-std={self.standard}", "-E"]
        if sanitized and self.sanitizers:
            args.append(f"-fsanitize={','.join(SANITIZER_FLAGS[s] for s in self.sanitizers)}")
        args += [f"-I{path}" for path in self.include_dirs]
        args += [f"-D{item}" for item in self.defines]
        return args + list(self.sources)

    def arguments(self) -> list[str]:
        """Compiler arguments after the driver path, deterministic for a given spec."""
        args = [f"-std={self.standard}", f"-O{self.optimization}", "-g", "-fno-omit-frame-pointer",
                "-fdiagnostics-color=never", "-fno-diagnostics-show-caret"]
        args += [f"-W{name}" for name in self.warnings]
        if self.warnings_as_errors:
            args.append("-Werror")
        if self.sanitizers:
            args += [f"-fsanitize={','.join(SANITIZER_FLAGS[s] for s in self.sanitizers)}",
                     "-fno-sanitize-recover=all"]
        args += [f"-I{path}" for path in self.include_dirs]
        args += [f"-D{item}" for item in self.defines]
        args += list(self.sources)
        args += ["-o", f"/output/{self.output}"]
        return args


class BuildContext(StrictModel):
    """Everything that determined one binary. Its digest names the build."""

    spec: NativeBuildSpec
    compiler: dict[str, str]                # tool_id, path, digest, version, target
    helpers: dict[str, dict[str, str]]      # cc1/as/ld/... -> path, digest
    arguments: list[str]
    sources: dict[str, Digest]              # every source and header in the snapshot that the build can see
    snapshot_id: str

    def digest(self) -> str:
        return digest_json(self.model_dump(mode="json"))

    def compile_commands(self, directory: str = "/input") -> list[dict[str, Any]]:
        """clangd-compatible entries, one per translation unit."""
        dropped = {*self.spec.sources, "-o", f"/output/{self.spec.output}"}
        flags = [argument for argument in self.arguments if argument not in dropped]
        return [{"directory": directory, "file": source, "arguments": [self.compiler["path"], *flags, "-c", source]}
                for source in self.spec.sources]


# ------------------------------------------------------------- diagnostics


class Diagnostic(StrictModel):
    path: str
    line: int
    column: int
    severity: Literal["error", "warning", "note"]
    message: str
    flag: str | None = None


_DIAGNOSTIC = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?P<severity>fatal error|error|warning|note): "
    r"(?P<message>.*?)(?: \[(?P<flag>-W[^\]]+)\])?$"
)


def parse_diagnostics(stderr: str) -> list[Diagnostic]:
    found = []
    for line in stderr.splitlines():
        match = _DIAGNOSTIC.match(line.strip())
        if match:
            severity = "error" if match["severity"] == "fatal error" else match["severity"]
            flag = match["flag"]
            if flag and flag.startswith("-Werror="):   # promoted by -Werror; keep the underlying warning
                flag = "-W" + flag.removeprefix("-Werror=")
            found.append(Diagnostic(path=match["path"].removeprefix("/input/").removeprefix("./"),
                                    line=int(match["line"]), column=int(match["col"]), severity=severity,
                                    message=match["message"][:500], flag=flag))
    return found


# ---------------------------------------------------------------- sanitizers


class SanitizerReport(StrictModel):
    sanitizer: Literal["address", "undefined", "leak"]
    kind: str
    path: str | None = None
    line: int | None = None
    function: str | None = None
    headline: str


class SanitizerParse(StrictModel):
    reports: list[SanitizerReport]
    runtime_failure: str | None = None   # the sanitizer could not start: a tool error, not a finding


_ASAN = re.compile(r"ERROR: AddressSanitizer: ([A-Za-z0-9_-]+)")
_LSAN = re.compile(r"ERROR: LeakSanitizer: detected memory leaks")
_UBSAN = re.compile(r"^(?P<path>[^:\s]+):(?P<line>\d+):(?P<col>\d+): runtime error: (?P<message>.+)$", re.M)
_FRAME = re.compile(r"#\d+ 0x[0-9a-f]+ in (?P<function>\S+) (?P<path>[^\s:]+):(?P<line>\d+)")
_RUNTIME_FAILURES = ("ReserveShadowMemoryRange failed", "failed to allocate", "LeakSanitizer has encountered a fatal error",
                     "Shadow memory range interleaves", "ASan runtime does not come first")
_UB_KINDS = (("left shift", "shift"), ("right shift", "shift"), ("shift exponent", "shift"),
             ("signed integer overflow", "signed-integer-overflow"), ("out of bounds", "out-of-bounds-index"),
             ("misaligned", "misaligned-access"), ("null pointer", "null-pointer"),
             ("division by zero", "division-by-zero"), ("not a valid value for type", "invalid-value"))


def _project_frame(text: str) -> tuple[str | None, int | None, str | None]:
    """The first stack frame inside the project (not libc or the sanitizer runtime)."""
    for match in _FRAME.finditer(text):
        path = match["path"]
        if path.startswith("/input/"):
            path = path.removeprefix("/input/")
        elif path.startswith(("/", "..")):
            continue
        try:
            check_relative_path(path)
        except ValueError:
            continue
        return path, int(match["line"]), match["function"]
    return None, None, None


def parse_sanitizer_output(stderr: str) -> SanitizerParse:
    """Structured reports; if none were made but the runtime failed, say so instead."""
    reports: list[SanitizerReport] = []
    asan = _ASAN.search(stderr)
    if asan:
        path, line, function = _project_frame(stderr[asan.end():])
        reports.append(SanitizerReport(sanitizer="address", kind=asan.group(1), path=path, line=line,
                                       function=function, headline=stderr[asan.start():].splitlines()[0][:300]))
    if _LSAN.search(stderr):
        path, line, function = _project_frame(stderr[_LSAN.search(stderr).end():])
        reports.append(SanitizerReport(sanitizer="leak", kind="memory-leak", path=path, line=line,
                                       function=function, headline="LeakSanitizer: detected memory leaks"))
    for match in _UBSAN.finditer(stderr):
        message = match["message"]
        kind = next((name for needle, name in _UB_KINDS if needle in message), "undefined-behavior")
        path = match["path"].removeprefix("/input/").removeprefix("./")
        try:
            check_relative_path(path)
        except ValueError:
            path = None
        reports.append(SanitizerReport(sanitizer="undefined", kind=kind, path=path, line=int(match["line"]),
                                       headline=f"runtime error: {message}"[:300]))
    if not reports:
        for marker in _RUNTIME_FAILURES:
            if marker in stderr:
                return SanitizerParse(reports=[], runtime_failure=marker)
    return SanitizerParse(reports=reports)


# ---------------------------------------------------- instrumentation bypass

# Constructs that switch sanitizer checks off, talk to the sanitizer runtime
# (for example __asan_default_options, which can weaken every check), or run
# uninstrumented code (inline assembly). Scanned only in the project's own
# code after preprocessing, so token pasting cannot hide them.
_BYPASS = re.compile(
    r"\b_*no_sanitize\w*|\b_*no_address_safety_analysis\w*|\b__(?:asan|lsan|ubsan|msan|tsan|sanitizer)_\w+"
    r"|\b(?:__asm__|__asm|asm)\b|\bdisable_sanitizer_instrumentation\b"
)
_LINE_MARKER = re.compile(r'^# \d+ "([^"]*)"')


def _without_markers(preprocessed: str) -> list[str]:
    return [line for line in preprocessed.splitlines() if not line.startswith("#") and line.strip()]


def instrumentation_problems(sanitized: str, plain: str) -> list[str]:
    """Reasons the sanitized build may not measure what it claims to."""
    problems = []
    if _without_markers(sanitized) != _without_markers(plain):
        problems.append("the source compiles differently when sanitizers are enabled (sanitizer-aware code)")
    current, seen = "", set()
    for line in sanitized.splitlines():
        marker = _LINE_MARKER.match(line)
        if marker:
            current = marker.group(1)
            continue
        if current.startswith(("/", "<")) or not current:
            continue  # system headers and compiler built-ins
        for match in _BYPASS.finditer(line):
            seen.add((current, match.group(0)))
    for path, token in sorted(seen):
        problems.append(f"{path} uses {token!r}, which disables, configures or bypasses sanitizer checks")
    return problems


# ------------------------------------------------------------ malformed inputs


def malformed_variants(seed: bytes, length_fields: list[tuple[int, int]] = ()) -> list[tuple[str, bytes]]:
    """Deterministic structural mutations of one valid input.

    length_fields are (offset, width) of big-endian length fields in the seed.
    The result is deduplicated and excludes the seed itself.
    """
    variants: list[tuple[str, bytes]] = [("empty", b"")]
    for cut in range(1, len(seed)):
        variants.append((f"truncated_{cut}", seed[:cut]))
    variants.append(("trailing_byte", seed + b"\x00"))
    for offset, width in length_fields:
        if offset + width > len(seed):
            raise ValueError(f"length field at {offset} runs past the seed")
        current = int.from_bytes(seed[offset:offset + width], "big")
        for label, value in (("zero", 0), ("plus_one", current + 1), ("max", (1 << (8 * width)) - 1)):
            if value == current or value >= 1 << (8 * width):
                continue
            mutated = seed[:offset] + value.to_bytes(width, "big") + seed[offset + width:]
            variants.append((f"len{offset}_{label}", mutated))
    seen: set[bytes] = {seed}
    unique = []
    for name, data in variants:
        if data not in seen:
            seen.add(data)
            unique.append((name, data))
    return unique


def oracle_cases(prefix: str, inputs: list[tuple[str, bytes]], oracle: Callable[[bytes], ExpectedResult], *,
                 file_name: str = "input.bin") -> list[BlackBoxCase]:
    """Black-box cases whose expectations come from an approved reference oracle."""
    import base64

    cases = []
    for name, data in inputs:
        cases.append(BlackBoxCase(
            case_id=f"{prefix}_{name}".replace("-", "_")[:64], description=f"{prefix}: {name} ({len(data)} bytes)",
            args=[f"{{case}}/{file_name}"], files={file_name: base64.b64encode(data).decode()},
            expect=oracle(data)))
    return cases


# ------------------------------------------------------------------ assessor


class NativeAssessor:
    """Build a C/C++ candidate in the sandbox, then run the protected suite against it."""

    def __init__(self, service, registry: ToolRegistry) -> None:  # noqa: ANN001 - AssessmentService
        self.service = service
        self.registry = registry

    def build_context(self, spec: NativeBuildSpec, snapshot_id: str) -> tuple[BuildContext, ToolDefinition]:
        spec.check_sources()
        tool = self.registry.resolve(spec.compiler)
        snapshot = self.service.controller.store.snapshot(snapshot_id)
        manifest = load_manifest(self.service.controller.blobs, snapshot.manifest_digest)
        visible = {entry.path: entry.digest for entry in manifest.entries
                   if entry.path.endswith(SOURCE_SUFFIXES["c"] + SOURCE_SUFFIXES["cpp"] + HEADER_SUFFIXES)}
        missing = [path for path in spec.sources if path not in visible]
        if missing:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"sources not in the snapshot: {missing}")
        target = _probe(Path(tool.executable), ["-dumpmachine"])
        context = BuildContext(
            spec=spec,
            compiler={"tool_id": tool.tool_id, "path": tool.executable, "digest": tool.executable_digest,
                      "version": tool.version, "target": target},
            helpers=helper_programs(tool), arguments=spec.arguments(), sources=visible, snapshot_id=snapshot_id)
        return context, tool

    def assess(self, criterion_id: str, snapshot_id: str) -> dict[str, Any]:
        service = self.service
        criterion = service.controller.store.criterion(criterion_id)
        if criterion is None or criterion.assessment_method != "native_cases":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{criterion_id} is not a native_cases criterion")
        criterion_ref = {"id": criterion.criterion_id, "revision": criterion.revision}
        spec = NativeBuildSpec.model_validate(criterion.parameters["build"])
        suite = service.load_suite(criterion.parameters["suite_artifact_id"], criterion.parameters["suite_digest"])
        if suite.entrypoint[:1] != [f"/input/{spec.output}"]:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"the suite must run /input/{spec.output}")

        def record(result: str, limitations: list[str], evidence: list[str], summary: dict[str, Any]) -> str:
            return service._send("assessment.record", {
                "criterion_ref": criterion_ref, "snapshot_id": snapshot_id, "evidence_refs": evidence,
                "assessment_result": result, "assessor_ref": NATIVE_TOOL_REF, "limitations": limitations,
                "summary": summary}).result["assessment_id"]

        capabilities = service.backend.capabilities()
        if not capabilities.available:
            assessment_id = record("not_run", [f"isolation backend unavailable: {capabilities.unavailable_reason}"],
                                   [], {})
            return {"assessment_id": assessment_id, "result": "not_run"}

        context, tool = self.build_context(spec, snapshot_id)
        build = self._build(context, tool, snapshot_id)
        if build["result"] != "built":
            result = "fail" if build["result"] == "compile_failed" else "error"
            assessment_id = record(result, [build["reason"]], [build["evidence_id"]],
                                   {"build": build["result"], "diagnostics": len(build["diagnostics"])})
            return {**build, "build_result": build["result"], "assessment_id": assessment_id, "result": result,
                    "finding_ids": []}

        run_dir = service.work_root / f"{new_id('job')}-bin"
        run_dir.mkdir(parents=True)
        os.chmod(run_dir, 0o755)
        binary = run_dir / spec.output
        with open(binary, "wb") as handle:
            for chunk in service.controller.blobs.iter_verified(build["binary_digest"]):
                handle.write(chunk)
        os.chmod(binary, 0o755)
        sanitizer: dict[str, SanitizerParse] = {}
        job_id = new_id("job")
        try:
            run: SuiteRun = run_suite(
                suite, run_dir, service.backend, service.work_root / job_id, python_path=service.python_path,
                limits=service.limits, toolchain_paths=tool.toolchain_paths,
                env=RUN_ENV if spec.sanitizers else {},
                on_observation=lambda case_id, obs: sanitizer.__setitem__(
                    case_id, parse_sanitizer_output(obs.stderr.decode("utf-8", "replace"))))
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

        reports = [(case_id, report) for case_id, parsed in sanitizer.items() for report in parsed.reports]
        runtime_failures = sorted({parsed.runtime_failure for parsed in sanitizer.values() if parsed.runtime_failure})
        report_id = service._record_report("native_evaluation_report", {
            "suite_run": run.model_dump(mode="json"), "build_context_digest": context.digest(),
            "sanitizer_reports": [{"case_id": case_id, **report.model_dump(mode="json")} for case_id, report in reports],
            "sanitizer_runtime_failures": runtime_failures})
        evidence_id = service._send("evidence.record_execution", {
            "job_id": job_id, "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": NATIVE_TOOL_REF,
            "environment_digest": run.environment_digest, "evaluator_digest": run.evaluator_digest,
            "observation_type": "native_case_results",
            "observation": {"counts": run.counts, "build_context_digest": context.digest(),
                            "sanitizers": list(spec.sanitizers),
                            "sanitizer_reports": [f"{case_id}: {r.sanitizer}.{r.kind} at {r.path}:{r.line}"
                                                  for case_id, r in reports][:50],
                            "failed_cases": [c.case_id for c in run.cases if c.outcome == "fail"][:50]},
            "artifact_refs": [report_id],
        }).result["evidence_id"]

        finding_ids = []
        for case_id, report in reports:
            finding_ids.append(service._send("finding.record", {
                "rule_id": f"{report.sanitizer}.{report.kind}",
                "claim": f"{report.headline} (reproduced by case {case_id})",
                "severity": "high", "confirmation": "confirmed", "path": report.path, "line": report.line,
                "fingerprint": digest_json({"rule": f"{report.sanitizer}.{report.kind}", "path": report.path,
                                            "function": report.function}),
                "snapshot_id": snapshot_id, "evidence_refs": [evidence_id, build["evidence_id"]],
            }).result["finding_id"])

        for problem in build["bypass"]:
            finding_ids.append(service._send("finding.record", {
                "rule_id": "native.sanitizer_bypass", "claim": problem, "severity": "high",
                "confirmation": "suspected", "fingerprint": digest_json({"rule": "native.sanitizer_bypass",
                                                                          "problem": problem}),
                "snapshot_id": snapshot_id, "evidence_refs": [build["evidence_id"]],
            }).result["finding_id"])

        limitations = list(run.limitations)
        if spec.sanitizers:
            limitations.append("sanitizers report errors on executed paths only")
        else:
            limitations.append("no sanitizers were enabled; memory errors may go unseen")
        if runtime_failures:
            result = "error"
            limitations.append(f"the sanitizer runtime could not start: {', '.join(runtime_failures)}")
        elif reports:
            result = "fail"
        elif build["bypass"] and run.result == "pass":
            result = "inconclusive"
            limitations.append("the candidate may evade the sanitizers; a reviewer must decide: "
                               + "; ".join(build["bypass"])[:800])
        else:
            result = run.result
        assessment_id = record(result, limitations, [build["evidence_id"], evidence_id],
                               {"counts": run.counts, "sanitizer_reports": len(reports),
                                "build_context_digest": context.digest()})
        return {"assessment_id": assessment_id, "result": result, "run": run, "reports": reports,
                "finding_ids": finding_ids, "context": context, "diagnostics": build["diagnostics"],
                "build_evidence_id": build["evidence_id"], "evidence_id": evidence_id}

    def _build(self, context: BuildContext, tool: ToolDefinition, snapshot_id: str) -> dict[str, Any]:
        service = self.service
        snapshot_dir = service._materialize(snapshot_id)
        job_id = new_id("job")
        work = service.work_root / job_id
        limits = service.limits.model_copy(update={"wall_ms": max(service.limits.wall_ms, 120_000),
                                                   "cpu_seconds": max(service.limits.cpu_seconds, 60)})
        collected = None
        problems: list[str] = []
        try:
            if context.spec.sanitizers:
                problems = self._bypass_check(context, tool, snapshot_dir, limits)
            observation: ExecutionObservation = service.backend.run(
                SandboxSpec(job_key="native_build", input_dir=str(snapshot_dir), cwd="/input",
                            argv=[tool.executable, *context.arguments], limits=limits,
                            toolchain_paths=tool.toolchain_paths, env={"LC_ALL": "C", "TMPDIR": "/tmp"}),
                work)
            if observation.terminated_by == "exited" and observation.exit_code == 0:
                collected = collect_outputs(Path(observation.output_dir), service.controller.blobs, max_files=1,
                                            max_bytes=limits.output_bytes)
        finally:
            destroy = getattr(service.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        stderr = observation.stderr.decode("utf-8", "replace")
        diagnostics = parse_diagnostics(stderr)
        context_id = service._record_report("native_build_context", {
            **context.model_dump(mode="json"), "context_digest": context.digest(),
            "compile_commands": context.compile_commands()})
        artifact_refs = [context_id]
        binary_digest = None
        if observation.terminated_by in ("launch_failed", "cancelled"):
            outcome, reason = "error", f"build incomplete: {observation.terminated_by}"
        elif observation.terminated_by != "exited":
            outcome, reason = "error", f"compiler {observation.terminated_by}"
        elif observation.exit_code != 0:
            outcome, reason = "compile_failed", "the candidate does not compile with the protected flags"
        elif collected is None or not collected.accepted or [f.path for f in collected.files] != [context.spec.output]:
            outcome, reason = "error", "the build did not produce exactly the expected binary"
        else:
            outcome, reason = "built", "compiled with the protected flags"
            item = collected.files[0]
            binary_digest = item.digest
            artifact_refs.append(service._send("artifact.register", {
                "digest": item.digest, "size": item.size, "content_type": "application/octet-stream",
                "artifact_kind": "native_binary", "sensitivity": "project"}).result["artifact_id"])
        evidence_id = service._send("evidence.record_execution", {
            "job_id": job_id, "attempt_number": 1, "snapshot_id": snapshot_id,
            "tool_ref": f"{tool.tool_id}@{tool.version}",
            "environment_digest": digest_json(service.backend.capabilities().model_dump(mode="json")),
            "observation_type": "native_build",
            "observation": {"outcome": outcome, "exit_code": observation.exit_code, "instrumentation_problems": problems,
                            "terminated_by": observation.terminated_by, "build_context_digest": context.digest(),
                            "binary_digest": binary_digest,
                            "diagnostics": [d.model_dump(mode="json") for d in diagnostics][:50],
                            "stderr_tail": stderr[-1500:]},
            "artifact_refs": artifact_refs,
        }).result["evidence_id"]
        return {"result": outcome, "reason": reason, "binary_digest": binary_digest, "diagnostics": diagnostics,
                "evidence_id": evidence_id, "context_artifact_id": context_id, "bypass": problems}

    def _bypass_check(self, context: BuildContext, tool: ToolDefinition, snapshot_dir: Path, limits) -> list[str]:  # noqa: ANN001
        outputs = []
        for sanitized in (True, False):
            work = self.service.work_root / new_id("job")
            try:
                observation = self.service.backend.run(
                    SandboxSpec(job_key="native_preprocess", input_dir=str(snapshot_dir), cwd="/input",
                                argv=[tool.executable, *context.spec.preprocess_arguments(sanitized=sanitized)],
                                limits=limits, toolchain_paths=tool.toolchain_paths, env={"LC_ALL": "C"}), work)
            finally:
                destroy = getattr(self.service.backend, "destroy", None)
                if destroy is not None:
                    destroy(work)
            if observation.terminated_by != "exited" or observation.exit_code != 0:
                return []  # the real compile reports the error with its diagnostics
            if observation.stdout_truncated:
                return ["the preprocessed source exceeds the output limit, so it could not be checked"]
            outputs.append(observation.stdout.decode("utf-8", "replace"))
        return instrumentation_problems(outputs[0], outputs[1])

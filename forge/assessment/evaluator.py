"""Run a protected black-box suite against a candidate snapshot.

Trust layout:
- untrusted: the candidate program, running in the sandbox with /input (its
  own snapshot, read-only) and /case (this case's input files, read-only);
- trusted: this module, outside the sandbox, which holds the expected
  results and compares them with what the sandbox observed.

The candidate cannot read the expected results, cannot change the suite,
and cannot influence the verdict by printing "ALL TESTS PASSED" or writing
report files: only exit status and the declared output streams are compared.

Outcomes per case:
- pass: every expectation met;
- fail: the program ran and behaved wrongly (wrong output, wrong exit code,
  crash, timeout, memory limit);
- error: the measurement itself failed (sandbox could not start, job was
  cancelled). An error never counts as either pass or fail.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

from forge.authority.authorize import ResourceLimits
from forge.contracts.base import StrictModel
from forge.contracts.digests import Digest, digest_json, sha256_digest
from forge.contracts.paths import check_relative_path
from forge.sandbox.base import ExecutionObservation, SandboxBackend, SandboxSpec

TOOL_REF = "forge.evaluator.black_box@1"
COMPARISON_REVISION = 1
MAX_JSON_STDOUT = 1024 * 1024

CaseOutcome = Literal["pass", "fail", "error"]
SuiteResult = Literal["pass", "fail", "error", "inconclusive"]


class CaseResult(StrictModel):
    case_id: str
    outcome: CaseOutcome
    reasons: list[str]
    exit_code: int | None
    signal: int | None
    terminated_by: str
    wall_ms: int
    stdout_digest: Digest
    stderr_digest: Digest
    stdout_truncated: bool
    stderr_excerpt: str = Field(max_length=2000)


class SuiteRun(StrictModel):
    suite_id: str
    suite_digest: Digest
    evaluator_digest: Digest
    environment_digest: Digest
    backend_id: str
    result: SuiteResult
    cases: list[CaseResult]
    limitations: list[str]
    counts: dict[str, int]


def evaluator_digest(suite_digest: str) -> str:
    return digest_json({"tool": TOOL_REF, "suite_digest": suite_digest, "comparison": COMPARISON_REVISION})


def environment_digest(backend: SandboxBackend, python_path: str) -> str:
    caps = backend.capabilities()
    return digest_json(
        {
            "backend": caps.model_dump(mode="json"),
            "kernel": platform.release(),
            "python": os.path.realpath(python_path),
            "python_version": platform.python_version(),
        }
    )


def run_suite(
    suite,  # noqa: ANN001 - EvaluatorSuite
    snapshot_dir: Path,
    backend: SandboxBackend,
    work_root: Path,
    *,
    python_path: str,
    limits: ResourceLimits,
    repeat: int = 1,
    toolchain_paths: list[str] | None = None,
) -> SuiteRun:
    """Run every case (repeat > 1 also checks that results are deterministic)."""
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    case_limits = limits.model_copy(update={"wall_ms": min(limits.wall_ms, suite.case_wall_ms)})
    results: list[CaseResult] = []
    limitations: list[str] = []
    for case in suite.cases:
        runs = []
        for attempt in range(repeat):
            case_dir = work_root / f"case-{case.case_id}-{attempt}"
            _write_case_files(case_dir, case.file_bytes())
            argv = [_resolve(part, python_path) for part in suite.entrypoint] + [
                arg.replace("{case}", "/case") for arg in case.args
            ]
            spec = SandboxSpec(
                job_key=f"eval_{case.case_id}_{attempt}"[:60],
                input_dir=str(snapshot_dir),
                case_dir=str(case_dir),
                argv=argv,
                cwd="/input",
                limits=case_limits,
                stdin=case.stdin(),
                toolchain_paths=toolchain_paths or [],
            )
            sandbox_dir = work_root / f"run-{case.case_id}-{attempt}"
            observation = backend.run(spec, sandbox_dir)
            try:
                runs.append(compare(case, observation))
            finally:
                destroy = getattr(backend, "destroy", None)
                if destroy is not None:
                    destroy(sandbox_dir)
                shutil.rmtree(case_dir, ignore_errors=True)
        first = runs[0]
        for later in runs[1:]:
            if (later.outcome, later.exit_code, later.stdout_digest) != (
                first.outcome, first.exit_code, first.stdout_digest
            ):
                limitations.append(f"case {case.case_id} gave different results on repeated runs")
                break
        results.append(first)

    counts = {outcome: sum(1 for item in results if item.outcome == outcome) for outcome in ("pass", "fail", "error")}
    if counts["fail"]:
        result: SuiteResult = "fail"
    elif counts["error"]:
        result = "error"
    elif limitations:
        result = "inconclusive"
    else:
        result = "pass"
    limitations.append(
        f"{len(suite.cases)} finite cases were run; passing them does not prove behavior on other inputs"
    )
    suite_digest = suite.digest()
    return SuiteRun(
        suite_id=suite.suite_id,
        suite_digest=suite_digest,
        evaluator_digest=evaluator_digest(suite_digest),
        environment_digest=environment_digest(backend, python_path),
        backend_id=backend.capabilities().backend_id,
        result=result,
        cases=results,
        limitations=limitations,
        counts=counts,
    )


def _resolve(part: str, python_path: str) -> str:
    return python_path if part == "{python}" else part


def _write_case_files(case_dir: Path, files: dict[str, bytes]) -> None:
    case_dir.mkdir(parents=True, exist_ok=False)
    for path, data in files.items():
        check_relative_path(path)
        target = case_dir.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o644)
    for directory, _subdirs, _files in os.walk(case_dir):
        os.chmod(directory, 0o755)


def compare(case, observation: ExecutionObservation) -> CaseResult:  # noqa: ANN001 - BlackBoxCase
    expect = case.expect
    reasons: list[str] = []
    outcome: CaseOutcome = "pass"
    if observation.terminated_by in ("launch_failed", "cancelled"):
        outcome = "error"
        reasons.append(f"measurement incomplete: {observation.terminated_by}")
    elif observation.terminated_by == "timeout":
        reasons.append(f"did not finish within {observation.wall_ms} ms")
    elif observation.terminated_by == "memory_limit":
        reasons.append("exceeded the memory limit")
    else:
        reasons.extend(_check_exit(expect.exit_code, observation))
        if observation.stdout_truncated and (expect.stdout_json is not None or expect.stdout_text is not None):
            reasons.append("stdout exceeded the output limit")
        elif expect.stdout_json is not None:
            reasons.extend(_check_json(expect.stdout_json, observation.stdout))
        elif expect.stdout_text is not None and observation.stdout.decode("utf-8", "replace") != expect.stdout_text:
            reasons.append("stdout text differs from the expected text")
        if expect.stderr_contains is not None and expect.stderr_contains.encode() not in observation.stderr:
            reasons.append("stderr does not contain the expected message")
    if outcome == "pass" and reasons:
        outcome = "fail"
    return CaseResult(
        case_id=case.case_id,
        outcome=outcome,
        reasons=reasons,
        exit_code=observation.exit_code,
        signal=observation.signal,
        terminated_by=observation.terminated_by,
        wall_ms=observation.wall_ms,
        stdout_digest=sha256_digest(observation.stdout),
        stderr_digest=sha256_digest(observation.stderr),
        stdout_truncated=observation.stdout_truncated,
        stderr_excerpt=observation.stderr[-1500:].decode("utf-8", "replace"),
    )


def _check_exit(expected: int | str | None, observation: ExecutionObservation) -> list[str]:
    if expected is None:
        return []
    if observation.signal is not None:
        return [f"crashed with signal {observation.signal}"]
    if expected == "nonzero":
        return [] if observation.exit_code not in (0, None) else ["exited 0 but a failure exit was required"]
    if observation.exit_code != expected:
        return [f"exit code {observation.exit_code}, expected {expected}"]
    return []


def _check_json(expected: JsonValue, stdout: bytes) -> list[str]:
    if len(stdout) > MAX_JSON_STDOUT:
        return ["stdout is too large to parse as JSON"]
    try:
        actual = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["stdout is not valid JSON"]
    where = first_difference(expected, actual)
    # Report where the output differs, not the expected value: hidden
    # evaluator data should not leak into a repair loop's context.
    return [] if where is None else [f"stdout JSON differs at {where}"]


def first_difference(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and not isinstance(actual, bool)
    ):
        return path
    if isinstance(expected, dict):
        if set(expected) != set(actual):
            return f"{path} (keys)"
        for key in sorted(expected):
            found = first_difference(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path} (length)"
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            found = first_difference(left, right, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if expected == actual else path


def encode_files(files: dict[str, bytes]) -> dict[str, str]:
    """Helper for building suites: raw bytes -> base64 text."""
    return {path: base64.b64encode(data).decode("ascii") for path, data in files.items()}

"""Sandboxed white-box test runner for Python projects (TDD tooling).

Runs a project's own tests inside the isolation backend and returns one
structured result per test. Standard library only, so it works offline on a
clean interpreter: it collects `unittest.TestCase` classes and pytest-style
plain `test_*` functions that take no arguments (fixtures are reported as
unsupported rather than guessed).

Each failure is classified, because TDD needs to know WHY a test is red:

- `assertion`: the test ran and its expectation was not met (a real red);
- `missing_symbol`: the code under test does not exist yet (ImportError,
  NameError, or a module lacking the attribute): an acceptable first red
  for a new interface;
- `broken_test`: the test file itself does not compile or import cleanly;
- `error`: any other exception;
- `timeout`: the test exceeded its per-test time limit.

Trust boundary. These results are produced inside the same process as the
code under test, so hostile code can forge them. They are workflow evidence
for the TDD cycle, recorded with that limitation; acceptance still rests on
the protected black-box evaluator, and `forge.tdd.integrity` flags
production code that inspects or manipulates the test machinery.
"""

from __future__ import annotations

import ast
import hashlib
import json
import secrets
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field

from forge.contracts.base import StrictModel
from forge.contracts.ids import new_id
from forge.sandbox.base import SandboxSpec

RUNNER_REF = "forge.tdd.unit_runner@1"
MAX_TEST_FILE_BYTES = 1024 * 1024

Outcome = Literal["pass", "fail", "skip"]
FailureKind = Literal["assertion", "missing_symbol", "broken_test", "error", "timeout"]

_HARNESS = r"""
import contextlib, glob, importlib.util, inspect, io, json, os, signal, sys, time, traceback, unittest

def make_reporter(token, channel):
    def report(results):
        channel.write("FORGE-TEST-RESULTS " + token + " " + json.dumps(results) + "\n")
        channel.flush()
    return report

report = make_reporter(sys.stdin.readline().strip(), os.fdopen(os.dup(1), "w"))
spec = json.loads(sys.argv[1])
root = "/input"
for extra in spec["paths"]:
    sys.path.insert(0, os.path.join(root, extra))
sys.path.insert(0, root)
results = []

class Timeout(BaseException):
    pass

def on_alarm(signum, frame):
    raise Timeout()

signal.signal(signal.SIGALRM, on_alarm)

def classify_text(last):
    head = last.split(":", 1)[0].strip()
    if head.endswith("Timeout"):
        return "timeout"
    if head == "AssertionError":
        return "assertion"
    if head in ("ImportError", "ModuleNotFoundError", "NameError") or last.startswith("AttributeError: module"):
        return "missing_symbol"
    return "error"

def describe(error):
    return "".join(traceback.format_exception_only(type(error), error)).strip().splitlines()[-1]

def record(test_id, outcome, kind=None, message="", started=None):
    results.append({"test_id": test_id, "outcome": outcome, "kind": kind, "message": message[-600:],
                    "duration_ms": int((time.perf_counter() - started) * 1000) if started else 0})

def call(test_id, function):
    started = time.perf_counter()
    signal.setitimer(signal.ITIMER_REAL, spec["per_test_seconds"])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            function()
        outcome = ("pass", None, "")
    except unittest.SkipTest as skip:
        outcome = ("skip", None, str(skip))
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        text = describe(error)
        kind = "error" if isinstance(error, SystemExit) else classify_text(text)
        outcome = ("fail", kind, text)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    record(test_id, outcome[0], outcome[1], outcome[2], started)

def run_case(test_id, case):
    started = time.perf_counter()
    outcome = unittest.TestResult()
    signal.setitimer(signal.ITIMER_REAL, spec["per_test_seconds"])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            case.run(outcome)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if outcome.skipped:
        record(test_id, "skip", None, outcome.skipped[0][1], started)
    elif outcome.failures:
        record(test_id, "fail", "assertion", outcome.failures[0][1].strip().splitlines()[-1], started)
    elif outcome.errors:
        last = outcome.errors[0][1].strip().splitlines()[-1]
        record(test_id, "fail", classify_text(last), last, started)
    else:
        record(test_id, "pass", started=started)

files = sorted({p for pattern in spec["patterns"] for p in glob.glob(os.path.join(root, pattern), recursive=True)})
for path in files:
    relative = os.path.relpath(path, root)
    name = "forge_test_" + relative.replace("/", "_").removesuffix(".py").replace(".", "_")
    try:
        module_spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module_spec.loader.exec_module(module)
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        text = describe(error)
        kind = "missing_symbol" if classify_text(text) == "missing_symbol" else "broken_test"
        record(relative, "fail", kind, text)
        continue
    for attribute, value in sorted(vars(module).items()):
        if getattr(value, "__module__", None) != name:
            continue
        if attribute.startswith("test") and inspect.isfunction(value):
            required = [p.name for p in inspect.signature(value).parameters.values()
                        if p.default is inspect.Parameter.empty
                        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            if required:
                record(f"{relative}::{attribute}", "fail", "broken_test",
                       "takes fixtures (" + ", ".join(required) + "), which this runner does not provide")
            else:
                call(f"{relative}::{attribute}", value)
        elif inspect.isclass(value) and issubclass(value, unittest.TestCase):
            for method in unittest.TestLoader().getTestCaseNames(value):
                run_case(f"{relative}::{attribute}::{method}", value(method))
report(results)
"""


class TestResult(StrictModel):
    test_id: str
    outcome: Outcome
    kind: FailureKind | None = None
    message: str = ""
    duration_ms: int = 0


class TestRun(StrictModel):
    status: Literal["completed", "error"]
    error: str | None = None
    results: list[TestResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    def by_id(self) -> dict[str, TestResult]:
        return {result.test_id: result for result in self.results}

    def counts(self) -> dict[str, int]:
        return {outcome: sum(1 for r in self.results if r.outcome == outcome) for outcome in ("pass", "fail", "skip")}

    def all_pass(self) -> bool:
        return self.status == "completed" and bool(self.results) and all(r.outcome == "pass" for r in self.results)


class TestRunner:
    __test__ = False  # not a test case, despite the name

    """Runs a snapshot's tests in the sandbox through an AssessmentService's backend."""

    def __init__(self, service, *, patterns: tuple[str, ...] = ("tests/**/test_*.py", "test_*.py"),  # noqa: ANN001
                 import_paths: tuple[str, ...] = ("src",), per_test_seconds: float = 10.0) -> None:
        self.service = service
        self.patterns = patterns
        self.import_paths = import_paths
        self.per_test_seconds = per_test_seconds

    def run_directory(self, directory: Path) -> TestRun:
        service = self.service
        spec = {"patterns": list(self.patterns), "paths": list(self.import_paths),
                "per_test_seconds": self.per_test_seconds}
        work = service.work_root / new_id("job")
        # Read by the harness before any project code is imported; a program
        # that prints a fake results line cannot know it.
        token = secrets.token_hex(16)
        try:
            observation = service.backend.run(SandboxSpec(
                job_key="unit_tests", input_dir=str(directory), cwd="/input",
                argv=[service.python_path, "-I", "-B", "-c", _HARNESS, json.dumps(spec)],
                limits=service.limits, toolchain_paths=service.python_paths, stdin=(token + "\n").encode()), work)
        finally:
            destroy = getattr(service.backend, "destroy", None)
            if destroy is not None:
                destroy(work)
        limitations = ["results come from the process that also runs the code under test; hostile code can forge "
                       "them, so they are workflow evidence, not acceptance"]
        lines = [line for line in observation.stdout.decode("utf-8", "replace").splitlines()
                 if line.startswith(f"FORGE-TEST-RESULTS {token} ")]
        if observation.terminated_by != "exited" or len(lines) != 1:
            reason = (f"test run {observation.terminated_by} (exit {observation.exit_code})"
                      if observation.terminated_by != "exited" else
                      "the result channel was missing or written more than once")
            tail = observation.stderr[-400:].decode("utf-8", "replace").strip()
            return TestRun(status="error", error=f"{reason}: {tail}" if tail else reason, limitations=limitations)
        try:
            results = [TestResult.model_validate(item) for item in json.loads(lines[0].split(" ", 2)[2])]
        except ValueError as error:
            return TestRun(status="error", error=f"unreadable results: {str(error)[:200]}", limitations=limitations)
        if len({r.test_id for r in results}) != len(results):
            return TestRun(status="error", error="duplicate test ids in the results", limitations=limitations)
        return TestRun(status="completed", results=results, limitations=limitations)

    def run_snapshot(self, snapshot_id: str) -> TestRun:
        directory = self.service._materialize(snapshot_id)
        try:
            return self.run_directory(directory)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------- static test identity


def identify_tests(path: str, source: bytes) -> dict[str, str]:
    """Digest of each test function's source, by test id, without executing anything."""
    if len(source) > MAX_TEST_FILE_BYTES:
        return {path: "sha256:" + hashlib.sha256(source).hexdigest()}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {path: "sha256:" + hashlib.sha256(source).hexdigest()}
    found: dict[str, str] = {}

    def digest(node: ast.AST) -> str:
        return "sha256:" + hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            found[f"{path}::{node.name}"] = digest(node)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    found[f"{path}::{node.name}::{item.name}"] = digest(item)
    return found

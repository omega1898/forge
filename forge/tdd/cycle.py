"""Test-driven development as an enforced, recorded cycle.

Each phase compares two snapshots and runs the project's tests in the
sandbox. The rules are checked by code, so a model cannot talk its way past
them:

RED (base -> red snapshot)
  - only test files changed: no production code before a failing test;
  - at least one test was added or changed (the "cycle tests");
  - every cycle test fails for an acceptable reason: an assertion, or a
    missing symbol for a new interface. A test that fails because it is
    itself broken (syntax error, unsupported fixture) is not a red;
  - every test that passed on the base still passes.

GREEN (red -> green snapshot)
  - test files are byte-for-byte unchanged: the implementation may not edit,
    delete or weaken the tests that define it;
  - production code changed;
  - every test passes; none is skipped;
  - production changes are scanned for test-aware code (reward hacking);
    any flag sends the cycle to review instead of admitting it;
  - optionally, mutation testing on the changed lines measures whether the
    cycle tests constrain the new code; survivors are reported as findings
    and a minimum score can be required.

REFACTOR (green -> refactor snapshot, repeatable)
  - test files unchanged, every test still passes, integrity scan clean.

Every phase records evidence that names the previous phase's evidence, so
the chain red -> green -> refactor is reconstructible from the project
record. Phase evidence is workflow evidence: it shows how the change was
made. Whether the change is acceptable is still decided by the protected
criteria.
"""

from __future__ import annotations

import fnmatch
import shutil
from dataclasses import dataclass, field
from typing import Any, Literal

from forge.contracts.digests import canonical_json_bytes, digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.tdd.integrity import scan_source
from forge.tdd.mutation import changed_lines, generate_mutants, run_mutation
from forge.tdd.runner import RUNNER_REF, TestRun, TestRunner, identify_tests
from forge.workspace.snapshot import load_manifest

Verdict = Literal["admitted", "rejected", "needs_review"]
ACCEPTABLE_RED = ("assertion", "missing_symbol")
DEFAULT_TEST_GLOBS = ("tests/*", "tests/**/*", "test_*.py", "*/test_*.py", "*_test.py", "conftest.py")


@dataclass
class PhaseResult:
    phase: Literal["red", "green", "refactor"]
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    cycle_tests: list[str] = field(default_factory=list)
    evidence_id: str | None = None
    run: TestRun | None = None
    integrity_flags: list[dict[str, Any]] = field(default_factory=list)
    mutation: dict[str, Any] | None = None


class TddCycle:
    def __init__(self, service, *, test_globs: tuple[str, ...] = DEFAULT_TEST_GLOBS,  # noqa: ANN001
                 runner: TestRunner | None = None, mutation_limit: int = 20,
                 min_mutation_score: float | None = None) -> None:
        self.service = service
        self.test_globs = test_globs
        self.runner = runner or TestRunner(service)
        self.mutation_limit = mutation_limit
        self.min_mutation_score = min_mutation_score

    # ----------------------------------------------------------- helpers

    def is_test_path(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.test_globs)

    def _files(self, snapshot_id: str) -> dict[str, str]:
        snapshot = self.service.controller.store.snapshot(snapshot_id)
        if snapshot is None:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{snapshot_id} does not exist")
        manifest = load_manifest(self.service.controller.blobs, snapshot.manifest_digest)
        return {entry.path: entry.digest for entry in manifest.entries}

    def _text(self, digest: str) -> str:
        return self.service.controller.blobs.read_bytes(digest, max_bytes=4 * 1024 * 1024).decode("utf-8", "replace")

    def _diff(self, before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
        return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
                "changed": sorted(p for p in set(before) & set(after) if before[p] != after[p])}

    def _test_identity(self, files: dict[str, str]) -> dict[str, str]:
        identity: dict[str, str] = {}
        for path, digest in files.items():
            if self.is_test_path(path) and path.endswith(".py"):
                identity.update(identify_tests(path, self.service.controller.blobs.read_bytes(
                    digest, max_bytes=4 * 1024 * 1024)))
        return identity

    def _record(self, phase: str, snapshot_id: str, observation: dict[str, Any], run: TestRun | None) -> str:
        service = self.service
        artifact_refs = []
        if run is not None:
            report = service.controller.blobs.put_bytes(canonical_json_bytes(run.model_dump(mode="json")))
            artifact_refs.append(service._send("artifact.register", {
                "digest": report.digest, "size": report.size, "content_type": "application/json",
                "artifact_kind": "unit_test_report", "sensitivity": "project"}).result["artifact_id"])
        return service._send("evidence.record_execution", {
            "job_id": new_id("job"), "attempt_number": 1, "snapshot_id": snapshot_id, "tool_ref": RUNNER_REF,
            "environment_digest": digest_json(service.backend.capabilities().model_dump(mode="json")),
            "observation_type": f"tdd_{phase}", "observation": observation, "artifact_refs": artifact_refs,
        }).result["evidence_id"]

    def _previous(self, evidence_id: str, phases: tuple[str, ...]) -> dict[str, Any]:
        evidence = self.service.controller.store.evidence(evidence_id)
        if evidence is None or evidence.observation_type not in tuple(f"tdd_{p}" for p in phases):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{evidence_id} is not a {' or '.join(phases)} phase")
        if evidence.observation.get("verdict") != "admitted":
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{evidence_id} was not admitted; the cycle cannot continue")
        return {"snapshot_id": evidence.snapshot_id, **evidence.observation}

    def _integrity(self, before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
        flags = []
        for path in sorted(after):
            if self.is_test_path(path) or not path.endswith(".py") or before.get(path) == after[path]:
                continue
            for item in scan_source(path, self._text(after[path])):
                flags.append({"path": item.path, "line": item.line, "rule": item.rule, "detail": item.detail})
        return flags

    # ------------------------------------------------------------- phases

    def red(self, base_snapshot_id: str, red_snapshot_id: str) -> PhaseResult:
        base, red = self._files(base_snapshot_id), self._files(red_snapshot_id)
        diff = self._diff(base, red)
        touched = diff["added"] + diff["removed"] + diff["changed"]
        reasons: list[str] = []
        production = [p for p in touched if not self.is_test_path(p)]
        if production:
            reasons.append(f"production files changed before a failing test exists: {production[:5]}")
        base_ids, red_ids = self._test_identity(base), self._test_identity(red)
        cycle_tests = sorted(t for t in red_ids if base_ids.get(t) != red_ids[t])
        if not cycle_tests:
            reasons.append("no test was added or changed")
        result = PhaseResult("red", "rejected", reasons, cycle_tests)
        if not reasons:
            base_run, red_run = self.runner.run_snapshot(base_snapshot_id), self.runner.run_snapshot(red_snapshot_id)
            result.run = red_run
            if red_run.status != "completed":
                reasons.append(f"the tests could not be run: {red_run.error}")
            else:
                outcomes = red_run.by_id()
                for test_id in cycle_tests:
                    found = outcomes.get(test_id) or outcomes.get(test_id.split("::", 1)[0])
                    if found is None:
                        reasons.append(f"{test_id} was not collected by the runner")
                    elif found.outcome == "pass":
                        reasons.append(f"{test_id} already passes: it does not specify anything new")
                    elif found.outcome == "skip":
                        reasons.append(f"{test_id} is skipped, which is not a failing test")
                    elif found.kind not in ACCEPTABLE_RED:
                        reasons.append(f"{test_id} fails for the wrong reason ({found.kind}): {found.message[:160]}")
                previously = base_run.by_id() if base_run.status == "completed" else {}
                for test_id, before in previously.items():
                    if test_id in cycle_tests or before.outcome != "pass":
                        continue
                    after = outcomes.get(test_id)
                    if after is None or after.outcome != "pass":
                        reasons.append(f"{test_id} passed before and does not now")
        result.verdict = "rejected" if reasons else "admitted"
        result.evidence_id = self._record("red", red_snapshot_id, {
            "verdict": result.verdict, "reasons": reasons[:20], "base_snapshot_id": base_snapshot_id,
            "cycle_tests": cycle_tests[:100], "test_files": {p: d for p, d in red.items() if self.is_test_path(p)},
            "counts": result.run.counts() if result.run else {}}, result.run)
        return result

    def green(self, red_evidence_id: str, green_snapshot_id: str, *, mutate: bool = True) -> PhaseResult:
        previous = self._previous(red_evidence_id, ("red",))
        return self._advance("green", previous, red_evidence_id, green_snapshot_id, mutate=mutate,
                             require_production_change=True)

    def refactor(self, previous_evidence_id: str, refactor_snapshot_id: str) -> PhaseResult:
        previous = self._previous(previous_evidence_id, ("green", "refactor"))
        return self._advance("refactor", previous, previous_evidence_id, refactor_snapshot_id, mutate=False,
                             require_production_change=True)

    def _advance(self, phase: str, previous: dict[str, Any], previous_evidence_id: str, snapshot_id: str, *,
                 mutate: bool, require_production_change: bool) -> PhaseResult:
        before = self._files(previous["snapshot_id"])
        after = self._files(snapshot_id)
        cycle_tests = list(previous.get("cycle_tests", []))
        reasons: list[str] = []
        expected_tests = previous["test_files"]
        current_tests = {p: d for p, d in after.items() if self.is_test_path(p)}
        if current_tests != expected_tests:
            edited = sorted({p for p in set(expected_tests) | set(current_tests)
                             if expected_tests.get(p) != current_tests.get(p)})
            reasons.append(f"test files were changed in the {phase} phase: {edited[:5]}")
        diff = self._diff(before, after)
        production = [p for p in diff["added"] + diff["removed"] + diff["changed"] if not self.is_test_path(p)]
        if require_production_change and not production:
            reasons.append("no production file changed")
        result = PhaseResult(phase, "rejected", reasons, cycle_tests)  # type: ignore[arg-type]
        if not reasons:
            run = self.runner.run_snapshot(snapshot_id)
            result.run = run
            if run.status != "completed":
                reasons.append(f"the tests could not be run: {run.error}")
            else:
                not_passing = [f"{r.test_id} ({r.outcome}{', ' + r.kind if r.kind else ''})"
                               for r in run.results if r.outcome != "pass"]
                if not_passing:
                    reasons.append(f"not every test passes: {not_passing[:5]}")
                collected = run.by_id()
                missing = [t for t in cycle_tests if t not in collected]
                if missing:
                    reasons.append(f"cycle tests were not run: {missing[:5]}")
        flags = self._integrity(before, after) if not reasons else []
        result.integrity_flags = flags
        if not reasons and mutate:
            result.mutation = self._mutation(before, after, snapshot_id)
            score = result.mutation.get("score")
            if self.min_mutation_score is not None and score is not None and score < self.min_mutation_score:
                reasons.append(f"mutation score {score:.2f} is below the required {self.min_mutation_score:.2f}")
        result.verdict = "rejected" if reasons else ("needs_review" if flags else "admitted")
        if flags:
            result.reasons.append("production code looks test-aware; a reviewer must decide: "
                                  + "; ".join(f"{f['path']}:{f['line']} {f['detail']}" for f in flags[:5]))
        result.evidence_id = self._record(phase, snapshot_id, {
            "verdict": result.verdict, "reasons": result.reasons[:20], "previous_evidence_id": previous_evidence_id,
            "cycle_tests": cycle_tests[:100], "test_files": current_tests, "integrity_flags": flags[:20],
            "counts": result.run.counts() if result.run else {},
            "mutation": result.mutation or {}}, result.run)
        if result.mutation:
            for survivor in result.mutation.get("survivors", []):
                self.service._send("finding.record", {
                    "rule_id": "tdd.surviving_mutant",
                    "claim": f"tests still pass when {survivor['path']}:{survivor['line']} is changed "
                             f"({survivor['description']}); the tests may not constrain this line, or the change "
                             "may be equivalent",
                    "severity": "medium", "path": survivor["path"], "line": survivor["line"],
                    "fingerprint": digest_json({"rule": "tdd.surviving_mutant", **survivor}),
                    "snapshot_id": snapshot_id, "evidence_refs": [result.evidence_id]})
        return result

    def _mutation(self, before: dict[str, str], after: dict[str, str], snapshot_id: str) -> dict[str, Any]:
        mutants = []
        for path in sorted(after):
            if self.is_test_path(path) or not path.endswith(".py") or before.get(path) == after[path]:
                continue
            old = self._text(before[path]) if path in before else None
            new = self._text(after[path])
            mutants += generate_mutants(path, new, lines=changed_lines(old, new),
                                        limit=self.mutation_limit - len(mutants))
            if len(mutants) >= self.mutation_limit:
                break
        if not mutants:
            return {"score": None, "mutants": 0, "survivors": [], "note": "no mutable changed lines"}
        directory = self.service._materialize(snapshot_id)
        try:
            report = run_mutation(self.runner, directory, mutants)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        survivors = [{"path": r.mutant.path, "line": r.mutant.line, "operator": r.mutant.operator,
                      "description": r.mutant.description} for r in report.survivors]
        return {"score": report.score, "mutants": len(report.results), "killed": report.killed,
                "survivors": survivors, "note": report.note}

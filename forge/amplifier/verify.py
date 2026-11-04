"""Generate-and-verify admission rules (ticket D07).

A model's output is admitted by evidence, never by its own confidence:

- a fix is admitted only if the reproduction FAILS on the base snapshot and
  PASSES on the candidate (a "fix" for something that never failed proves
  nothing);
- a generated test suite is admitted only if it PASSES on a known-good
  snapshot and FAILS on a known-bad one (a test that cannot fail measures
  nothing).
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.assessment.evaluator import run_suite
from forge.assessment.suite import EvaluatorSuite
from forge.contracts.ids import new_id


@dataclass
class Admission:
    admitted: bool
    reason: str
    before: str | None = None
    after: str | None = None


def admit_fix(service, criterion_id: str, base_snapshot_id: str, candidate_snapshot_id: str) -> Admission:  # noqa: ANN001
    before = service.assess_black_box(criterion_id, base_snapshot_id)["result"]
    after = service.assess_black_box(criterion_id, candidate_snapshot_id)["result"]
    if before != "fail":
        return Admission(False, f"the reproduction does not fail on the base snapshot ({before}); "
                                "nothing was demonstrated to be broken", before, after)
    if after != "pass":
        return Admission(False, f"the candidate does not pass the reproduction ({after})", before, after)
    return Admission(True, "fails before, passes after", before, after)


def admit_generated_suite(service, suite: EvaluatorSuite, good_snapshot_id: str, bad_snapshot_id: str) -> Admission:  # noqa: ANN001
    snapshot_dir = service._materialize(good_snapshot_id)
    try:
        good = run_suite(suite, snapshot_dir, service.backend, service.work_root / new_id("job"),
                         python_path=service.python_path, limits=service.limits,
                         toolchain_paths=service.python_paths).result
    finally:
        import shutil

        shutil.rmtree(snapshot_dir, ignore_errors=True)
    if good != "pass":
        return Admission(False, f"the generated tests do not pass on the known-good code ({good})", good)
    if not service.suite_can_fail(suite, bad_snapshot_id):
        return Admission(False, "the generated tests also pass on the known-bad code; they cannot detect the defect",
                         good, "pass")
    return Admission(True, "passes on known-good code and fails on known-bad code", good, "fail")

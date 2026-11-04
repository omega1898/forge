"""Hidden-evaluator grading of a learner's code, in the sandbox.

The learner's files are written to a fresh directory and the exercise's
hidden suite runs against them exactly as the protected evaluator runs a
candidate. The suite never enters the learner's working directory or any
help context.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from forge.assessment.evaluator import run_suite
from forge.assessment.suite import EvaluatorSuite
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path


class SandboxCodeGrader:
    def __init__(self, service) -> None:  # noqa: ANN001 - AssessmentService
        self.service = service

    def __call__(self, files: dict[str, str], suite_document: dict[str, Any]) -> tuple[str, str]:
        suite = EvaluatorSuite.model_validate(suite_document)
        directory = self.service.work_root / f"{new_id('job')}-learner"
        try:
            directory.mkdir(parents=True)
            os.chmod(directory, 0o755)
            for path, text in files.items():
                check_relative_path(path)
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
                os.chmod(target, 0o644)
            run = run_suite(suite, directory, self.service.backend, self.service.work_root / new_id("job"),
                            python_path=self.service.python_path, limits=self.service.limits,
                            toolchain_paths=self.service.python_paths)
        except ValueError as error:
            return "error", f"submission refused: {error}"
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        if run.result == "pass":
            return "pass", f"all {len(run.cases)} hidden checks pass"
        failed = sum(1 for case in run.cases if case.outcome != "pass")
        # Report how many hidden checks failed, never which inputs they used.
        return ("fail" if run.result == "fail" else "error"), f"{failed} of {len(run.cases)} hidden checks fail"

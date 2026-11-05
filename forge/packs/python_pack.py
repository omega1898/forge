"""The Python language pack: what FORGE can do for Python projects, and with which tools.

A pack is a descriptor, not executable policy: it names the admitted tools,
the evaluators it provides, the task templates it supports, and the facts
that select it. Tools are admitted by digest through the ToolRegistry; a
repository cannot substitute its own implementation.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from forge.contracts.base import StrictModel
from forge.tools.registry import ToolRegistry


class PackDescriptor(StrictModel):
    pack_id: str
    version: int
    languages: list[str]
    tools: list[str]
    evaluators: list[str]
    task_templates: list[str]
    selected_by_facts: list[str]
    limitations: list[str]


PYTHON_PACK = PackDescriptor(
    pack_id="python",
    version=1,
    languages=["python"],
    tools=["python", "ruff"],
    evaluators=["black_box_cases", "analyzer_verdict", "package_install", "unit_tests", "mutation_score",
                "performance_comparison"],
    task_templates=["build_tool", "review", "bug_fix", "test_strategy", "tech_debt", "tdd_cycle"],
    selected_by_facts=["language.python", "build.python_packaging"],
    limitations=[
        "unit tests run with FORGE's standard-library runner (unittest classes and plain test functions);"
        " pytest fixtures and plugins are not supported, and in-process results are workflow evidence only",
        "type checking (mypy) is not admitted yet",
        "evidence is produced on the Linux backend only; other platforms are not_run",
    ],
)


def admit_python_tools(registry: ToolRegistry) -> list[str]:
    """Admit the host's Python interpreter and ruff (if present), pinned by digest."""
    admitted = []
    from forge.assessment.service import interpreter

    python_path, python_dirs = interpreter()
    # An interpreter outside /usr (pyenv, a CI tool cache) needs its own tree mounted in the sandbox.
    registry.admit("python", Path(python_path), version=sys.version.split()[0], toolchain_paths=python_dirs)
    admitted.append("python")
    ruff = shutil.which("ruff")
    if ruff:
        real = Path(os.path.realpath(ruff))
        registry.admit("ruff", real, version="host", toolchain_paths=[str(real.parent.parent)])
        admitted.append("ruff")
    return admitted

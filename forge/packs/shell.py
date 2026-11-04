"""Shell packs (ticket F23): Bash available; PowerShell and OCaml reported unavailable.

Hostile arguments fail safely. A script tool declares the arguments it
takes; each value is checked against its declared kind before anything
runs, and accepted values reach the script as separate argv entries, never
through a shell command line, so `$(id)` or `; rm -rf /` arrive as inert
text. Values that look like options are refused unless the kind allows
them, paths must be clean, relative and present in the snapshot, and
network targets must pass the grant's TargetScope.

A small deterministic linter covers the most damaging Bash mistakes while
shellcheck is not installed: no strict mode, eval of expanded data,
unquoted argument expansion, download piped to a shell, and `rm -rf` on an
unguarded variable path.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from forge.contracts.base import Slug, StrictModel
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.ids import new_id
from forge.contracts.paths import check_relative_path
from forge.packs.python_pack import PackDescriptor
from forge.sandbox.base import ExecutionObservation, SandboxSpec
from forge.tools.scope import TargetScope, check_target

BASH_PACK = PackDescriptor(
    pack_id="bash", version=1, languages=["bash"], tools=["bash"], evaluators=["black_box_cases", "bash_lint"],
    task_templates=["build_tool", "review"], selected_by_facts=["language.shell"],
    limitations=["shellcheck is not installed; FORGE's five-rule linter is a floor, not a replacement",
                 "PowerShell (pwsh) and OCaml are not installed on this host; their packs are not_run"])

MAX_ARGUMENT = 4096


class ArgumentSpec(StrictModel):
    name: Slug
    kind: Literal["path", "integer", "choice", "text", "target"]
    choices: list[str] = Field(default_factory=list)
    pattern: str | None = None            # required for text: no free-form text by default
    minimum: int | None = None
    maximum: int | None = None


class ScriptTool(StrictModel):
    tool_id: Slug
    script: str
    arguments: list[ArgumentSpec] = Field(max_length=32)


def validate_arguments(tool: ScriptTool, values: list[str], *, snapshot_paths: set[str] | None = None,
                       scope: TargetScope | None = None, resolver=None) -> list[str]:  # noqa: ANN001
    """Return the arguments to pass, or raise with the first reason a value is refused."""
    if len(values) != len(tool.arguments):
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{tool.tool_id} takes {len(tool.arguments)} arguments, "
                                                    f"got {len(values)}")
    accepted = []
    for spec, value in zip(tool.arguments, values, strict=True):
        def refuse(reason: str) -> ForgeError:
            return ForgeError(ErrorCode.INVALID_REQUEST, f"argument {spec.name} refused: {reason}")

        if not isinstance(value, str) or len(value) > MAX_ARGUMENT:
            raise refuse("not text or too long")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise refuse("contains control characters (NUL, newline or similar)")
        if spec.kind == "integer":
            if not re.fullmatch(r"-?[0-9]{1,18}", value):
                raise refuse("not an integer")
            number = int(value)
            if (spec.minimum is not None and number < spec.minimum) or \
                    (spec.maximum is not None and number > spec.maximum):
                raise refuse(f"outside {spec.minimum}..{spec.maximum}")
            accepted.append(str(number))
            continue
        if value.startswith("-"):
            raise refuse("looks like an option")
        if spec.kind == "path":
            try:
                check_relative_path(value)
            except ValueError as error:
                raise refuse(str(error)) from None
            if snapshot_paths is not None and value not in snapshot_paths:
                raise refuse("not a file in the snapshot")
        elif spec.kind == "choice":
            if value not in spec.choices:
                raise refuse(f"not one of {spec.choices}")
        elif spec.kind == "text":
            if spec.pattern is None or not re.fullmatch(spec.pattern, value):
                raise refuse("does not match the declared pattern")
        elif spec.kind == "target":
            if scope is None:
                raise refuse("no target scope was granted")
            check_target(value, scope, resolver=resolver)
        accepted.append(value)
    return accepted


def run_script(service, snapshot_id: str, tool: ScriptTool, values: list[str], **checks) -> ExecutionObservation:  # noqa: ANN001, ANN003
    """Validate, then run the script with bash in the sandbox; arguments travel as argv."""
    bash = shutil.which("bash", path="/usr/bin:/bin")
    if bash is None:
        raise ForgeError(ErrorCode.MISSING_CAPABILITY, "bash is not installed")
    check_relative_path(tool.script)
    arguments = validate_arguments(tool, values, **checks)
    directory = service._materialize(snapshot_id)
    work = service.work_root / new_id("job")
    try:
        return service.backend.run(SandboxSpec(
            job_key="script_tool", input_dir=str(directory), cwd="/input",
            argv=[bash, "--noprofile", "--norc", tool.script, *arguments], limits=service.limits), work)
    finally:
        destroy = getattr(service.backend, "destroy", None)
        if destroy is not None:
            destroy(work)
        shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------- lint


@dataclass(frozen=True)
class LintFinding:
    rule: str
    line: int
    message: str


def _strip_comments(line: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", line)


def lint_bash(text: str) -> list[LintFinding]:
    findings: list[LintFinding] = []
    lines = text.splitlines()
    head = "\n".join(_strip_comments(line) for line in lines[:15])
    if not (re.search(r"set\s+-[a-z]*e[a-z]*u|set\s+-[a-z]*u[a-z]*e|set\s+-euo\s+pipefail", head)
            or ("set -e" in head and "set -u" in head)):
        findings.append(LintFinding("BASH001", 1, "no strict mode (set -euo pipefail): errors and unset variables "
                                                  "are silently ignored"))
    for number, raw in enumerate(lines, 1):
        line = _strip_comments(raw)
        if re.search(r"\beval\b[^\n]*\$", line):
            findings.append(LintFinding("BASH002", number, "eval of expanded data executes it as code"))
        unquoted = re.sub(r'"[^"]*"', '""', re.sub(r"'[^']*'", "''", line))
        if re.search(r"\$(\{)?[@*1-9]\b|\$\{?[@*]\}?", unquoted):
            findings.append(LintFinding("BASH003", number, "unquoted argument expansion splits and globs its value"))
        if re.search(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh\b", line):
            findings.append(LintFinding("BASH004", number, "downloads code and runs it without verification"))
        # A ${VAR:?} guard does not match this pattern, so guarded paths are not flagged.
        if re.search(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+[\"']?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?(/|[\"']?\s*$)", line):
            findings.append(LintFinding("BASH005", number, "rm -r on a variable path without a ${VAR:?} guard "
                                                           "deletes from / when the variable is empty"))
    return findings

"""`forge.toml`: how to build a project and which test cases it must pass.

One small file in the project folder tells FORGE everything `forge check`
needs. Example (an Ada program; the same shape works for Fortran, C, C++,
COBOL, OCaml, ARM C under emulation, and Python scripts):

    [build]
    toolchain = "ada"              # ada | fortran | c | cpp | cobol | ocaml | c_arm | python
    sources = ["main.adb"]         # Ada: the main unit; gnatmake finds the units it uses

    [[tests]]
    id = "high_aoa_no_flaps"
    args = ["20", "100", "0"]
    stdout = "WARN\\n"
    exit_code = 0
    verifies = ["HLR-1"]

Each test runs the built program with `args` (and optional `stdin` and input
`files`, reachable as `{case}/name` in the arguments) and checks every
expectation it states: `exit_code` (a number or "nonzero", default 0),
exact `stdout`, `stdout_json`, or `stderr_contains`. `verifies` names the
requirements the test verifies; the compliance trace uses it.

The file in the project is only a proposal. The first `forge check` turns it
into a protected suite that you accepted; later edits to the tests (by you or
by a model) are not used until you accept them again (`--accept-tests`).
"""

from __future__ import annotations

import base64
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from forge.assessment.suite import EvaluatorSuite
from forge.contracts.base import StrictModel
from forge.contracts.digests import digest_json
from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.paths import check_relative_path

PROJECT_FILE = "forge.toml"
Toolchain = Literal["ada", "fortran", "c", "cpp", "cobol", "ocaml", "c_arm", "python"]
COMPILED = ("ada", "fortran", "cobol", "ocaml", "c_arm")
NATIVE = ("c", "cpp")
COVERAGE = ("ada", "fortran", "c", "cpp")
SUFFIXES: dict[str, tuple[str, ...]] = {
    "ada": (".adb",), "fortran": (".f90", ".f95", ".f03", ".f08", ".f", ".for", ".f77"), "c": (".c",),
    "cpp": (".cpp", ".cc", ".cxx"), "cobol": (".cbl", ".cob"), "ocaml": (".ml",), "python": (".py",),
}
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class BuildSection(StrictModel):
    toolchain: Toolchain
    sources: list[str] = Field(default_factory=list, max_length=64)
    entry: str | None = None                       # python: the script to run
    standard: str | None = None                    # c / cpp: c11, c17, c++17, c++20 ...
    include_dirs: list[str] = Field(default_factory=list, max_length=16)
    defines: list[str] = Field(default_factory=list, max_length=32)
    case_wall_ms: int = Field(default=10_000, ge=100, le=600_000)

    @field_validator("sources", "include_dirs")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        for path in value:
            check_relative_path(path)
            if path.startswith("-"):
                raise ValueError(f"{path!r} would be read as a compiler option")
        return value

    @model_validator(mode="after")
    def _complete(self) -> BuildSection:
        if self.toolchain == "python":
            if not self.entry or not self.entry.endswith(".py"):
                raise ValueError("a python project names its script: entry = \"main.py\"")
            check_relative_path(self.entry)
        elif not self.sources:
            raise ValueError(f"a {self.toolchain} project lists its sources: sources = [\"...\"]")
        return self


class CheckCase(StrictModel):
    id: str
    description: str = ""
    args: list[str] = Field(default_factory=list, max_length=64)
    stdin: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    exit_code: int | Literal["nonzero"] | None = 0
    stdout: str | None = None
    stdout_json: JsonValue = None
    stderr_contains: str | None = None
    verifies: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError(f"test id {value!r} must start with a lowercase letter and use only lowercase "
                             "letters, digits and underscores")
        return value


class ProjectSpec(StrictModel):
    build: BuildSection
    tests: list[CheckCase] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique(self) -> ProjectSpec:
        ids = [test.id for test in self.tests]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"test ids must be unique; repeated: {duplicates}")
        return self

    def entrypoint(self) -> list[str]:
        if self.build.toolchain == "python":
            return ["{python}", "-I", f"/input/{self.build.entry}"]
        return ["/input/program"]

    def suite(self) -> dict[str, Any]:
        """The protected black-box suite these tests become."""
        cases = []
        for test in self.tests:
            expect: dict[str, Any] = {"exit_code": test.exit_code}
            if test.stdout is not None:
                expect["stdout_text"] = test.stdout
            if test.stdout_json is not None:
                expect["stdout_json"] = test.stdout_json
            if test.stderr_contains is not None:
                expect["stderr_contains"] = test.stderr_contains
            cases.append({"case_id": test.id, "description": (test.description or test.id)[:500], "args": test.args,
                          "stdin_b64": base64.b64encode(test.stdin.encode()).decode() if test.stdin else "",
                          "files": {name: base64.b64encode(text.encode()).decode()
                                    for name, text in test.files.items()},
                          "expect": expect})
        suite = EvaluatorSuite.model_validate({
            "suite_id": "forge_project", "revision": 1, "description": "Tests from forge.toml",
            "entrypoint": self.entrypoint(), "cases": cases, "case_wall_ms": self.build.case_wall_ms})
        return suite.model_dump(mode="json")

    def digest(self) -> str:
        """Identity of what is accepted: the build and the tests, not comments or ordering of keys."""
        return digest_json(self.model_dump(mode="json"))

    def verifies(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for test in self.tests:
            for requirement in test.verifies:
                found.setdefault(requirement, []).append(f"{PROJECT_FILE}:test {test.id}")
        return found


def parse_project(raw: bytes) -> ProjectSpec:
    """Read forge.toml; errors say what to change, in the file's own terms."""
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{PROJECT_FILE} is not valid TOML: {error}") from None
    try:
        return ProjectSpec.model_validate(data)
    except ValidationError as error:
        problems = []
        for item in error.errors()[:8]:
            where = ".".join(str(part) for part in item["loc"])
            problems.append(f"{where}: {item['msg']}")
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{PROJECT_FILE} needs changes: " + "; ".join(problems)) from None


# --------------------------------------------------------------- forge check init


def detect(folder: Path) -> tuple[str, list[str]]:
    """Guess the toolchain and main sources from the files in a folder (never from their contents' claims)."""
    files = [p.relative_to(folder).as_posix() for p in sorted(folder.rglob("*"))
             if p.is_file() and not any(part.startswith(".") for part in p.relative_to(folder).parts)]
    counts = {name: [f for f in files if f.lower().endswith(suffixes)] for name, suffixes in SUFFIXES.items()}
    toolchain = max(counts, key=lambda name: (len(counts[name]), name != "python"))
    if not counts[toolchain]:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"no source files FORGE recognizes in {folder}")
    sources = counts[toolchain]
    if toolchain == "ada":
        mains = [f for f in sources if Path(f).stem.lower() in ("main", "driver")]
        sources = mains or sources[:1]
    if toolchain == "python":
        mains = [f for f in sources if Path(f).name in ("main.py", "__main__.py", "cli.py")]
        sources = mains or sources[:1]
    return toolchain, sources


def starter(toolchain: str, sources: list[str]) -> str:
    """A forge.toml to edit: the build section filled in, and one example test to replace."""
    lines = ["# How FORGE builds and tests this project. Edit the example test, then run: forge check", "",
             "[build]", f'toolchain = "{toolchain}"']
    if toolchain == "python":
        lines.append(f"entry = {json.dumps(sources[0])}")
    else:
        lines.append("sources = [" + ", ".join(json.dumps(s) for s in sources) + "]")
    lines += ["", "# One [[tests]] block per test case. Every expectation you write is checked.",
              "[[tests]]", 'id = "example"', 'description = "replace with a real case"', 'args = []',
              "exit_code = 0", '# stdout = "expected output\\n"', '# verifies = ["REQ-1"]', ""]
    return "\n".join(lines)

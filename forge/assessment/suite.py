"""Protected black-box evaluator suites.

A suite says how to run the candidate program and what each case must
produce. It is stored in the controller's artifact store, never in the
candidate's workspace, and its expected values never enter the sandbox:
the candidate only ever sees its own inputs. Comparison happens afterwards,
in trusted code.

Templating is deliberately tiny: "{python}" is replaced by the admitted
interpreter path and "{case}" by the read-only directory holding the case's
input files. There is no shell, no environment expansion, no glob.
"""

from __future__ import annotations

import base64
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from forge.contracts.base import Slug, StrictModel, Text
from forge.contracts.digests import canonical_json_bytes, sha256_digest
from forge.contracts.paths import check_relative_path

MAX_CASE_FILE_BYTES = 1024 * 1024
ALLOWED_PLACEHOLDERS = ("{python}", "{case}")


class ExpectedResult(StrictModel):
    """What a correct program does. Every given field must match."""

    exit_code: int | Literal["nonzero"] | None = None
    stdout_json: JsonValue = None
    stdout_text: str | None = None
    stderr_contains: str | None = None

    @model_validator(mode="after")
    def _something(self) -> ExpectedResult:
        if self.exit_code is None and self.stdout_json is None and self.stdout_text is None:
            raise ValueError("a case must check the exit code or the output")
        if self.stdout_json is not None and self.stdout_text is not None:
            raise ValueError("choose stdout_json or stdout_text, not both")
        return self


class BlackBoxCase(StrictModel):
    case_id: Slug
    description: Text(500)
    args: list[str] = Field(default_factory=list, max_length=64)
    stdin_b64: str = ""
    # Files placed read-only under /case for this case, as base64 bytes.
    files: dict[str, str] = Field(default_factory=dict)
    expect: ExpectedResult

    @field_validator("files")
    @classmethod
    def _files(cls, value: dict[str, str]) -> dict[str, str]:
        for path, encoded in value.items():
            check_relative_path(path)
            if len(base64.b64decode(encoded, validate=True)) > MAX_CASE_FILE_BYTES:
                raise ValueError(f"case file {path} is larger than {MAX_CASE_FILE_BYTES} bytes")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: list[str]) -> list[str]:
        for arg in value:
            if "\x00" in arg or "{" in arg.replace("{case}", ""):
                raise ValueError(f"argument {arg!r} contains an unsupported placeholder or NUL")
        return value

    def file_bytes(self) -> dict[str, bytes]:
        return {path: base64.b64decode(data) for path, data in self.files.items()}

    def stdin(self) -> bytes:
        return base64.b64decode(self.stdin_b64) if self.stdin_b64 else b""


class EvaluatorSuite(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite_id: Slug
    revision: int = Field(ge=1)
    description: Text(1000)
    # Relative to the snapshot root, e.g. ["{python}", "-I", "src/reader/cli.py"].
    entrypoint: list[str] = Field(min_length=1, max_length=32)
    cases: list[BlackBoxCase] = Field(min_length=1, max_length=500)
    case_wall_ms: int = Field(default=10_000, ge=100, le=600_000)

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint(cls, value: list[str]) -> list[str]:
        for part in value:
            if "{" in part and part not in ALLOWED_PLACEHOLDERS:
                raise ValueError(f"entrypoint element {part!r} has an unsupported placeholder")
        return value

    @model_validator(mode="after")
    def _unique(self) -> EvaluatorSuite:
        ids = [case.case_id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("case_id values must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        return sha256_digest(self.canonical_bytes())

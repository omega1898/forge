"""Admitted tools: exact executables, recorded by digest.

A project may ask for a tool by ID ("ruff") but can never supply its
implementation: a file named `ruff` inside the repository, or an earlier
entry on PATH, is irrelevant. The registry records the absolute path and
SHA-256 of each admitted executable, and refuses to run it if the bytes
changed since admission.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from forge.contracts.base import Slug, StrictModel
from forge.contracts.digests import Digest
from forge.contracts.errors import ErrorCode, ForgeError


class ToolDefinition(StrictModel):
    tool_id: Slug
    version: str
    executable: str
    executable_digest: Digest
    # Read-only host directories the tool needs inside the sandbox.
    toolchain_paths: list[str] = Field(default_factory=list)
    side_effect_class: Literal["read_only", "scratch_only"] = "read_only"


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def admit(
        self, tool_id: str, executable: Path, *, version: str, toolchain_paths: list[str] | None = None,
        side_effect_class: Literal["read_only", "scratch_only"] = "read_only",
    ) -> ToolDefinition:
        path = Path(executable)
        if not path.is_absolute():
            raise ForgeError(ErrorCode.INVALID_REQUEST, "tool executables must be absolute paths")
        real = Path(os.path.realpath(path))
        if not real.is_file() or not os.access(real, os.X_OK):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{real} is not an executable file")
        definition = ToolDefinition(
            tool_id=tool_id,
            version=version,
            executable=str(real),
            executable_digest=file_digest(real),
            toolchain_paths=toolchain_paths or [],
            side_effect_class=side_effect_class,
        )
        self._tools[tool_id] = definition
        return definition

    def resolve(self, tool_id: str) -> ToolDefinition:
        definition = self._tools.get(tool_id)
        if definition is None:
            raise ForgeError(ErrorCode.MISSING_CAPABILITY, f"tool {tool_id!r} is not admitted on this host")
        try:
            current = file_digest(Path(definition.executable))
        except OSError:
            raise ForgeError(ErrorCode.MISSING_CAPABILITY, f"tool {tool_id!r} executable is missing") from None
        if current != definition.executable_digest:
            raise ForgeError(
                ErrorCode.MISSING_CAPABILITY,
                f"tool {tool_id!r} executable changed since admission; re-admit it deliberately",
                details={"admitted": definition.executable_digest, "current": current},
            )
        return definition

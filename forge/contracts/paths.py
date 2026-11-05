"""Relative paths that cross module boundaries.

Inside FORGE a project file is always named by a normalized, relative,
forward-slash path such as "src/reader.py". Host paths ("/home/me/x",
"C:\\x") never cross a boundary, because a path supplied by a repository,
a model, or a manifest could otherwise point outside the project.
"""

from __future__ import annotations

import unicodedata
from pathlib import PurePosixPath

from forge.contracts.errors import safe_preview

# Names Windows refuses regardless of extension (CON.txt is still CON).
WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)
_WINDOWS_FORBIDDEN_CHARS = set('<>:"|?*\\')


def check_relative_path(value: object) -> str:
    """Return value if it is a clean relative path, else raise ValueError."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"path must be a non-empty string, got {safe_preview(value)}")
    if "\x00" in value or "\\" in value:
        raise ValueError(f"path contains NUL or backslash: {safe_preview(value)}")
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        raise ValueError(f"path must be relative: {safe_preview(value)}")
    parts = PurePosixPath(value).parts
    if any(part in ("..", ".") for part in parts) or "/".join(parts) != value:
        raise ValueError(f"path must be normalized without '.', '..' or '//': {safe_preview(value)}")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"path contains control characters: {safe_preview(value)}")
    return value


def portability_problems(path: str, target: str) -> list[str]:
    """Reasons a path cannot be created faithfully on a target OS ("linux", "windows", "macos")."""
    problems: list[str] = []
    if target == "windows":
        for part in path.split("/"):
            stem = part.split(".")[0].lower()
            if stem in WINDOWS_RESERVED:
                problems.append(f"reserved Windows name {part!r}")
            if any(char in _WINDOWS_FORBIDDEN_CHARS for char in part):
                problems.append(f"character not allowed on Windows in {part!r}")
            if part.endswith((" ", ".")):
                problems.append(f"trailing space or dot not preserved on Windows in {part!r}")
    return problems


def collision_key(path: str, target: str) -> str:
    """Two paths with the same key would become one file on the target."""
    if target == "windows":
        return path.lower()
    if target == "macos":
        # Default APFS volumes are case-insensitive and normalization-insensitive.
        return unicodedata.normalize("NFD", path).lower()
    return path

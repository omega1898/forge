"""schema_version handling for every serialized object.

Versions are "MAJOR.MINOR" text. A different MAJOR means an incompatible
contract. A higher MINOR than this installation knows means the object came
from a newer FORGE and may contain fields this version would misread, so it is
refused with a clear message instead of a confusing "unexpected field" error.
"""

from __future__ import annotations

import re

from forge.contracts.errors import ErrorCode, ForgeError, safe_preview

SCHEMA_VERSION = "1.0"
_SUPPORTED_MAJOR = 1
_SUPPORTED_MINOR = 0
_VERSION_RE = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")


def check_schema_version(value: object, *, what: str = "object") -> str:
    if not isinstance(value, str) or _VERSION_RE.match(value) is None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"{what} has missing or malformed schema_version {safe_preview(value)}; "
            f'expected text such as "{SCHEMA_VERSION}"',
        )
    major_text, minor_text = value.split(".")
    major, minor = int(major_text), int(minor_text)
    if major != _SUPPORTED_MAJOR or minor > _SUPPORTED_MINOR:
        raise ForgeError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"{what} uses schema_version {value}; this FORGE installation supports "
            f"{_SUPPORTED_MAJOR}.0 through {_SUPPORTED_MAJOR}.{_SUPPORTED_MINOR}",
            details={"received": value, "supported": SCHEMA_VERSION},
        )
    return value

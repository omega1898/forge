"""Typed errors that cross module and client boundaries.

Every failure FORGE reports to a client uses one of a fixed set of codes so
that clients can react without parsing English text. Messages must be safe to
show to a model-facing client: no secrets, no environment variables, no stack
traces.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_VERSION = "unsupported_version"
    PERMISSION_DENIED = "permission_denied"
    STALE_VERSION = "stale_version"
    MISSING_CAPABILITY = "missing_capability"
    WORKSPACE_CHANGED = "workspace_changed"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    ARTIFACT_MISSING = "artifact_missing"
    INTERNAL_ERROR = "internal_error"


# A retryable error may succeed if the same request is sent again later
# without changes. stale_version is NOT retryable as-is: the client must
# re-read current state and decide again.
_RETRYABLE = {ErrorCode.RESOURCE_UNAVAILABLE, ErrorCode.DEADLINE_EXCEEDED}


class ForgeError(Exception):
    """An error with a stable code, a safe message, and related object IDs."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        related_ids: tuple[str, ...] | list[str] = (),
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ErrorCode(code)
        self.message = message
        self.related_ids = tuple(related_ids)
        self.details = dict(details or {})

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "related_ids": list(self.related_ids),
            "retryable": self.retryable,
            "details": self.details,
        }

    def __repr__(self) -> str:
        return f"ForgeError({self.code.value!r}, {self.message!r})"


def safe_preview(value: object, limit: int = 80) -> str:
    """Return a short, printable description of untrusted input for messages.

    Hostile input can be enormous or contain control characters; echoing it
    unbounded into logs or error messages is itself a risk.
    """
    text = repr(value)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text

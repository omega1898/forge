"""Time conventions.

Records store wall-clock timestamps as UTC RFC3339 text, always with
microseconds and a trailing 'Z', for example 2026-11-04T09:30:00.000000Z.
The fixed width means text order equals time order.

Wall-clock time is for records only. Elapsed budgets (timeouts) must use a
monotonic clock, because wall clocks can jump when the system time changes.

The clock is injectable so tests can control time instead of sleeping.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from pydantic import AfterValidator, WithJsonSchema

from forge.contracts.errors import ErrorCode, ForgeError, safe_preview

RFC3339_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A test clock that starts at a fixed instant and only moves when told."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware start time")

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


def format_timestamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("refusing to format a timestamp without a timezone")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_timestamp(text: object) -> datetime:
    """Parse RFC3339 text that carries an explicit offset; reject anything else."""
    if not isinstance(text, str) or re.match(RFC3339_PATTERN, text) is None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"invalid timestamp {safe_preview(text)}: expected UTC RFC3339 such as "
            "2026-11-04T09:30:00Z",
        )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST, f"invalid timestamp {safe_preview(text)}"
        ) from None
    return parsed.astimezone(UTC)


def _check(value: str) -> str:
    try:
        return format_timestamp(parse_timestamp(value))
    except ForgeError as error:
        raise ValueError(error.message) from None


# Accepts any RFC3339 text with an offset and normalizes it to the stored form.
Timestamp = Annotated[
    str,
    AfterValidator(_check),
    WithJsonSchema({"type": "string", "pattern": RFC3339_PATTERN}),
]

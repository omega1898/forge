"""Shared building blocks for every contract model.

StrictModel rejects unexpected fields (a typo or an injected "approved": true
is an error, not silently ignored), refuses type coercion (the text "5" is not
the number 5), and is immutable after validation.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from forge.contracts.ids import AnyId


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# Lowercase snake_case names for profiles, methods, environments, actions.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

# Client-chosen key that makes a retried mutation safe to deliver twice.
IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]

# Control characters other than tab/newline/carriage return have no business in
# human-readable fields and are a common smuggling vector for terminal escape
# sequences and log injection.
_FORBIDDEN_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _no_control_chars(value: str) -> str:
    if _FORBIDDEN_CONTROL.search(value):
        raise ValueError("text contains forbidden control characters")
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def Text(max_length: int):  # noqa: N802 - used like a type
    return Annotated[
        str,
        StringConstraints(min_length=1, max_length=max_length),
        AfterValidator(_no_control_chars),
    ]


class RevisionRef(StrictModel):
    """A reference to a specific revision of a record.

    Meaning can change between revisions, so a test assessed against
    requirement revision 2 must not silently satisfy revision 3.
    """

    id: AnyId
    revision: int = Field(ge=1)

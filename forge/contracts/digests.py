"""Content digests: 'sha256:' followed by 64 lowercase hexadecimal characters.

A digest is a fingerprint of exact bytes. Two different byte sequences
practically never share a SHA-256 digest, so a digest identifies content.

To fingerprint structured data (a request, a manifest) we first convert it to
one canonical byte form. Without that step, {"a":1,"b":2} and {"b":2,"a":1}
would get different digests although they mean the same thing.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

from pydantic import AfterValidator, WithJsonSchema

from forge.contracts.errors import ErrorCode, ForgeError, safe_preview

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DIGEST_RE = re.compile(DIGEST_PATTERN)


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON-compatible data to one canonical UTF-8 byte form.

    Sorted keys, no insignificant whitespace, and NaN/Infinity rejected
    (they are not valid JSON and have no single canonical spelling).
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest_json(value: Any) -> str:
    return sha256_digest(canonical_json_bytes(value))


def validate_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST_RE.match(value) is None:
        raise ForgeError(
            ErrorCode.INVALID_REQUEST,
            f"invalid digest {safe_preview(value)}: expected sha256:<64 lowercase hex>",
        )
    return value


def _check(value: str) -> str:
    try:
        return validate_digest(value)
    except ForgeError as error:
        raise ValueError(error.message) from None


Digest = Annotated[
    str,
    AfterValidator(_check),
    WithJsonSchema({"type": "string", "pattern": DIGEST_PATTERN}),
]

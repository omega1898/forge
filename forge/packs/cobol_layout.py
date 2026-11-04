"""Mainframe record layouts and exact comparison (tickets F19, F20).

COBOL data lives in fixed-width records described by a copybook. This module
reads the common subset of copybook syntax and decodes records exactly:

- `PIC X(n)`: text, n bytes in the record's code page (EBCDIC cp037 by
  default; ASCII-based files use latin-1 or utf-8 explicitly);
- `PIC 9(n)`, `PIC S9(n)V9(m)` (DISPLAY, "zoned decimal"): one digit per byte,
  the sign carried in the zone of the last byte;
- the same with `COMP-3` ("packed decimal"): two digits per byte and a sign
  nibble.

Values are `Decimal`, never float, so a one-cent difference survives.
`cobol_round` reproduces COBOL's ROUNDED (half away from zero), which
differs from Java's and Python's banker's rounding on exact halves.
`compare_records` reports every field difference; an accepted difference
must be listed explicitly, by record key and field, and is reported as
accepted rather than dropped.

What is not supported, and is refused rather than guessed: OCCURS DEPENDING
ON, REDEFINES, COMP/COMP-4/COMP-5 binary, floating point (COMP-1/COMP-2),
SIGN SEPARATE and national (PIC N) fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from forge.contracts.errors import ErrorCode, ForgeError

_LINE = re.compile(
    r"^\s*(?P<level>\d{2})\s+(?P<name>[A-Z0-9][A-Z0-9-]*)"
    r"(?:\s+PIC(?:TURE)?\s+(?P<pic>[^\s.]+))?(?:\s+(?P<usage>COMP-3|COMPUTATIONAL-3|DISPLAY))?\s*\.\s*$"
)
_UNSUPPORTED = ("OCCURS", "REDEFINES", "COMP-1", "COMP-2", "COMP-4", "COMP-5", "BINARY", "SIGN", "PIC N",
                "COMPUTATIONAL ", "COMP.")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str          # "text" | "zoned" | "packed"
    offset: int
    length: int        # bytes in the record
    digits: int = 0
    scale: int = 0
    signed: bool = False


@dataclass(frozen=True)
class Layout:
    name: str
    fields: tuple[Field, ...]

    @property
    def record_length(self) -> int:
        return sum(field.length for field in self.fields)


def _expand(picture: str) -> str:
    return re.sub(r"([X9])\((\d+)\)", lambda m: m.group(1) * int(m.group(2)), picture.upper())


def parse_copybook(text: str) -> Layout:
    """Elementary items in order; group levels only name the record."""
    fields: list[Field] = []
    record_name = ""
    offset = 0
    statement = ""
    for raw in text.splitlines():
        line = raw[6:72] if len(raw) > 6 and raw[:6].strip().isdigit() else raw  # fixed-format sequence area
        if not line.strip() or line.lstrip().startswith("*"):
            continue
        statement += " " + line.strip()
        if not statement.rstrip().endswith("."):
            continue
        current, statement = statement.strip().upper(), ""
        if any(word in current + " " for word in _UNSUPPORTED):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"unsupported copybook feature in: {current[:80]}")
        match = _LINE.match(current)
        if not match:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"cannot read copybook statement: {current[:80]}")
        if match["pic"] is None:
            record_name = record_name or match["name"]
            continue
        picture = _expand(match["pic"])
        if set(picture) <= {"X"}:
            fields.append(Field(match["name"], "text", offset, len(picture)))
            offset += len(picture)
            continue
        numeric = re.fullmatch(r"(S?)(9+)(?:V(9+))?", picture)
        if not numeric:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"unsupported picture {match['pic']}")
        integer, fraction = len(numeric.group(2)), len(numeric.group(3) or "")
        digits = integer + fraction
        packed = match["usage"] in ("COMP-3", "COMPUTATIONAL-3")
        length = digits // 2 + 1 if packed else digits
        fields.append(Field(match["name"], "packed" if packed else "zoned", offset, length, digits, fraction,
                            bool(numeric.group(1))))
        offset += length
    if statement.strip():
        raise ForgeError(ErrorCode.INVALID_REQUEST, "copybook ends inside a statement")
    if not fields:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "copybook has no elementary fields")
    return Layout(record_name or "RECORD", tuple(fields))


def _zoned(data: bytes, field: Field, encoding: str) -> Decimal:
    if encoding.lower() in ("cp037", "cp500", "cp1140"):
        zones = [byte >> 4 for byte in data]
        digits = [byte & 0x0F for byte in data]
        if any(z != 0xF for z in zones[:-1]) or any(d > 9 for d in digits):
            raise ValueError(f"{field.name}: not a zoned decimal")
        sign_zone = zones[-1]
        negative = sign_zone == 0xD
        if sign_zone not in (0xC, 0xD, 0xF) or (negative and not field.signed):
            raise ValueError(f"{field.name}: invalid sign")
    else:  # ASCII zoned: last byte carries the sign as p..y for negatives (common convention)
        text = data.decode("ascii")
        negative = text[-1] in "pqrstuvwxy"
        if negative:
            text = text[:-1] + str("pqrstuvwxy".index(text[-1]))
        if not text.isdigit() or (negative and not field.signed):
            raise ValueError(f"{field.name}: not a zoned decimal")
        digits = [int(c) for c in text]
    value = Decimal(int("".join(str(d) for d in digits))).scaleb(-field.scale)
    return -value if negative else value


def _packed(data: bytes, field: Field) -> Decimal:
    nibbles = []
    for byte in data:
        nibbles += [byte >> 4, byte & 0x0F]
    sign = nibbles.pop()
    if any(n > 9 for n in nibbles) or sign not in (0xC, 0xD, 0xF):
        raise ValueError(f"{field.name}: not a packed decimal")
    if sign == 0xD and not field.signed:
        raise ValueError(f"{field.name}: negative value in an unsigned field")
    value = Decimal(int("".join(map(str, nibbles)))).scaleb(-field.scale)
    return -value if sign == 0xD else value


def decode_record(record: bytes, layout: Layout, *, encoding: str = "cp037") -> dict[str, Any]:
    if len(record) != layout.record_length:
        raise ValueError(f"record is {len(record)} bytes; layout {layout.name} needs {layout.record_length}")
    values: dict[str, Any] = {}
    for field in layout.fields:
        chunk = record[field.offset:field.offset + field.length]
        if field.kind == "text":
            values[field.name] = chunk.decode(encoding).rstrip(" ")
        elif field.kind == "zoned":
            values[field.name] = _zoned(chunk, field, encoding)
        else:
            values[field.name] = _packed(chunk, field)
    return values


def decode_file(data: bytes, layout: Layout, *, encoding: str = "cp037") -> list[dict[str, Any]]:
    size = layout.record_length
    if len(data) % size:
        raise ValueError(f"file length {len(data)} is not a multiple of the record length {size}")
    return [decode_record(data[i:i + size], layout, encoding=encoding) for i in range(0, len(data), size)]


def encode_packed(value: Decimal, digits: int, scale: int) -> bytes:
    """Inverse of packed decoding, for building fixtures."""
    scaled = int((abs(value).scaleb(scale)).to_integral_value())
    text = str(scaled).rjust(digits, "0")
    if len(text) > digits:
        raise ValueError("value does not fit")
    if len(text) % 2 == 0:
        text = "0" + text
    nibbles = [int(c) for c in text] + [0xD if value < 0 else 0xC]
    return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def cobol_round(value: Decimal, scale: int) -> Decimal:
    """COBOL ROUNDED: half away from zero (Decimal ROUND_HALF_UP does exactly that)."""
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


# ----------------------------------------------------------------- comparison


@dataclass
class Difference:
    key: str
    field: str
    reference: Any
    candidate: Any
    accepted: bool
    reason: str = ""


def compare_records(reference: list[dict[str, Any]], candidate: list[dict[str, Any]], *, key: str,
                    accepted: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
    """Every difference is reported. Accepted ones need an explicit (key, field) entry with a reason."""
    accepted = accepted or {}
    by_key_ref = {str(r[key]): r for r in reference}
    by_key_cand = {str(r[key]): r for r in candidate}
    if len(by_key_ref) != len(reference) or len(by_key_cand) != len(candidate):
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{key} is not unique; records cannot be paired")
    differences: list[Difference] = []
    missing = sorted(set(by_key_ref) - set(by_key_cand))
    extra = sorted(set(by_key_cand) - set(by_key_ref))
    for record_key in sorted(set(by_key_ref) & set(by_key_cand)):
        left, right = by_key_ref[record_key], by_key_cand[record_key]
        for name in sorted(set(left) | set(right)):
            a, b = left.get(name), right.get(name)
            # A field missing on one side is a difference, never a silent match.
            same = (name in left) == (name in right) and a == b
            if not same:
                reason = accepted.get((record_key, name), "")
                differences.append(Difference(record_key, name, a, b, bool(reason), reason))
    unaccepted = [d for d in differences if not d.accepted]
    return {
        "equivalent": not unaccepted and not missing and not extra,
        "missing_records": missing,
        "extra_records": extra,
        "differences": differences,
        "unaccepted": unaccepted,
        "unused_acceptances": sorted(f"{k}:{f}" for (k, f) in accepted
                                     if not any(d.key == k and d.field == f for d in differences)),
    }

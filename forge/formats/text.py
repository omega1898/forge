"""Decode and re-encode text so an edit changes only what it means to change.

A file is decoded once: its byte order mark, encoding and line-ending style
are recorded, and its lines are given to the editor with "\\n" endings. When
the edited text is written back, the same mark, encoding and line endings
are restored. A file that is decoded and encoded unchanged comes back
byte for byte (tested on random bytes in every supported encoding).

Encodings, in the order they are tried:

1. a byte order mark (UTF-8, UTF-16 or UTF-32);
2. UTF-16 without a mark, when every other byte of mostly-ASCII text is NUL
   (Windows resource scripts and registry exports are often saved that way);
3. strict UTF-8;
4. the format's legacy single-byte code page (Windows-1252 by default; ISO-8859-1
   for Java .properties; code page 437 for cmd.exe batch files), and
   ISO-8859-1 if the code page leaves a byte undefined. A single-byte code page
   decodes and re-encodes every byte exactly.

A character the original encoding cannot hold is refused with a reason
instead of being replaced.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

from forge.formats.registry import FileFormat

BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
NAMES = {"utf-8": "UTF-8", "utf-16-le": "UTF-16 LE", "utf-16-be": "UTF-16 BE", "utf-32-le": "UTF-32 LE",
         "utf-32-be": "UTF-32 BE", "cp1252": "Windows-1252", "latin-1": "ISO-8859-1", "cp437": "code page 437"}
ENDINGS = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}


class NotText(ValueError):
    """The bytes are not text in any encoding FORGE reads."""


class EncodingRefused(ValueError):
    """The edited text holds a character the file's encoding cannot store."""


@dataclass(frozen=True)
class TextFile:
    text: str               # line endings normalized to "\n"
    encoding: str           # Python codec name
    bom: bytes              # written before the text; b"" for none
    newline: str            # "\n", "\r\n" or "\r": the style written back
    mixed: bool = False     # the original mixed styles; an edit writes the most common one

    def describe(self) -> str:
        parts = [NAMES.get(self.encoding, self.encoding) + (" with byte order mark" if self.bom else "")]
        parts.append(f"{ENDINGS[self.newline]} line endings" + (" (mixed in the original)" if self.mixed else ""))
        return ", ".join(parts)


def _utf16_without_bom(data: bytes) -> str | None:
    if len(data) < 4 or len(data) % 2:
        return None
    for codec, zeros in (("utf-16-le", data[1::2]), ("utf-16-be", data[0::2])):
        if zeros.count(0) >= 0.9 * len(zeros):
            try:
                data.decode(codec)
            except UnicodeDecodeError:
                return None
            return codec
    return None


def _newline(raw: str) -> tuple[str, bool]:
    crlf = raw.count("\r\n")
    lone_cr = raw.count("\r") - crlf
    lone_lf = raw.count("\n") - crlf
    counts = {"\r\n": crlf, "\r": lone_cr, "\n": lone_lf}
    used = [style for style, count in counts.items() if count]
    if not used:
        return "\n", False
    return max(used, key=lambda style: counts[style]), len(used) > 1


def decode(data: bytes, fmt: FileFormat | None = None) -> TextFile:
    """Decode a file's bytes; raises NotText for binary data."""
    bom, encoding = b"", None
    for mark, codec in BOMS:
        if data.startswith(mark):
            bom, encoding = mark, codec
            break
    body = data[len(bom):]
    if encoding is None and b"\x00" in body:
        encoding = _utf16_without_bom(body)
        if encoding is None:
            raise NotText("the bytes contain NUL and are not UTF-16 text")
    if encoding is not None:
        try:
            raw = body.decode(encoding)
        except UnicodeDecodeError as error:
            raise NotText(f"not valid {NAMES.get(encoding, encoding)}: {error.reason}") from None
    else:
        try:
            raw, encoding = body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            legacy = fmt.legacy_encoding if fmt is not None else "cp1252"
            try:
                raw, encoding = body.decode(legacy), legacy
            except UnicodeDecodeError:
                raw, encoding = body.decode("latin-1"), "latin-1"
    newline, mixed = _newline(raw)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    return TextFile(text=text, encoding=encoding, bom=bom, newline=newline, mixed=mixed)


def encode(text: str, like: TextFile | None = None, fmt: FileFormat | None = None) -> bytes:
    """Encode edited text the way the original was stored (or, for a new file, the way its format needs)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if like is not None:
        encoding, bom, newline = like.encoding, like.bom, like.newline
    else:
        encoding, newline = "utf-8", (fmt.newline if fmt is not None and fmt.newline else "\n")
        non_ascii = any(ord(char) > 127 for char in text)
        bom = codecs.BOM_UTF8 if fmt is not None and fmt.bom_for_non_ascii and non_ascii else b""
    try:
        body = text.replace("\n", newline).encode(encoding)
    except UnicodeEncodeError as error:
        char = error.object[error.start]
        line = text.count("\n", 0, text.find(char)) + 1
        hint = (" Write it as a \\uXXXX escape." if fmt is not None and fmt.format_id == "java_properties"
                else " Use characters the file's encoding holds, or ask the person to convert the file.")
        raise EncodingRefused(f"line {line}: {char!r} (U+{ord(char):04X}) cannot be written in "
                              f"{NAMES.get(encoding, encoding)}, this file's encoding.{hint}") from None
    return bom + body

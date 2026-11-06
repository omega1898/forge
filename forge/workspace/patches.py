"""A strict unified-diff applier for model-proposed patches.

Strict means: every context and removed line must match the base text
exactly, hunks must apply in order without overlap, and there is no fuzzy
matching or offset searching. A patch that does not apply exactly is
rejected, so a model cannot get an edit landed somewhere it did not intend.

Only plain text edits, additions and deletions of regular files are
supported. Renames, mode changes, symlinks and binary patches are refused.
Paths are validated as clean relative paths.

Lines are split on "\n" only. Python's str.splitlines() also splits on form
feed, vertical tab and Unicode line separators, which would rewrite a C or
Fortran source holding a page break. A completely empty line inside a hunk
is an empty context line, as git apply reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from forge.contracts.errors import ErrorCode, ForgeError
from forge.contracts.paths import check_relative_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
MAX_PATCH_BYTES = 512 * 1024


@dataclass
class FilePatch:
    path: str
    hunks: list[tuple[int, int, list[str]]] = field(default_factory=list)  # (old_start, old_count, lines)
    created: bool = False
    deleted: bool = False


def _strip_prefix(raw: str) -> str | None:
    raw = raw.split("\t")[0].strip()
    if raw == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if raw.startswith(prefix):
            raw = raw[2:]
            break
    return check_relative_path(raw)


def split_lines(text: str) -> list[str]:
    """Lines split on "\n" alone; a final newline does not start an empty line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def parse_patch(text: str) -> list[FilePatch]:
    if len(text.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "patch is too large")
    lines = [line.removesuffix("\r") for line in split_lines(text)]
    files: list[FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(("rename ", "similarity index", "new mode", "old mode", "GIT binary patch", "Binary files")):
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"unsupported patch feature: {line[:60]!r}")
        if not line.startswith("--- "):
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "patch header '---' is not followed by '+++'")
        try:
            old_path, new_path = _strip_prefix(line[4:]), _strip_prefix(lines[index + 1][4:])
        except ValueError as error:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"invalid path in patch: {error}") from None
        if old_path and new_path and old_path != new_path:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "renames are not supported")
        current = FilePatch(path=new_path or old_path or "", created=old_path is None, deleted=new_path is None)
        if not current.path:
            raise ForgeError(ErrorCode.INVALID_REQUEST, "patch names no file")
        index += 2
        while index < len(lines) and not lines[index].startswith("--- "):
            match = _HUNK.match(lines[index])
            if not match:
                index += 1
                continue
            old_start, old_count = int(match.group(1)), int(match.group(2) or "1")
            new_count = int(match.group(4) or "1")
            index += 1
            body: list[str] = []
            seen_old = seen_new = 0
            while index < len(lines) and (seen_old < old_count or seen_new < new_count):
                body_line = lines[index]
                if body_line.startswith("\\"):  # "\ No newline at end of file"
                    index += 1
                    continue
                if not body_line:
                    body_line = " "  # an empty context line whose leading space was dropped
                if body_line[0] not in " +-":
                    raise ForgeError(ErrorCode.INVALID_REQUEST, f"malformed hunk line in {current.path}")
                if body_line[0] in " -":
                    seen_old += 1
                if body_line[0] in " +":
                    seen_new += 1
                body.append(body_line)
                index += 1
            if seen_old != old_count or seen_new != new_count:
                raise ForgeError(ErrorCode.INVALID_REQUEST, f"hunk line counts do not match in {current.path}")
            current.hunks.append((old_start, old_count, body))
        files.append(current)
    if not files:
        raise ForgeError(ErrorCode.INVALID_REQUEST, "no file changes found in the patch")
    return files


def apply_file_patch(base_text: str | None, patch: FilePatch) -> str | None:
    """Apply one file's hunks exactly. Returns the new text, or None for a deletion."""
    if patch.created and base_text is not None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"patch creates {patch.path}, which already exists")
    if not patch.created and base_text is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"patch edits {patch.path}, which does not exist")
    old_lines = [] if base_text is None else split_lines(base_text)
    ends_with_newline = base_text is None or base_text.endswith("\n")
    output: list[str] = []
    cursor = 0
    for old_start, old_count, body in patch.hunks:
        start = max(old_start - 1, 0) if old_count else old_start
        if start < cursor:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"overlapping hunks in {patch.path}")
        output.extend(old_lines[cursor:start])
        position = start
        for line in body:
            kind, text = line[0], line[1:]
            if kind in " -":
                if position >= len(old_lines) or old_lines[position] != text:
                    raise ForgeError(
                        ErrorCode.INVALID_REQUEST,
                        f"patch does not apply exactly to {patch.path} at line {position + 1}",
                        details={"path": patch.path, "line": position + 1},
                    )
                position += 1
                if kind == " ":
                    output.append(text)
            else:
                output.append(text)
        cursor = position
    output.extend(old_lines[cursor:])
    if patch.deleted:
        if output:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"deletion patch for {patch.path} leaves content behind")
        return None
    return "\n".join(output) + ("\n" if output and ends_with_newline else "")

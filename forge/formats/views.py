"""What a model is shown for any file: text through the projection, everything else through a bounded reader.

Readers here parse formats, never run them, and stop at fixed limits:

- Office Open XML (Word, Excel, PowerPoint) and OpenDocument: ZIP members
  are read up to MAX_MEMBER bytes each and MAX_UNPACKED in all, so a ZIP bomb
  is refused; the XML parser refuses any document type declaration, which
  is how entity-expansion attacks start (OOXML and ODF never use one).
- Model files: pickles (and the pickles inside PyTorch checkpoints, NumPy
  object arrays and other ZIPs) are disassembled with pickletools, which
  reads opcodes without executing them, to list every callable the file
  would import on load. safetensors and GGUF headers are parsed with every
  offset checked against the file's size. Keras archives are checked for
  Lambda layers, which hold Python code.
- SQLite: a temporary read-only copy is opened in defensive mode with
  trusted_schema off, and only the schema is read.
- Executables: ELF and PE headers, including the PE hardening flags (ASLR,
  DEP, Control Flow Guard).
- Certificates: subject, issuer and validity through the cryptography
  package; private keys are never shown.

Formats that need a converter (PDF, RTF, legacy Word and Excel text) say so
instead of showing garbage.
"""

from __future__ import annotations

import ast
import io
import itertools
import json
import pickletools
import sqlite3
import struct
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from xml.parsers import expat

from forge.formats.project import Projection, project
from forge.formats.registry import FileFormat, detect
from forge.formats.text import NotText, TextFile, decode

MAX_MEMBER = 32 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_ROWS = 200
MAX_COLUMNS = 40
MAX_PICKLE = 64 * 1024 * 1024


@dataclass
class View:
    text: str
    fmt: FileFormat
    editable: bool = False            # a text view whose edits are restored through the projection
    notes: list[str] = field(default_factory=list)
    decoded: TextFile | None = None
    projection: Projection | None = None

    def render(self, path: str) -> str:
        """The observation for a model: the content, then FORGE's notes clearly outside it."""
        if not self.notes:
            return self.text
        return self.text + "\n-- FORGE notes (not part of the file) --\n" + "\n".join(f"- {n}" for n in self.notes)


class ReaderError(ValueError):
    pass


# ------------------------------------------------------------------ helpers --


def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as error:
        raise ReaderError(f"not a readable ZIP archive ({error})") from None
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ReaderError(f"the archive has {len(infos)} members; FORGE reads at most {MAX_MEMBERS}")
    if sum(info.file_size for info in infos) > MAX_UNPACKED:
        raise ReaderError("the archive unpacks to more than 128 MiB; not read (possible ZIP bomb)")
    return archive


def _member(archive: zipfile.ZipFile, name: str, limit: int = MAX_MEMBER) -> bytes:
    """One member's bytes, reading at most limit + 1 so a lying size field cannot exhaust memory."""
    try:
        with archive.open(name) as handle:
            data = handle.read(limit + 1)
    except (KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError, OSError) as error:
        raise ReaderError(f"cannot read {name} ({error})") from None
    if len(data) > limit:
        raise ReaderError(f"{name} is larger than {limit} bytes")
    return data


class _Xml:
    """Callbacks over XML with local element names; any DOCTYPE or entity declaration is refused."""

    def __init__(self) -> None:
        self.parser = expat.ParserCreate(namespace_separator=" ")

        def refuse(*_args: object) -> None:
            raise ReaderError("the XML declares a DOCTYPE or entities; refused")

        self.parser.StartDoctypeDeclHandler = refuse
        self.parser.EntityDeclHandler = refuse

    def parse(self, data: bytes, start, end=None, chars=None) -> None:
        self.parser.StartElementHandler = lambda name, attrs: start(name.rsplit(" ", 1)[-1], {
            key.rsplit(" ", 1)[-1]: value for key, value in attrs.items()})
        if end is not None:
            self.parser.EndElementHandler = lambda name: end(name.rsplit(" ", 1)[-1])
        if chars is not None:
            self.parser.CharacterDataHandler = chars
        try:
            self.parser.Parse(data, True)
        except expat.ExpatError as error:
            raise ReaderError(f"malformed XML: {expat.errors.messages[error.code]} at line {error.lineno}") from None


def _cut(lines: list[str], limit: int, what: str) -> list[str]:
    return lines if len(lines) <= limit else [*lines[:limit], f"... {len(lines) - limit} more {what} not shown"]


# ------------------------------------------------------- Office Open XML --


def _docx_text(xml: bytes) -> list[str]:
    paragraphs: list[str] = []
    state = {"text": [], "style": "", "numbered": False, "cells": None, "row": None, "deleted": False}

    def start(name: str, attrs: dict[str, str]) -> None:
        if name == "p":
            state["text"], state["style"], state["numbered"] = [], "", False
        elif name == "pStyle":
            state["style"] = attrs.get("val", "")
        elif name == "numPr":
            state["numbered"] = True
        elif name in ("tab",):
            state["text"].append("\t")
        elif name in ("br", "cr"):
            state["text"].append(" ")
        elif name == "tr":
            state["row"] = []
        elif name == "tc":
            state["cells"] = []
        elif name == "del":
            state["deleted"] = True
            state["text"].append("[deleted: ")

    def end(name: str) -> None:
        if name == "del":
            state["deleted"] = False
            state["text"].append("]")
        elif name == "p":
            text = "".join(state["text"]).strip()
            if state["cells"] is not None:
                state["cells"].append(text)
                return
            style = state["style"].lower()
            if style.startswith("heading") and style[7:].isdigit():
                text = "#" * min(int(style[7:]), 6) + " " + text
            elif style == "title":
                text = "# " + text
            elif state["numbered"] and text:
                text = "- " + text
            if text:
                paragraphs.append(text)
        elif name == "tc" and state["row"] is not None:
            state["row"].append(" ".join(t for t in (state["cells"] or []) if t))
            state["cells"] = None
        elif name == "tr" and state["row"] is not None:
            paragraphs.append("| " + " | ".join(state["row"]) + " |")
            state["row"] = None

    def chars(data: str) -> None:
        state["text"].append(data)

    collecting = {"on": False}

    def start_text(name: str, attrs: dict[str, str]) -> None:
        collecting["on"] = name in ("t", "delText")
        start(name, attrs)

    def chars_text(data: str) -> None:
        if collecting["on"]:
            chars(data)

    def end_text(name: str) -> None:
        if name in ("t", "delText"):
            collecting["on"] = False
        end(name)

    _Xml().parse(xml, start_text, end_text, chars_text)
    return paragraphs


def _office_flags(archive: zipfile.ZipFile) -> list[str]:
    names = {info.filename for info in archive.infolist()}
    notes = []
    if any(name.endswith("vbaProject.bin") for name in names):
        notes.append("contains VBA macros (vbaProject.bin): macros are code; FORGE does not run or show them")
    if any("/embeddings/" in name for name in names):
        notes.append("contains embedded objects: " + ", ".join(sorted(n.rsplit("/", 1)[-1] for n in names
                                                                       if "/embeddings/" in n))[:300])
    if any("externalLink" in name for name in names):
        notes.append("links to external workbooks")
    return notes


def _docx(data: bytes, fmt: FileFormat) -> View:
    archive = _zip(data)
    lines = _docx_text(_member(archive, "word/document.xml"))
    if "word/comments.xml" in archive.namelist():
        comments = _docx_text(_member(archive, "word/comments.xml"))
        if comments:
            lines += ["", "## Comments", *(f"- {c}" for c in comments)]
    return View("\n".join(_cut(lines, 2000, "paragraphs")), fmt, notes=_office_flags(archive))


def _column(ref: str) -> str:
    return "".join(char for char in ref if char.isalpha())


def _sheet_rows(xml: bytes, shared: list[str]) -> list[str]:
    """One worksheet as "A=value | B==SUM(...) -> 6" rows, with shared strings resolved."""
    rows: list[str] = []
    cell: dict[str, str] = {}
    row: list[str] = []
    capture = {"what": ""}

    def start(name: str, attrs: dict[str, str]) -> None:
        if name == "row":
            row.clear()
        elif name == "c":
            cell.clear()
            cell.update({"ref": attrs.get("r", ""), "type": attrs.get("t", ""), "v": "", "f": "", "is": ""})
        elif name in ("v", "f", "t"):
            capture["what"] = {"v": "v", "f": "f", "t": "is"}[name]

    def end(name: str) -> None:
        if name in ("v", "f", "t"):
            capture["what"] = ""
        elif name == "c" and len(row) < MAX_COLUMNS:
            value = cell["v"]
            if cell["type"] == "s" and value.isdigit() and int(value) < len(shared):
                value = shared[int(value)]
            elif cell["type"] == "inlineStr":
                value = cell["is"]
            elif cell["type"] == "b":
                value = "TRUE" if value == "1" else "FALSE"
            if cell["f"]:
                value = f"={cell['f']} -> {value}" if value else f"={cell['f']}"
            if value:
                row.append(f"{_column(cell['ref'])}={value}")
        elif name == "row" and row and len(rows) <= MAX_ROWS:
            rows.append(" | ".join(row))

    def chars(text: str) -> None:
        if capture["what"] and capture["what"] in cell:
            cell[capture["what"]] += text

    _Xml().parse(xml, start, end, chars)
    return rows


def _xlsx(data: bytes, fmt: FileFormat) -> View:
    archive = _zip(data)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        current: list[str] = []
        inside = {"t": False}

        def s_start(name: str, attrs: dict[str, str]) -> None:
            if name == "si":
                current.clear()
            inside["t"] = name == "t"

        def s_end(name: str) -> None:
            if name == "si":
                shared.append("".join(current))
            if name == "t":
                inside["t"] = False

        _Xml().parse(_member(archive, "xl/sharedStrings.xml"), s_start, s_end,
                     lambda text: current.append(text) if inside["t"] else None)
    sheets: list[tuple[str, str, str]] = []
    _Xml().parse(_member(archive, "xl/workbook.xml"), lambda name, attrs: sheets.append(
        (attrs.get("name", "?"), attrs.get("id", ""), attrs.get("state", "visible"))) if name == "sheet" else None)
    targets: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        _Xml().parse(_member(archive, "xl/_rels/workbook.xml.rels"), lambda name, attrs: targets.__setitem__(
            attrs.get("Id", ""), attrs.get("Target", "")) if name == "Relationship" else None)
    out: list[str] = []
    notes = _office_flags(archive)
    for index, (sheet_name, rel_id, state) in enumerate(sheets, 1):
        target = targets.get(rel_id, f"worksheets/sheet{index}.xml").lstrip("/")
        member = target if target.startswith("xl/") else f"xl/{target}"
        if state != "visible":
            notes.append(f"sheet {sheet_name!r} is {state}")
        out.append(f"## Sheet: {sheet_name}" + (f" ({state})" if state != "visible" else ""))
        try:
            rows = _sheet_rows(_member(archive, member), shared)
        except ReaderError as error:
            out.append(f"(sheet not read: {error})")
            continue
        out.extend(_cut(rows, MAX_ROWS, "rows"))
        out.append("")
    return View("\n".join(out).rstrip(), fmt, notes=notes)


def _pptx(data: bytes, fmt: FileFormat) -> View:
    archive = _zip(data)

    def number(name: str) -> int:
        digits = "".join(char for char in PurePosixPath(name).stem if char.isdigit())
        return int(digits) if digits else 0

    def texts(member: str) -> list[str]:
        paragraphs: list[str] = []
        current: list[str] = []
        inside = {"t": False}

        def start(name: str, attrs: dict[str, str]) -> None:
            inside["t"] = name == "t"
            if name == "p":
                current.clear()

        def end(name: str) -> None:
            if name == "t":
                inside["t"] = False
            if name == "p" and "".join(current).strip():
                paragraphs.append("".join(current).strip())

        _Xml().parse(_member(archive, member), start, end, lambda t: current.append(t) if inside["t"] else None)
        return paragraphs

    names = archive.namelist()
    slides = sorted((n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")), key=number)
    out: list[str] = []
    for slide in slides:
        out.append(f"## Slide {number(slide)}")
        out.extend(texts(slide))
        notes_member = f"ppt/notesSlides/notesSlide{number(slide)}.xml"
        if notes_member in names:
            spoken = [t for t in texts(notes_member) if not t.isdigit()]
            if spoken:
                out.append("Notes: " + " ".join(spoken))
        out.append("")
    return View("\n".join(out).rstrip(), fmt, notes=_office_flags(archive))


def _odf(data: bytes, fmt: FileFormat) -> View:
    archive = _zip(data)
    out: list[str] = []
    current: list[str] = []
    row: list[str] = []
    state = {"depth": 0, "level": 0, "in_cell": False, "rows": 0}

    def start(name: str, attrs: dict[str, str]) -> None:
        if name in ("p", "h"):
            if not state["in_cell"]:
                current.clear()
            state["level"] = int(attrs.get("outline-level", "1") or 1) if name == "h" else 0
        elif name == "table":
            out.append(f"## Table: {attrs.get('name', '')}")
        elif name == "table-row":
            row.clear()
        elif name == "table-cell":
            state["in_cell"] = True
            current.clear()
            state["repeat"] = min(int(attrs.get("number-columns-repeated", "1") or 1), MAX_COLUMNS)
        elif name in ("s", "tab", "line-break"):
            current.append(" ")
        elif name == "page":
            out.append(f"## Slide: {attrs.get('name', '')}")

    def end(name: str) -> None:
        if name == "table-cell":
            state["in_cell"] = False
            text = "".join(current).strip()
            row.extend([text] * (state.get("repeat", 1) if text else 1))
        elif name == "table-row":
            cells = [c for c in row[:MAX_COLUMNS]]
            while cells and not cells[-1]:
                cells.pop()
            if cells and state["rows"] < MAX_ROWS:
                out.append("| " + " | ".join(cells) + " |")
                state["rows"] += 1
        elif name in ("p", "h") and not state["in_cell"]:
            text = "".join(current).strip()
            if text:
                out.append(("#" * min(state["level"], 6) + " " if state["level"] else "") + text)

    _Xml().parse(_member(archive, "content.xml"), start, end, current.append)
    return View("\n".join(_cut(out, 3000, "lines")), fmt)


# ------------------------------------------------------------ archives --


_EXECUTABLE_MEMBERS = (".exe", ".dll", ".ps1", ".bat", ".cmd", ".vbs", ".js", ".jse", ".sh", ".scr", ".hta", ".lnk",
                       ".msi", ".jar", ".so", ".dylib")


def _zip_listing(data: bytes, fmt: FileFormat) -> View:
    archive = _zip(data)
    infos = archive.infolist()
    lines = [f"{info.filename}  ({info.file_size} bytes)" for info in infos]
    notes = []
    unsafe = [i.filename for i in infos if i.filename.startswith("/") or ".." in PurePosixPath(i.filename).parts
              or "\\" in i.filename]
    if unsafe:
        notes.append(f"member paths escape the extraction folder (zip slip): {unsafe[:5]}")
    packed = sum(i.compress_size for i in infos) or 1
    if sum(i.file_size for i in infos) / packed > 100:
        notes.append("compression ratio over 100:1")
    executables = [i.filename for i in infos if i.filename.lower().endswith(_EXECUTABLE_MEMBERS)]
    if executables:
        notes.append(f"contains executables or scripts: {executables[:10]}")
    pickles = [i.filename for i in infos if i.filename.endswith((".pkl", ".pickle")) or i.filename.endswith("data.pkl")]
    for name in pickles[:5]:
        try:
            notes.extend(f"{name}: {line}" for line in pickle_report(_member(archive, name, MAX_PICKLE)))
        except ReaderError as error:
            notes.append(f"{name}: {error}")
    return View("\n".join(_cut(lines, 300, "members")), fmt, notes=notes)


# ------------------------------------------------------------ pickles --

_STRING_OPS = {"SHORT_BINUNICODE", "BINUNICODE", "BINUNICODE8", "UNICODE", "STRING", "BINSTRING", "SHORT_BINSTRING"}
_EXPECTED_GLOBALS = {
    ("collections", "OrderedDict"), ("torch._utils", "_rebuild_tensor_v2"), ("torch._utils", "_rebuild_tensor"),
    ("torch._utils", "_rebuild_parameter"), ("torch._utils", "_rebuild_parameter_with_state"),
    ("torch", "Size"), ("torch", "device"), ("torch", "dtype"), ("torch._tensor", "_rebuild_from_type_v2"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"), ("numpy", "ndarray"),
    ("numpy", "dtype"), ("_codecs", "encode"), ("builtins", "set"), ("builtins", "frozenset"),
    ("builtins", "bytearray"), ("builtins", "slice"), ("builtins", "complex"), ("builtins", "range"),
    ("datetime", "datetime"), ("datetime", "date"), ("datetime", "timedelta"), ("decimal", "Decimal"),
}
_DANGEROUS_MODULES = {"os", "posix", "nt", "subprocess", "sys", "socket", "shutil", "pty", "runpy", "importlib",
                      "pickle", "_pickle", "marshal", "webbrowser", "requests", "urllib", "http", "ctypes", "code",
                      "codeop", "commands", "popen2", "platform", "multiprocessing", "asyncio", "pdb", "timeit",
                      "tempfile", "pathlib", "io", "glob", "zipimport", "__builtin__"}
_DANGEROUS_BUILTINS = {"eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "open", "apply",
                       "input", "breakpoint", "globals", "locals", "vars", "execfile"}


def _classify(module: str | None, name: str | None) -> str:
    if module is None or name is None:
        return "unknown"
    if (module, name) in _EXPECTED_GLOBALS or (module == "torch" and name.endswith("Storage")) or \
            (module.startswith("numpy") and name.endswith("DType")):
        return "expected"
    if module.split(".")[0] in _DANGEROUS_MODULES or (module in ("builtins", "__builtin__")
                                                      and name in _DANGEROUS_BUILTINS):
        return "dangerous"
    return "unknown"


def pickle_globals(data: bytes) -> tuple[list[tuple[str | None, str | None]], int, str | None]:
    """(imported callables, pickles read, parse error) without unpickling anything."""
    found: list[tuple[str | None, str | None]] = []
    position, count, error = 0, 0, None
    data = data[:MAX_PICKLE]
    while position < len(data) and count < 16:
        values: list[str | None] = []
        memo: dict[int, str | None] = {}
        stream = io.BytesIO(data[position:])
        try:
            for opcode, arg, _offset in pickletools.genops(stream):
                if opcode.name in ("GLOBAL", "INST"):
                    module, _, name = str(arg).partition(" ")
                    found.append((module, name))
                elif opcode.name == "STACK_GLOBAL":
                    found.append((values[-2] if len(values) > 1 else None, values[-1] if values else None))
                elif opcode.name in _STRING_OPS:
                    values.append(arg if isinstance(arg, str) else (arg.decode("latin-1") if isinstance(arg, bytes)
                                                                    else None))
                    continue
                elif opcode.name == "MEMOIZE":
                    memo[len(memo)] = values[-1] if values else None
                    continue
                elif opcode.name in ("PUT", "BINPUT", "LONG_BINPUT"):
                    memo[int(arg)] = values[-1] if values else None
                    continue
                elif opcode.name in ("GET", "BINGET", "LONG_BINGET"):
                    values.append(memo.get(int(arg)))
                    continue
                if opcode.stack_after:
                    values.append(None)
        except (ValueError, struct.error, OverflowError, KeyError, IndexError) as problem:
            error = str(problem)[:200]
            break
        count += 1
        position += stream.tell()
        if position >= len(data) or data[position:position + 1] != b"\x80":
            break
    return found, count, error


def pickle_report(data: bytes) -> list[str]:
    found, count, error = pickle_globals(data)
    lines = []
    seen: set[tuple[str | None, str | None]] = set()
    dangerous = []
    for module, name in found:
        if (module, name) in seen:
            continue
        seen.add((module, name))
        verdict = _classify(module, name)
        label = f"{module or '?'}.{name or '?'}"
        if verdict == "dangerous":
            dangerous.append(label)
        lines.append(f"imports {label} ({verdict})")
    head = (f"DANGEROUS: loading this pickle would call {', '.join(dangerous)}, which can run commands on the "
            "machine that loads it" if dangerous else
            "no known-dangerous imports; a pickle can still run any callable it imports, so treat unknown imports "
            "as unsafe")
    if error:
        head += f"; the pickle could not be fully parsed ({error}), so treat it as unsafe"
    return [f"{count} pickle(s); {head}", *_cut(lines, 40, "imports")]


def _pickle(data: bytes, fmt: FileFormat) -> View:
    report = pickle_report(data)
    return View("\n".join(report), fmt, notes=["the file was disassembled, never unpickled"])


def _pytorch(data: bytes, fmt: FileFormat) -> View:
    if data.startswith(b"PK"):
        listing = _zip_listing(data, fmt)
        listing.notes.append("PyTorch 2.6 and later default to torch.load(weights_only=True), which refuses "
                             "imports outside an allowlist; weights_only=False runs the pickle")
        return listing
    return _pickle(data, fmt)


def _keras(data: bytes, fmt: FileFormat) -> View:
    archive = _zip(data)
    notes = []
    layers: list[str] = []
    if "config.json" in archive.namelist():
        try:
            config = json.loads(_member(archive, "config.json"))
        except (json.JSONDecodeError, RecursionError) as error:
            raise ReaderError(f"config.json is not JSON ({error})") from None

        def walk(node: object) -> None:
            if isinstance(node, dict):
                if "class_name" in node and isinstance(node["class_name"], str):
                    layers.append(node["class_name"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(config)
    if "Lambda" in layers:
        notes.append("DANGEROUS: contains Lambda layers, which hold Python code; Keras refuses them in safe_mode, and "
                     "loading with safe_mode=False runs that code")
    text = "Layer classes: " + ", ".join(sorted(set(layers))) if layers else "no config.json layers found"
    listing = _zip_listing(data, fmt)
    return View(text + "\n\n" + listing.text, fmt, notes=notes + listing.notes)


# ------------------------------------------------------- model headers --

_SAFETENSORS_SIZES = {"BOOL": 1, "U8": 1, "I8": 1, "F8_E5M2": 1, "F8_E4M3": 1, "I16": 2, "U16": 2, "F16": 2,
                      "BF16": 2, "I32": 4, "U32": 4, "F32": 4, "F64": 8, "I64": 8, "U64": 8}


def _safetensors(data: bytes, fmt: FileFormat, size: int) -> View:
    if len(data) < 8:
        raise ReaderError("too short for a safetensors header")
    (length,) = struct.unpack_from("<Q", data, 0)
    if length > 100 * 1024 * 1024 or 8 + length > len(data):
        raise ReaderError(f"header length {length} is beyond what was read")
    try:
        header = json.loads(data[8:8 + length])
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ReaderError(f"header is not JSON ({error})") from None
    if not isinstance(header, dict):
        raise ReaderError("header is not a JSON object")
    metadata = header.pop("__metadata__", None)
    payload = size - 8 - length
    problems, lines, total, spans = [], [], 0, []
    for name, info in header.items():
        if not isinstance(info, dict):
            problems.append(f"{name}: entry is not an object")
            continue
        dtype, shape, offsets = info.get("dtype"), info.get("shape"), info.get("data_offsets")
        if dtype not in _SAFETENSORS_SIZES or not isinstance(shape, list) or not isinstance(offsets, list) \
                or len(offsets) != 2 or not all(isinstance(v, int) and v >= 0 for v in [*shape, *offsets]):
            problems.append(f"{name}: malformed entry")
            continue
        count = 1
        for dim in shape:
            count *= dim
        start, end = offsets
        if not 0 <= start <= end <= payload:
            problems.append(f"{name}: data offsets {offsets} fall outside the file")
        elif end - start != count * _SAFETENSORS_SIZES[dtype]:
            problems.append(f"{name}: {end - start} bytes stored for {count} values of {dtype}")
        spans.append((start, end, name))
        total += count
        lines.append(f"{name}: {dtype} {shape}")
    spans.sort()
    for (_s1, e1, n1), (s2, _e2, n2) in itertools.pairwise(spans):
        if s2 < e1:
            problems.append(f"{n1} and {n2} overlap")
    text = [f"{len(lines)} tensors, {total:,} values", *_cut(lines, 60, "tensors")]
    if isinstance(metadata, dict):
        text += ["", "metadata:", *(f"  {k}: {str(v)[:200]}" for k, v in list(metadata.items())[:20])]
    notes = ["safetensors holds data only; loading it does not run code"]
    if problems:
        notes.append("integrity problems: " + "; ".join(problems[:10]))
    return View("\n".join(text), fmt, notes=notes)


class _Cursor:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.at + count > len(self.data):
            raise ReaderError("the header runs past the bytes read")
        chunk = self.data[self.at:self.at + count]
        self.at += count
        return chunk

    def unpack(self, spec: str) -> tuple:
        return struct.unpack("<" + spec, self.take(struct.calcsize("<" + spec)))


_GGUF_SCALARS = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


def _gguf_value(cursor: _Cursor, kind: int, depth: int = 0) -> object:
    if kind in _GGUF_SCALARS:
        return cursor.unpack(_GGUF_SCALARS[kind])[0]
    if kind == 8:
        (length,) = cursor.unpack("Q")
        return cursor.take(length).decode("utf-8", "replace")
    if kind == 9 and depth < 2:
        element, count = cursor.unpack("IQ")
        items = [_gguf_value(cursor, element, depth + 1) for _ in range(count)] if count <= 5 else None
        if items is None:
            for _ in range(count):
                _gguf_value(cursor, element, depth + 1)
            return f"[array of {count}]"
        return items
    raise ReaderError(f"unknown GGUF value type {kind}")


def _gguf(data: bytes, fmt: FileFormat) -> View:
    cursor = _Cursor(data)
    if cursor.take(4) != b"GGUF":
        raise ReaderError("no GGUF magic")
    (version,) = cursor.unpack("I")
    if version < 2:
        return View(f"GGUF version {version}; FORGE reads versions 2 and 3", fmt)
    tensors, count = cursor.unpack("QQ")
    lines = [f"GGUF version {version}, {tensors} tensors, {count} metadata keys"]
    template = None
    try:
        for _ in range(min(count, 10_000)):
            (length,) = cursor.unpack("Q")
            key = cursor.take(length).decode("utf-8", "replace")
            (kind,) = cursor.unpack("I")
            value = _gguf_value(cursor, kind)
            if key == "tokenizer.chat_template":
                template = str(value)
                value = f"[{len(template)} characters, shown below]"
            lines.append(f"{key} = {str(value)[:200]}")
    except ReaderError as error:
        lines.append(f"(metadata cut short: {error})")
    notes = ["GGUF holds data only; its chat template is Jinja text that the runtime renders"]
    if template is not None:
        lines += ["", "chat template:", template[:3000]]
    return View("\n".join(_cut(lines, 120, "keys")), fmt, notes=notes)


def _npy_header(data: bytes) -> dict:
    if not data.startswith(b"\x93NUMPY") or len(data) < 10:
        raise ReaderError("no NumPy magic")
    major = data[6]
    if major == 1:
        (length,) = struct.unpack_from("<H", data, 8)
        start = 10
    else:
        (length,) = struct.unpack_from("<I", data, 8)
        start = 12
    try:
        header = ast.literal_eval(data[start:start + length].decode("latin-1"))  # literals only: nothing runs
    except (ValueError, SyntaxError) as error:
        raise ReaderError(f"header is not a Python literal ({error})") from None
    if not isinstance(header, dict):
        raise ReaderError("header is not a dictionary")
    return header


def _numpy(data: bytes, fmt: FileFormat) -> View:
    arrays = {}
    if data.startswith(b"PK"):
        archive = _zip(data)
        for name in archive.namelist()[:200]:
            try:
                with archive.open(name) as handle:
                    arrays[name] = _npy_header(handle.read(65536))  # the header only, never the array
            except (RuntimeError, zipfile.BadZipFile, OSError) as error:
                raise ReaderError(f"cannot read {name} ({error})") from None
    else:
        arrays["array"] = _npy_header(data)
    lines, notes = [], []
    for name, header in arrays.items():
        lines.append(f"{name}: dtype {header.get('descr')} shape {header.get('shape')}")
        if "O" in str(header.get("descr")):
            notes.append(f"DANGEROUS: {name} holds Python objects, stored as a pickle; numpy.load needs "
                         "allow_pickle=True for it, which runs the pickle")
    return View("\n".join(lines), fmt, notes=notes)


# ------------------------------------------------------------ databases --


def _sqlite(data: bytes, fmt: FileFormat) -> View:
    if not data.startswith(b"SQLite format 3\x00"):
        raise ReaderError("not an SQLite database")
    with tempfile.TemporaryDirectory(prefix="forge-sqlite-") as folder:
        path = Path(folder) / "copy.db"
        path.write_bytes(data)
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)  # file:///C:/... on Windows
        try:
            if hasattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE"):
                connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            connection.execute("PRAGMA trusted_schema = OFF")
            rows = connection.execute(
                "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name "
                "LIMIT 500").fetchall()
        except sqlite3.DatabaseError as error:
            raise ReaderError(f"SQLite could not read the schema ({error})") from None
        finally:
            connection.close()
    lines = [f"-- {kind} {name}\n{sql or '(no SQL)'};" for kind, name, sql in rows]
    return View("\n".join(lines) or "(empty schema)", fmt, notes=["schema only; no rows were read"])


def _access(data: bytes, fmt: FileFormat) -> View:
    engine = data[4:19].decode("latin-1", "replace") if len(data) > 19 else "?"
    version = data[0x14] if len(data) > 0x14 else None
    # The engine version byte, as mdbtools names its codes.
    names = {0: "Jet 3 (Access 97)", 1: "Jet 4 (Access 2000-2003)", 2: "ACE 12 (Access 2007)",
             3: "ACE 14 (Access 2010)", 4: "ACE 15 (Access 2013)", 5: "ACE 16 (Access 2016)",
             6: "ACE 17 (Access 2019)"}
    return View(f"{engine.strip()}, {names.get(version, f'version byte {version}')}", fmt, notes=[
        "reading tables and queries needs mdbtools, which FORGE does not run for the assistant yet",
        ("to edit VBA with FORGE, export modules as text (Application.SaveAsText, or the VBA editor's Export File) "
         "and work on the .bas, .cls and .frm files")])


# -------------------------------------------------------- OLE compound --


def ole_streams(data: bytes) -> list[str]:
    """Stream and storage names in an OLE2 compound file (legacy Office, MSI, Outlook .msg)."""
    if len(data) < 512 or not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ReaderError("not an OLE2 compound file")
    shift = struct.unpack_from("<H", data, 0x1E)[0]
    if shift not in (9, 12):
        raise ReaderError("unexpected sector size")
    sector = 1 << shift
    fat_count, first_dir = struct.unpack_from("<I", data, 0x2C)[0], struct.unpack_from("<i", data, 0x30)[0]
    difat = [v for v in struct.unpack_from("<109i", data, 0x4C) if v >= 0][:fat_count]

    def read_sector(index: int) -> bytes:
        start = (index + 1) * sector
        if index < 0 or start + sector > len(data):
            raise ReaderError("sector outside the file")
        return data[start:start + sector]

    fat: list[int] = []
    for index in difat:
        fat.extend(struct.unpack(f"<{sector // 4}i", read_sector(index)))
    names: list[str] = []
    current, seen = first_dir, set()
    while current >= 0 and current not in seen and len(seen) < 10_000:
        seen.add(current)
        block = read_sector(current)
        for offset in range(0, sector, 128):
            length = struct.unpack_from("<H", block, offset + 64)[0]
            kind = block[offset + 66]
            if kind in (1, 2) and 2 <= length <= 64:
                names.append(block[offset:offset + length - 2].decode("utf-16-le", "replace"))
        current = fat[current] if current < len(fat) else -1
    return names


def _ole(data: bytes, fmt: FileFormat) -> View:
    streams = ole_streams(data)
    notes = []
    if "EncryptedPackage" in streams or "EncryptionInfo" in streams:
        notes.append("password-protected Office document: the content is encrypted and cannot be read")
    if any(name in ("VBA", "_VBA_PROJECT_CUR", "Macros", "_VBA_PROJECT") for name in streams):
        notes.append("contains VBA macros: macros are code; FORGE does not run or show them")
    notes.append("the text of legacy Word, Excel and PowerPoint files needs a converter (LibreOffice), which FORGE "
                 "does not run for the assistant yet")
    return View("streams: " + ", ".join(_cut(streams, 60, "streams")), fmt, notes=notes)


# -------------------------------------------------------- executables --

_ELF_MACHINES = {3: "x86", 4: "Motorola 68000", 8: "MIPS", 20: "PowerPC", 21: "PowerPC 64", 40: "ARM", 50: "IA-64",
                 62: "x86-64", 83: "AVR", 92: "OpenRISC", 105: "TI MSP430", 164: "Hexagon", 183: "AArch64",
                 243: "RISC-V", 258: "LoongArch", 2: "SPARC", 43: "SPARC V9", 22: "S/390", 42: "SuperH",
                 87: "NEC V850", 88: "Renesas M32R", 106: "Analog Devices Blackfin", 113: "Altera Nios II"}
_ELF_TYPES = {1: "relocatable object", 2: "executable", 3: "shared object or position-independent executable",
              4: "core dump"}


def _elf(data: bytes, fmt: FileFormat) -> View:
    if len(data) < 52 or not data.startswith(b"\x7fELF"):
        raise ReaderError("not an ELF file")
    bits = {1: 32, 2: 64}.get(data[4], 0)
    order = {1: "<", 2: ">"}.get(data[5])
    if not bits or order is None:
        raise ReaderError("unknown ELF class or byte order")
    kind, machine = struct.unpack_from(order + "HH", data, 16)
    entry = struct.unpack_from(order + ("I" if bits == 32 else "Q"), data, 24)[0]
    text = (f"ELF {bits}-bit {'little' if order == '<' else 'big'}-endian, {_ELF_TYPES.get(kind, kind)}, "
            f"{_ELF_MACHINES.get(machine, f'machine {machine}')}, entry point 0x{entry:x}")
    return View(text, fmt, notes=["for sections, symbols and disassembly use the reverse-engineering pack (radare2)"])


_PE_MACHINES = {0x14C: "x86", 0x8664: "x86-64", 0x1C0: "ARM", 0x1C4: "ARM Thumb-2", 0xAA64: "ARM64",
                0x200: "IA-64", 0xEBC: "EFI byte code"}
_PE_SUBSYSTEMS = {1: "native (driver)", 2: "Windows GUI", 3: "Windows console", 9: "Windows CE", 10: "EFI application",
                  11: "EFI boot driver", 12: "EFI runtime driver", 14: "Xbox", 16: "Windows boot application"}


def _pe(data: bytes, fmt: FileFormat) -> View:
    if len(data) < 0x40 or not data.startswith(b"MZ"):
        raise ReaderError("not a PE file")
    (offset,) = struct.unpack_from("<I", data, 0x3C)
    if offset + 24 > len(data) or data[offset:offset + 4] != b"PE\x00\x00":
        raise ReaderError("no PE header")
    machine, sections, stamp = struct.unpack_from("<HHI", data, offset + 4)
    characteristics = struct.unpack_from("<H", data, offset + 22)[0]
    optional = offset + 24
    if optional + 72 > len(data):
        raise ReaderError("optional header runs past the bytes read")
    magic = struct.unpack_from("<H", data, optional)[0]
    subsystem, dll_flags = struct.unpack_from("<HH", data, optional + 68)
    built = datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d") if stamp else "not set"
    flags = {"ASLR (dynamic base)": 0x40, "DEP (NX compatible)": 0x100, "Control Flow Guard": 0x4000,
             "high-entropy ASLR": 0x20}
    hardening = [f"{name}: {'yes' if dll_flags & bit else 'NO'}" for name, bit in flags.items()]
    text = (f"PE {'PE32+' if magic == 0x20B else 'PE32'} {'DLL' if characteristics & 0x2000 else 'executable'}, "
            f"{_PE_MACHINES.get(machine, hex(machine))}, {_PE_SUBSYSTEMS.get(subsystem, f'subsystem {subsystem}')}, "
            f"{sections} sections, timestamp {built}\nhardening: " + ", ".join(hardening))
    return View(text, fmt, notes=["the timestamp is whatever the linker wrote; reproducible builds often fix or hash it"])


def _pcap(data: bytes, fmt: FileFormat) -> View:
    if data[:4] == b"\x0a\x0d\x0d\x0a":
        return View("pcapng capture", fmt, notes=["open it with Wireshark or tshark to read packets"])
    order = "<" if data[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    if len(data) < 24:
        raise ReaderError("too short for a pcap header")
    major, minor, _zone, _sig, snaplen, link = struct.unpack_from(order + "HHiIII", data, 4)
    packets, at = 0, 24
    while at + 16 <= len(data) and packets < 1_000_000:
        captured = struct.unpack_from(order + "I", data, at + 8)[0]
        at += 16 + captured
        packets += 1
    links = {1: "Ethernet", 101: "raw IP", 105: "802.11", 113: "Linux cooked", 127: "802.11 radiotap", 228: "IPv4",
             229: "IPv6", 195: "802.15.4", 249: "USB"}
    return View(f"pcap {major}.{minor}, link type {links.get(link, link)}, snapshot length {snaplen}, "
                f"{packets} packets in the bytes read", fmt)


def _pem_notes(text: str) -> list[str]:
    notes = []
    try:
        from cryptography import x509
    except ImportError:
        return ["certificate details need the cryptography package"]
    blocks = text.split("-----BEGIN CERTIFICATE-----")[1:]
    for block in blocks[:10]:
        pem = ("-----BEGIN CERTIFICATE-----" + block.split("-----END CERTIFICATE-----")[0]
               + "-----END CERTIFICATE-----\n").encode()
        try:
            cert = x509.load_pem_x509_certificate(pem)
        except ValueError as error:
            notes.append(f"a certificate block could not be parsed ({error})")
            continue
        expired = cert.not_valid_after_utc < datetime.now(UTC)
        notes.append(f"certificate: subject {cert.subject.rfc4514_string()}; issuer {cert.issuer.rfc4514_string()}; "
                     f"valid {cert.not_valid_before_utc:%Y-%m-%d} to {cert.not_valid_after_utc:%Y-%m-%d}"
                     + (" (EXPIRED)" if expired else "") + (" (self-signed)" if cert.subject == cert.issuer else ""))
    return notes


# ------------------------------------------------------------ dispatch --

_READERS = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "odf": _odf, "zip_listing": _zip_listing, "ole": _ole,
            "pickle": _pickle, "pytorch": _pytorch, "keras": _keras, "gguf": _gguf, "numpy": _numpy,
            "sqlite": _sqlite, "access": _access, "elf": _elf, "pe": _pe, "pcap": _pcap}


def view(path: str, data: bytes, *, size: int | None = None, complete: bool = True,
         fmt: FileFormat | None = None) -> View:
    """The model's view of one file's bytes (data may be the first part of a larger file)."""
    size = len(data) if size is None else size
    fmt = fmt or detect(path, data[:8192])
    if fmt.sensitive:
        return View(f"{fmt.title}: holds credentials or secrets; FORGE never shows it to a model", fmt)
    partial = [] if complete else [f"only the first {len(data):,} of {size:,} bytes were read"]
    if fmt.kind == "text":
        try:
            decoded = decode(data, fmt)
        except NotText as error:
            return View(f"{fmt.title}, {size:,} bytes: not readable as text ({error})", fmt)
        projection = project(decoded.text, fmt)
        notes = []
        if decoded.encoding != "utf-8" or decoded.bom or decoded.newline != "\n" or decoded.mixed:
            notes.append(f"{decoded.describe()}; edits keep them")
        notes += projection.notes()
        if fmt.reader == "pem":
            notes += _pem_notes(decoded.text)
        return View(projection.view, fmt, editable=complete, notes=partial + notes, decoded=decoded,
                    projection=projection)
    if fmt.format_id == "safetensors":
        def reader(d: bytes, f: FileFormat) -> View:
            return _safetensors(d, f, size)
    elif fmt.reader == "needs_converter":
        return View(f"{fmt.title}, {size:,} bytes", fmt, notes=[
            ("its text needs a converter (pdftotext for PDF; LibreOffice or pandoc for RTF), which FORGE does not "
             "run for the assistant yet")])
    else:
        reader = _READERS.get(fmt.reader or "")
    if reader is None:
        return View(f"{fmt.title}, {size:,} bytes; binary, not shown", fmt, notes=partial)
    try:
        result = reader(data, fmt)
    except ReaderError as error:
        return View(f"{fmt.title}, {size:,} bytes; could not be read: {error}", fmt, notes=partial)
    result.notes = partial + result.notes
    return result

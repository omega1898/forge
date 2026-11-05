"""Convert each checker's native output into `QualityFinding`s.

Tools that write SARIF go through `findings.from_sarif`. The rest get a small,
strict parser here. A parser never guesses: output it cannot read raises
`AdapterError`, which the runner records as a failed tool run, never as
"no findings".
"""

from __future__ import annotations

import json
import re
from typing import Any
from xml.etree import ElementTree

from forge.packs.native import parse_diagnostics
from forge.quality.findings import QualityFinding, cwes_in, from_sarif, relative_path

_CLANG_TIDY = re.compile(r"^(?P<path>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?P<level>warning|error): "
                         r"(?P<message>.*?) \[(?P<checks>[A-Za-z0-9_.,-]+)\]$")
_CPPCHECK_SKIP = {"missingInclude", "missingIncludeSystem", "checkersReport", "unmatchedSuppression",
                  "toomanyconfigs", "normalCheckLevelMaxBranches", "noValidConfiguration", "syntaxError"}
_CPPCHECK_LEVEL = {"error": "error", "warning": "warning", "style": "note", "performance": "note",
                   "portability": "warning"}
_SHELLCHECK_LEVEL = {"error": "error", "warning": "warning", "info": "note", "style": "note"}


class AdapterError(ValueError):
    pass


def json_documents(text: str) -> list[Any]:
    """Several JSON documents written back to back (GCC writes one SARIF log per translation unit)."""
    decoder, position, documents = json.JSONDecoder(), 0, []
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            return documents
        try:
            document, position = decoder.raw_decode(text, position)
        except ValueError as error:
            raise AdapterError(f"unreadable JSON at offset {position}: {error}") from None
        documents.append(document)


def sarif_stream(text: str, tool: str) -> list[QualityFinding]:
    return [f for document in json_documents(text) for f in from_sarif(document, tool)]


def clang_tidy(text: str) -> list[QualityFinding]:
    findings = []
    for line in text.splitlines():
        match = _CLANG_TIDY.match(line.strip())
        if match is None:
            continue
        check = match["checks"].split(",")[0]
        findings.append(QualityFinding(
            tool="clang-tidy", rule_id=check, level="error" if match["level"] == "error" else "warning",
            message=match["message"][:2000], path=relative_path(match["path"]), line=int(match["line"]),
            column=max(1, int(match["col"])), cwe=cwes_in(match["message"])))
    return findings


def cppcheck_xml(text: str) -> list[QualityFinding]:
    """cppcheck --xml (version 2): CWE comes from each error's `cwe` attribute."""
    start = text.find("<?xml")
    if start < 0:
        raise AdapterError("cppcheck wrote no XML report")
    try:
        root = ElementTree.fromstring(text[start:])
    except ElementTree.ParseError as error:
        raise AdapterError(f"unreadable cppcheck XML: {error}") from None
    findings = []
    for error in root.iter("error"):
        rule = error.get("id", "")
        if rule in _CPPCHECK_SKIP or error.get("severity") == "information":
            continue
        location = error.find("location")
        if location is None:
            continue
        findings.append(QualityFinding(
            tool="cppcheck", rule_id=rule, level=_CPPCHECK_LEVEL.get(error.get("severity", ""), "warning"),
            message=(error.get("msg") or rule)[:2000], path=relative_path(location.get("file", "")),
            line=max(1, int(location.get("line", "1"))), column=max(1, int(location.get("column", "1") or 1)),
            cwe=cwes_in(int(error.get("cwe"))) if (error.get("cwe") or "").isdigit() else []))
    return findings


def ruff_json(text: str, bandit_cwe: dict[str, list[int]]) -> list[QualityFinding]:
    """ruff --output-format json. Its S rules reuse bandit's numbers (S602 is B602), so bandit's CWE applies."""
    try:
        items = json.loads(text or "[]")
    except ValueError as error:
        raise AdapterError(f"unreadable ruff JSON: {error}") from None
    findings = []
    for item in items:
        code = str(item.get("code") or "E999")
        cwe = bandit_cwe.get("B" + code[1:], []) if re.fullmatch(r"S\d{3}", code) else []
        location = item.get("location") or {}
        findings.append(QualityFinding(
            tool="ruff", rule_id=code, level="error" if code.startswith(("E9", "F821")) else "warning",
            message=str(item.get("message", code))[:2000], path=relative_path(str(item.get("filename", ""))),
            line=max(1, int(location.get("row", 1))), column=max(1, int(location.get("column", 1))),
            cwe=cwes_in(*cwe)))
    return findings


def shellcheck_json(text: str) -> list[QualityFinding]:
    """shellcheck -f json1."""
    try:
        comments = json.loads(text or '{"comments": []}').get("comments", [])
    except (ValueError, AttributeError) as error:
        raise AdapterError(f"unreadable ShellCheck JSON: {error}") from None
    return [QualityFinding(tool="shellcheck", rule_id=f"SC{c.get('code')}",
                           level=_SHELLCHECK_LEVEL.get(c.get("level", ""), "note"),
                           message=str(c.get("message", ""))[:2000], path=relative_path(str(c.get("file", ""))),
                           line=max(1, int(c.get("line", 1))), column=max(1, int(c.get("column", 1))))
            for c in comments]


def compiler_text(text: str, tool: str) -> list[QualityFinding]:
    """GNAT, gfortran, cobc and GCC text diagnostics; the warning flag is the rule."""
    findings = []
    for item in parse_diagnostics(text):
        if item.severity not in ("error", "warning"):
            continue
        rule = item.flag or f"compiler-{item.severity}"
        findings.append(QualityFinding(
            tool=tool, rule_id=rule, level=item.severity, message=item.message[:2000],
            path=relative_path(item.path), line=max(1, item.line), column=max(1, item.column),
            cwe=cwes_in(item.message)))
    return findings

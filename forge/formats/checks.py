"""The syntax gate: does an edited file still parse as its format?

An edit that leaves a file unparseable is refused before any build or test
runs, and the model is told the line and the parser's message. Only new
problems count: a problem the file already had before the edit (same message,
same line text) never blocks an edit, so a misdetected format or a file that
was already broken cannot trap the model.

Every check is pure Python and never runs the file. Where no parser is
installed (YAML without PyYAML), the result says "not checked" and why; a
check that did not run is never reported as passed.

Rules beyond plain syntax, each because the toolchain would otherwise accept
the file and misbehave:

- Fixed-form COBOL and Fortran: compilers ignore columns 73 and later, so a
  statement that runs past column 72 is silently cut. (gfortran warns only
  with -Wline-truncation, which -Wall implies; for free form the default is
  an error, at 132 columns.)
- Batch files: with LF-only line endings, cmd.exe can fail to find labels
  for GOTO and CALL.
- PowerShell: Windows PowerShell 5.1 reads a script without a byte order
  mark in the ANSI code page, so non-ASCII text in a UTF-8 file without a
  BOM is misread there.
- Intel HEX and Motorola S-records: every record's checksum is verified.
"""

from __future__ import annotations

import ast
import configparser
import csv
import io
import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from xml.parsers import expat

from forge.formats.registry import FileFormat
from forge.formats.text import TextFile

MAX_PROBLEMS = 20


@dataclass(frozen=True)
class Problem:
    line: int
    message: str
    text: str = ""            # the line's text, so the same problem is recognized after other lines move
    severity: str = "error"   # error blocks an edit; warning is reported only

    def render(self, path: str) -> str:
        return f"{path}:{self.line}: {self.severity}: {self.message}"


@dataclass
class CheckResult:
    status: str                                   # passed | failed | not_checked
    problems: list[Problem] = field(default_factory=list)
    reason: str = ""

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]


def _line_text(text: str, line: int) -> str:
    lines = text.split("\n")
    return lines[line - 1] if 0 < line <= len(lines) else ""


def _problem(text: str, line: int, message: str, severity: str = "error") -> Problem:
    return Problem(line=line, message=message, text=_line_text(text, line), severity=severity)


# ------------------------------------------------------------------ parsers --


def _python(text: str, decoded: TextFile) -> list[Problem]:
    try:
        ast.parse(text, type_comments=False)
    except SyntaxError as error:
        return [_problem(text, error.lineno or 1, f"Python syntax error: {error.msg}")]
    except (RecursionError, MemoryError, ValueError) as error:
        return [_problem(text, 1, f"Python source could not be parsed: {type(error).__name__}")]
    return []


def _json_value(text: str) -> tuple[object, list[Problem]]:
    try:
        return json.loads(text), []
    except json.JSONDecodeError as error:
        return None, [_problem(text, error.lineno, f"JSON syntax error: {error.msg} (column {error.colno})")]
    except RecursionError:
        return None, [_problem(text, 1, "JSON is nested too deeply to parse")]


def _json(text: str, decoded: TextFile) -> list[Problem]:
    return _json_value(text)[1]


def _jsonl(text: str, decoded: TextFile) -> list[Problem]:
    problems = []
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip():
            try:
                json.loads(line)
            except (json.JSONDecodeError, RecursionError) as error:
                problems.append(Problem(number, f"JSON Lines: line is not one JSON value ({error})"[:200], line))
    return problems


def _ipynb(text: str, decoded: TextFile) -> list[Problem]:
    value, problems = _json_value(text)
    if problems:
        return problems
    if not isinstance(value, dict) or not isinstance(value.get("cells"), list) or "nbformat" not in value:
        return [_problem(text, 1, "a notebook needs a top-level object with \"cells\" (a list) and \"nbformat\"")]
    for index, cell in enumerate(value["cells"]):
        if not isinstance(cell, dict) or cell.get("cell_type") not in ("code", "markdown", "raw") \
                or "source" not in cell:
            return [_problem(text, 1, f"cell {index} needs a cell_type (code, markdown or raw) and a source")]
    return []


def _sarif(text: str, decoded: TextFile) -> list[Problem]:
    value, problems = _json_value(text)
    if problems:
        return problems
    if not isinstance(value, dict) or value.get("version") != "2.1.0" or not isinstance(value.get("runs"), list):
        return [_problem(text, 1, 'a SARIF 2.1.0 log needs "version": "2.1.0" and a "runs" list')]
    return []


def _toml(text: str, decoded: TextFile) -> list[Problem]:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        match = re.search(r"line (\d+)", str(error))
        return [_problem(text, int(match.group(1)) if match else 1, f"TOML syntax error: {error}")]
    return []


def _xml(text: str, decoded: TextFile) -> list[Problem]:
    parser = expat.ParserCreate()
    # expat does not fetch external entities unless a handler asks it to; none is set.
    try:
        parser.Parse(text.encode("utf-8"), True)
    except expat.ExpatError as error:
        return [_problem(text, error.lineno, f"XML is not well-formed: {expat.errors.messages[error.code]}")]
    return []


def _ini(text: str, decoded: TextFile, *, strict: bool = True, top_level: bool = True) -> list[Problem]:
    parser = configparser.ConfigParser(interpolation=None, strict=strict, allow_no_value=True,
                                       delimiters=("=", ":"), comment_prefixes=("#", ";"),
                                       inline_comment_prefixes=None, empty_lines_in_values=False)
    offset = 0
    source = text
    if top_level:
        # Keys before the first section are normal in .editorconfig and .npmrc.
        source, offset = "[forge top level]\n" + text, 1
    try:
        parser.read_string(source)
    except configparser.MissingSectionHeaderError as error:
        return [_problem(text, max(error.lineno - offset, 1), "a key appears before any [section]")]
    except configparser.DuplicateSectionError as error:
        line = (error.lineno or 1) - offset
        return [_problem(text, max(line, 1), f"section [{error.section}] appears twice")]
    except configparser.DuplicateOptionError as error:
        line = (error.lineno or 1) - offset
        return [_problem(text, max(line, 1), f"key {error.option!r} appears twice in [{error.section}]")]
    except configparser.ParsingError as error:
        return [_problem(text, max(lineno - offset, 1), f"not a key = value line: {line_text.strip()[:80]!r}")
                for lineno, line_text in error.errors[:MAX_PROBLEMS]]
    except configparser.Error as error:
        return [_problem(text, 1, f"INI syntax error: {error}"[:200])]
    return []


def _ini_check(text: str, decoded: TextFile) -> list[Problem]:
    return _ini(text, decoded)


def _systemd(text: str, decoded: TextFile) -> list[Problem]:
    # Repeated keys (several ExecStart=) are normal in units; a key outside a section is not.
    return _ini(text, decoded, strict=False, top_level=False)


_PROPERTIES_ESCAPE = re.compile(r"\\u(?![0-9A-Fa-f]{4})")


def _properties(text: str, decoded: TextFile) -> list[Problem]:
    problems = []
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.lstrip()
        if stripped.startswith(("#", "!")):
            continue
        # A \u escape must have four hex digits, or Properties.load fails with "Malformed \uxxxx encoding".
        for match in _PROPERTIES_ESCAPE.finditer(line):
            backslashes = len(line[:match.start()]) - len(line[:match.start()].rstrip("\\"))
            if backslashes % 2 == 0:
                problems.append(Problem(number, "malformed \\uXXXX escape: Java's Properties.load rejects it", line))
    return problems


_ENV_LINE = re.compile(r"""^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.\-]*\s*=""")


def _dotenv(text: str, decoded: TextFile) -> list[Problem]:
    problems: list[Problem] = []
    open_quote = None
    for number, line in enumerate(text.split("\n"), 1):
        if open_quote:
            if line.rstrip().endswith(open_quote):
                open_quote = None
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _ENV_LINE.match(line):
            problems.append(Problem(number, "not a NAME=value line", line))
            continue
        value = line.split("=", 1)[1].strip()
        if value[:1] in "\"'" and (len(value) == 1 or not value.endswith(value[0])):
            open_quote = value[0]
    if open_quote:
        problems.append(Problem(len(text.split("\n")), f"a {open_quote} quoted value is never closed"))
    return problems


def _delimited(text: str, delimiter: str) -> list[Problem]:
    problems = []
    width = None
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        for row in reader:
            if not row:
                continue
            if width is None:
                width = len(row)
            elif len(row) != width and len(problems) < 5:
                problems.append(_problem(text, reader.line_num, f"row has {len(row)} fields; the first row has {width}",
                                         severity="warning"))
    except csv.Error as error:
        problems.append(_problem(text, reader.line_num, f"CSV syntax error: {error}"))
    return problems


def _csv(text: str, decoded: TextFile) -> list[Problem]:
    return _delimited(text, ",")


def _tsv(text: str, decoded: TextFile) -> list[Problem]:
    return _delimited(text, "\t")


def _batch(text: str, decoded: TextFile) -> list[Problem]:
    if decoded.newline != "\r\n" and text.count("\n") > 0:
        return [Problem(1, "cmd.exe batch files need CRLF line endings; with LF only, GOTO and CALL can fail to "
                           "find their labels", severity="warning")]
    return []


def _powershell(text: str, decoded: TextFile) -> list[Problem]:
    if not decoded.bom and decoded.encoding == "utf-8" and any(ord(char) > 127 for char in text):
        line = next(n for n, line in enumerate(text.split("\n"), 1) if any(ord(char) > 127 for char in line))
        return [_problem(text, line, "non-ASCII text in UTF-8 without a byte order mark: Windows PowerShell 5.1 reads "
                                     "such a script in the ANSI code page and misreads it; save with a BOM",
                         severity="warning")]
    return []


_MAKE_RULE = re.compile(r"^[^\s#=:][^=]*?:(?![=:])")
_MAKE_DIRECTIVE = re.compile(r"^\s*(?:-?include|sinclude|ifeq|ifneq|ifdef|ifndef|else|endif|define|endef|export|"
                             r"unexport|override|vpath|undefine)\b")


def _makefile(text: str, decoded: TextFile) -> list[Problem]:
    if ".RECIPEPREFIX" in text:
        return []
    problems: list[Problem] = []
    in_rule = continued = False
    for number, line in enumerate(text.split("\n"), 1):
        if continued:
            continued = line.endswith("\\")
            continue
        continued = line.endswith("\\")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("\t"):
            continue
        if line[0] == " ":
            if in_rule and not _MAKE_DIRECTIVE.match(line) and "=" not in line:
                problems.append(Problem(number, "recipe line starts with spaces, not a tab (make: missing separator)",
                                        line))
            continue
        in_rule = bool(_MAKE_RULE.match(line))
    return problems[:MAX_PROBLEMS]


_DOCKER_INSTRUCTIONS = frozenset({"FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE", "ENV", "ADD", "COPY",
                                  "ENTRYPOINT", "VOLUME", "USER", "WORKDIR", "ARG", "ONBUILD", "STOPSIGNAL",
                                  "HEALTHCHECK", "SHELL"})
_HEREDOC = re.compile(r"<<-?[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")


def _dockerfile(text: str, decoded: TextFile) -> list[Problem]:
    problems: list[Problem] = []
    escape = "\\"
    seen_instruction = continued = False
    heredoc: str | None = None
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
            continue
        if not seen_instruction and re.match(r"#\s*escape\s*=\s*(\S)", stripped):
            escape = re.match(r"#\s*escape\s*=\s*(\S)", stripped).group(1)  # type: ignore[union-attr]
            continue
        if continued:
            continued = stripped.endswith(escape)
            continue
        if not stripped or stripped.startswith("#"):
            continue
        word = stripped.split(None, 1)[0].upper()
        if word not in _DOCKER_INSTRUCTIONS:
            problems.append(Problem(number, f"unknown Dockerfile instruction {stripped.split(None, 1)[0]!r}", line))
        elif not seen_instruction and word not in ("FROM", "ARG"):
            problems.append(Problem(number, "the first instruction must be FROM (or ARG before FROM)", line))
        seen_instruction = True
        continued = stripped.endswith(escape)
        found = _HEREDOC.search(stripped)
        if found and word in ("RUN", "COPY", "ADD"):
            heredoc = found.group(1)
    return problems[:MAX_PROBLEMS]


def _jcl(text: str, decoded: TextFile) -> list[Problem]:
    problems = []
    for number, line in enumerate(text.split("\n"), 1):
        if len(line) > 80:
            problems.append(Problem(number, f"JCL records are 80 columns; this line has {len(line)} and the rest "
                                            "is lost", line))
        elif line.startswith("//") and line[2:3] not in (" ", "*", ""):
            name = line[2:].split(" ", 1)[0]
            if len(name) > 8:
                problems.append(Problem(number, f"JCL name {name!r} is longer than 8 characters", line))
    return problems[:MAX_PROBLEMS]


_FREE_COBOL = re.compile(r"(?i)>>\s*SOURCE\s+(?:FORMAT\s+)?(?:IS\s+)?FREE|\$\s*SET\s+SOURCEFORMAT\s*\(?[\"']?FREE")
_COBOL_INDICATORS = " *-/dD$"


def _is_fixed_form(lines: list[str], *, cobol: bool) -> bool:
    """Most non-blank lines look like card images: sequence or label columns, then code from column 7 or 8."""
    voters = [line for line in lines if line.strip()]
    if not voters:
        return False
    if cobol:
        fixed = sum(1 for line in voters if len(line) >= 7 and (line[:6].strip() == "" or line[:6].isdigit())
                    and line[6] in _COBOL_INDICATORS)
    else:
        fixed = sum(1 for line in voters if line[0] in "Cc*!" or (len(line) > 6 and line[:5].strip().isdigit()
                                                                 or line[:5] == "     "))
    return fixed >= 0.8 * len(voters)


def _past_column_72(number: int, line: str, problems: list[Problem], language: str) -> None:
    # Columns 73-80 are the sequence area: ignored, so a statement reaching column 72 and going on is cut there.
    if len(line) > 72 and line[71] != " " and line[72:80].strip() and not line[72:80].strip().isdigit():
        problems.append(Problem(number, f"{language} code runs past column 72; the compiler ignores columns 73 and "
                                        "later, so the statement is cut. Continue it on the next line", line))


def _cobol_fixed(text: str, decoded: TextFile) -> list[Problem]:
    lines = text.split("\n")
    if any(_FREE_COBOL.search(line) for line in lines[:30]) or not _is_fixed_form(lines, cobol=True):
        return []
    problems: list[Problem] = []
    for number, line in enumerate(lines, 1):
        if len(line) >= 7 and line[6] not in _COBOL_INDICATORS and line.strip():
            problems.append(Problem(number, f"column 7 holds {line[6]!r}; the indicator area takes space, *, /, - "
                                            "or D", line))
        _past_column_72(number, line, problems, "COBOL")
    return problems[:MAX_PROBLEMS]


def _fortran_fixed(text: str, decoded: TextFile) -> list[Problem]:
    lines = text.split("\n")
    if not _is_fixed_form(lines, cobol=False):
        return []
    problems: list[Problem] = []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line[0] in "Cc*!" or "\t" in line[:6]:
            continue
        if line[:5].strip() and not line[:5].strip().isdigit():
            problems.append(Problem(number, "columns 1-5 hold a statement label, which must be digits", line))
        _past_column_72(number, line, problems, "Fortran")
    return problems[:MAX_PROBLEMS]


def _fortran_free(text: str, decoded: TextFile) -> list[Problem]:
    problems = []
    for number, line in enumerate(text.split("\n"), 1):
        if len(line) > 132 and not line.lstrip().startswith("!"):
            problems.append(Problem(number, f"free-form line of {len(line)} characters: gfortran stops at 132 by "
                                            "default (an error); continue it with &", line))
    return problems[:MAX_PROBLEMS]


def _intel_hex(text: str, decoded: TextFile) -> list[Problem]:
    problems: list[Problem] = []
    end_seen = False
    for number, line in enumerate(text.split("\n"), 1):
        record = line.strip()
        if not record:
            continue
        if end_seen:
            problems.append(Problem(number, "record after the end-of-file record", line))
            break
        if not record.startswith(":") or len(record) < 11 or (len(record) - 1) % 2 or \
                not re.fullmatch(r"[0-9A-Fa-f]+", record[1:]):
            problems.append(Problem(number, "not an Intel HEX record (:llaaaatt...cc in hex)", line))
            continue
        data = bytes.fromhex(record[1:])
        if data[0] != len(data) - 5:
            problems.append(Problem(number, f"byte count {data[0]} does not match the record's {len(data) - 5} data "
                                            "bytes", line))
        elif sum(data) & 0xFF:
            problems.append(Problem(number, "checksum does not match", line))
        elif data[3] > 5:
            problems.append(Problem(number, f"unknown record type {data[3]:02X}", line))
        end_seen = data[3] == 1
    if not problems and not end_seen and text.strip():
        problems.append(Problem(len(text.split("\n")), "no end-of-file record (:00000001FF)"))
    return problems[:MAX_PROBLEMS]


_SREC_ADDRESS = {"0": 2, "1": 2, "2": 3, "3": 4, "5": 2, "6": 3, "7": 4, "8": 3, "9": 2}


def _srecord(text: str, decoded: TextFile) -> list[Problem]:
    problems: list[Problem] = []
    for number, line in enumerate(text.split("\n"), 1):
        record = line.strip()
        if not record:
            continue
        if len(record) < 4 or record[0] not in "Ss" or record[1] not in _SREC_ADDRESS or \
                not re.fullmatch(r"[0-9A-Fa-f]+", record[2:]) or len(record) % 2:
            problems.append(Problem(number, "not a Motorola S-record (S<type><count><address><data><checksum>)",
                                    line))
            continue
        data = bytes.fromhex(record[2:])
        if data[0] != len(data) - 1:
            problems.append(Problem(number, f"byte count {data[0]} does not match the record's {len(data) - 1} bytes",
                                    line))
        elif (~sum(data[:-1])) & 0xFF != data[-1]:
            problems.append(Problem(number, "checksum does not match", line))
        elif data[0] < _SREC_ADDRESS[record[1]] + 1:
            problems.append(Problem(number, "record is too short for its address field", line))
    return problems[:MAX_PROBLEMS]


def _yaml(text: str, decoded: TextFile) -> CheckResult:
    if "{{" in text or "{%" in text:
        return CheckResult("not_checked", reason="template syntax ({{ }} or {% %}); the file is YAML only after "
                                                 "rendering")
    tabs = [Problem(number, "tab used for indentation; YAML does not allow tabs there", line)
            for number, line in enumerate(text.split("\n"), 1)
            if line[:len(line) - len(line.lstrip(" \t"))].count("\t") and line.strip()]
    try:
        import yaml  # optional: PyYAML
    except ImportError:
        if tabs:
            return CheckResult("failed", tabs[:MAX_PROBLEMS], "no YAML parser installed; only indentation was checked")
        return CheckResult("not_checked", reason="no YAML parser installed (pip install pyyaml); only indentation "
                                                 "was checked")
    try:
        for _ in yaml.compose_all(text, Loader=yaml.SafeLoader):  # builds nodes only; nothing is constructed
            pass
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark or error.context_mark
        line = mark.line + 1 if mark is not None else 1
        return CheckResult("failed", [_problem(text, line, f"YAML syntax error: {error.problem or error}")])
    except (yaml.YAMLError, RecursionError) as error:
        return CheckResult("failed", [_problem(text, 1, f"YAML could not be parsed: {type(error).__name__}")])
    return CheckResult("failed", tabs) if tabs else CheckResult("passed")


CHECKS: dict[str, Callable[[str, TextFile], list[Problem]]] = {
    "python": _python, "json": _json, "jsonl": _jsonl, "ipynb": _ipynb, "sarif": _sarif, "toml": _toml,
    "xml": _xml, "ini": _ini_check, "systemd": _systemd, "properties": _properties, "dotenv": _dotenv,
    "csv": _csv, "tsv": _tsv, "batch": _batch, "powershell": _powershell, "makefile": _makefile,
    "dockerfile": _dockerfile, "jcl": _jcl, "cobol_fixed": _cobol_fixed, "fortran_fixed": _fortran_fixed,
    "fortran_free": _fortran_free, "intel_hex": _intel_hex, "srecord": _srecord,
}


def check(fmt: FileFormat, decoded: TextFile, path: str = "") -> CheckResult:
    """Run the format's syntax check on decoded text."""
    if fmt.check is None:
        return CheckResult("not_checked", reason=f"no syntax check for {fmt.title}")
    if fmt.check == "yaml":
        return _yaml(decoded.text, decoded)
    if fmt.check == "ini" and path.casefold().endswith(".gitconfig"):
        problems = _ini(decoded.text, decoded, strict=False)  # git allows repeated keys (several fetch = lines)
    else:
        problems = CHECKS[fmt.check](decoded.text, decoded)
    problems = problems[:MAX_PROBLEMS]
    return CheckResult("failed" if any(p.severity == "error" for p in problems) else "passed", problems)


def new_problems(before: CheckResult | None, after: CheckResult) -> list[Problem]:
    """Problems the edit introduced: not in the file before (same message and line text)."""
    known = {(p.message, p.text) for p in before.problems} if before is not None else set()
    return [p for p in after.problems if (p.message, p.text) not in known]

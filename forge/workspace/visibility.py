"""What a model may see and change in a snapshot: one definition, used by every reader and by the controller.

Every documented case of an agent gaming its evaluation (special-casing
tests, deleting a detector's markers, reading the fix from git history) had
one thing in common: the agent could reach what scored it. FORGE keeps the
evaluation out of reach, and this module is the single place that says what
"out of reach" means:

- HIDDEN paths (evaluator files and hidden tests) are never shown to a model:
  not listed, not read, not searched.
- PROTECTED paths (hidden paths, the visible test suites, conftest.py and
  forge.toml) may be read but never changed by an implementation proposal;
  changing them needs a separate test_change proposal a person reviews
  (INT-T10).
- A file whose bytes equal an admitted evaluator suite is hidden wherever it
  sits, so copying a suite into the source tree does not expose it.
- forge.toml is shown with its expected results removed: the model sees how
  the project is built and what each test exercises, never the answers.

Paths are compared ignoring letter case and Unicode normalization form. Windows
and macOS disks treat "Tests/x" and "tests/x" as one file, so a check that
only matched the exact spelling could be passed by changing the case.
"""

from __future__ import annotations

import json
import tomllib
import unicodedata
from typing import Any

HIDDEN_PREFIXES = ("evaluation/", ".forge-eval/", "hidden_tests/")
PROTECTED_PREFIXES = ("tests/", "test/", *HIDDEN_PREFIXES)
PROJECT_FILE = "forge.toml"
# Exact paths: forge.toml holds the accepted tests and standards; a rewrite of it would also drop the
# expected results the model was never shown.
PROTECTED_FILES = ("conftest.py", PROJECT_FILE)
# Never indexed for model search: the project file's expectations would leak through search hits.
INDEX_HIDDEN = (*HIDDEN_PREFIXES, PROJECT_FILE)
EXPECTATIONS = ("exit_code", "stdout", "stdout_json", "stderr_contains")
HIDDEN_NOTICE = "this file is part of the protected evaluation and is not shown"


def path_key(path: str) -> str:
    """The form two paths share when a case-insensitive, normalization-insensitive disk would make them one file."""
    return unicodedata.normalize("NFC", path).casefold()


def is_hidden(path: str, digest: str | None = None, protected_digests: frozenset[str] | set[str] = frozenset()) -> bool:
    return path_key(path).startswith(HIDDEN_PREFIXES) or (digest is not None and digest in protected_digests)


def is_protected(path: str) -> bool:
    key = path_key(path)
    return key.startswith(PROTECTED_PREFIXES) or key in PROTECTED_FILES


def suite_digests(store) -> frozenset[str]:
    """Digests of every evaluator suite ever admitted: their bytes are hidden wherever they appear.

    A retired suite stays hidden too; its expected results may still be the ones the current suite checks.
    """
    rows = store.conn.execute("SELECT digest FROM artifacts WHERE artifact_kind = 'evaluator_suite'").fetchall()
    return frozenset(row[0] for row in rows)


def _value(value: Any) -> str:
    """A TOML literal for the plain values forge.toml uses (JSON's string and array syntax is valid TOML)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def project_view(raw: bytes) -> str:
    """forge.toml for a model: build and quality sections intact; each test without its expected results.

    Inputs (args) and the requirements a test verifies stay, so the model knows how the program is driven;
    stdin and input files are summarized by size; exit code and output expectations are removed.
    """
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return f"{PROJECT_FILE} could not be read as TOML, so it is not shown"
    lines = ["# forge.toml as shown to the assistant: expected results are part of the protected evaluation",
             "# and are not shown. Make the program correct; do not try to match particular outputs.", ""]
    for section in ("build", "quality"):
        if isinstance(data.get(section), dict):
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {_value(value)}" for key, value in data[section].items())
            lines.append("")
    for test in data.get("tests", []) if isinstance(data.get("tests"), list) else []:
        if not isinstance(test, dict):
            continue
        lines.append("[[tests]]")
        for key in ("id", "description", "args", "verifies"):
            if key in test:
                lines.append(f"{key} = {_value(test[key])}")
        if test.get("stdin"):
            lines.append(f"# stdin: {len(str(test['stdin']).encode())} bytes, not shown")
        if isinstance(test.get("files"), dict) and test["files"]:
            lines.append(f"# input files: {', '.join(sorted(test['files']))} (contents not shown)")
        lines.append("")
    return "\n".join(lines)


def model_text(path: str, data: bytes, *, digest: str | None = None,
               protected_digests: frozenset[str] | set[str] = frozenset()) -> str | None:
    """The text a model may see for one file, or None when the file is hidden."""
    if is_hidden(path, digest, protected_digests):
        return None
    if path == PROJECT_FILE:
        return project_view(data)
    # The format layer: secrets masked, documents and binaries summarized.
    from forge.formats.views import view

    return view(path, data).render(path)

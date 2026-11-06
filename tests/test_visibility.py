"""What a model may see and change: the one filter in forge/workspace/visibility.py.

Each test here is an attempt to reach the evaluation by a route a model could
take: a different letter case, a copy of the suite, or forge.toml's answers.
"""

from __future__ import annotations

import tomllib

from forge.workspace import visibility

PROJECT = b'''[build]
toolchain = "python"
entry = "main.py"

[quality]
standards = ["python-secure"]

[[tests]]
id = "warns_when_slow"
description = "a slow approach at high angle warns"
args = ["20", "100", "0"]
stdin = "SECRET_STDIN_TEXT"
files = { "input.txt" = "SECRET_FILE_TEXT" }
stdout = "SECRET_EXPECTED_STDOUT\\n"
exit_code = 3
verifies = ["HLR-1"]

[[tests]]
id = "json_report"
args = ["--json"]
stdout_json = { verdict = "SECRET_JSON_VALUE" }
stderr_contains = "SECRET_STDERR"
'''


def test_hidden_paths_are_hidden_in_any_letter_case() -> None:
    for path in ("evaluation/suite.json", "Evaluation/suite.json", "HIDDEN_TESTS/case.txt", ".Forge-Eval/x"):
        assert visibility.is_hidden(path), path
    assert not visibility.is_hidden("src/evaluation_notes.py")
    assert not visibility.is_hidden("src/evaluation/model.py")  # only top-level evaluator folders


def test_a_copy_of_an_admitted_suite_is_hidden_wherever_it_sits() -> None:
    assert visibility.is_hidden("notes/answers.json", "sha256:abc", {"sha256:abc"})
    assert not visibility.is_hidden("notes/answers.json", "sha256:other", {"sha256:abc"})


def test_protected_paths_cover_tests_evaluators_and_the_project_file() -> None:
    for path in ("tests/test_reader.py", "Tests/test_reader.py", "test/x.c", "conftest.py", "forge.toml",
                 "Forge.TOML", "hidden_tests/a", "evaluation/b"):
        assert visibility.is_protected(path), path
    for path in ("reader.py", "src/conftest_helpers.py", "src/forge.toml.example", "docs/tests.md", "forge.tomlx"):
        assert not visibility.is_protected(path), path


def test_unicode_forms_of_the_same_name_compare_equal() -> None:
    # macOS stores names decomposed (NFD); "tésts" and "tésts" are one folder there.
    decomposed, composed = "tésts/x", "tésts/x"
    assert visibility.path_key(decomposed) == visibility.path_key(composed)


def test_the_project_view_keeps_how_tests_run_and_drops_every_expected_result() -> None:
    view = visibility.project_view(PROJECT)
    for secret in ("SECRET_EXPECTED_STDOUT", "SECRET_JSON_VALUE", "SECRET_STDERR", "SECRET_STDIN_TEXT",
                   "SECRET_FILE_TEXT", "exit_code", "stdout"):
        assert secret not in view, secret
    parsed = tomllib.loads(view)  # still valid TOML, so a model reads it like the real file
    assert parsed["build"] == {"toolchain": "python", "entry": "main.py"}
    assert parsed["quality"] == {"standards": ["python-secure"]}
    first, second = parsed["tests"]
    assert first == {"id": "warns_when_slow", "description": "a slow approach at high angle warns",
                     "args": ["20", "100", "0"], "verifies": ["HLR-1"]}
    assert second == {"id": "json_report", "args": ["--json"]}
    assert "# stdin: 17 bytes, not shown" in view
    assert "# input files: input.txt (contents not shown)" in view


def test_a_broken_project_file_is_not_shown_at_all() -> None:
    assert visibility.project_view(b'[[tests]]\nstdout = "SECRET') == "forge.toml could not be read as TOML, so it is not shown"
    assert visibility.project_view(b"\xff\xfe") == "forge.toml could not be read as TOML, so it is not shown"


def test_model_text_routes_each_file_through_the_filter() -> None:
    assert visibility.model_text("evaluation/x.json", b"answers") is None
    assert visibility.model_text("copy.json", b"answers", digest="sha256:s", protected_digests={"sha256:s"}) is None
    assert "SECRET_EXPECTED_STDOUT" not in (visibility.model_text("forge.toml", PROJECT) or "")
    assert visibility.model_text("reader.py", b"print(1)\n") == "print(1)\n"

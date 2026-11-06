# `forge check`: build, test, coverage and analysis from one file

`forge check` builds a project, runs its tests, measures structural coverage
(including MC/DC) and runs the compiler's analyzers. Everything runs inside
FORGE's sandbox, and every result is recorded as evidence about the exact
snapshot. The compliance checks then read that evidence (see
[compliance.md](compliance.md)).

## Start

```bash
forge check init --source path/to/project
```

This writes a `forge.toml` with the build section filled in from the files
it finds, plus one example test for you to replace.

```bash
forge check
```

This captures the project, then runs the tests, analysis and coverage,
followed by the compliance gate. Each run starts from a fresh snapshot.

You can also run one step at a time: `forge check tests`, `forge check
coverage`, `forge check analyze` or `forge check sanitize`. Add `--json` for
machine-readable output.

## forge.toml

```toml
[build]
toolchain = "ada"          # ada | fortran | c | cpp | cobol | ocaml | c_arm | python
sources = ["main.adb"]     # Ada: the main unit (gnatmake finds the rest); others: every source

[[tests]]
id = "high_aoa_slow"       # lowercase letters, digits, underscores
args = ["20", "100", "0"]  # input files are reachable as {case}/name
stdout = "WARN\n"          # exact; or stdout_json, stderr_contains
exit_code = 0              # default 0; "nonzero" also works
verifies = ["HLR-1"]       # the requirements this test verifies
```

The fields mean:

- C and C++ projects may add `standard` (for example `"c11"` or `"c++17"`),
  `include_dirs` and `defines`.
- Python projects name their script with `entry = "main.py"` instead of
  `sources`.
- A test may add `stdin` (text) and `files` (a table of name = text) for the
  program to read.

A real example is `tests/fixtures/ada_stall/forge.toml`: an Ada stall
warning with 8 tests tied to three high-level requirements.

## Protected tests

The tests in `forge.toml` are only a proposal:

- The first `forge check` accepts them as a protected suite, recorded in
  your name. The expected values are stored outside the project and never
  enter the sandbox.
- If `forge.toml` changes later, FORGE keeps running the tests you accepted
  and tells you. This holds whoever made the change, you or a model. Review
  the change, then run `forge check --accept-tests`.
- A model can run checks, but only a trusted person can accept tests.

This is what makes a passing check mean something: code cannot pass by
weakening the tests it is judged by.

## What the assistant may see and change

Every documented case of an agent gaming its evaluation (special-casing
tests, deleting a detector's markers, reading the fix from git history) had
one thing in common: the agent could reach what scored it. One module,
`forge/workspace/visibility.py`, says what is out of reach. Every reader
uses it: the assistant's `list_files`, `read_file` and `search_code`, the
search index, the context builder and the controller.

| Path | The model can read it | The model can change it |
|---|---|---|
| `evaluation/`, `.forge-eval/`, `hidden_tests/` | no: not listed, not read, not found by search | no |
| any file whose bytes equal an admitted evaluator suite, wherever it sits | no | (not applicable) |
| `forge.toml` | only with every expected result removed: builds, standards, test ids, arguments and requirements stay; `stdin` and input files are summarized by size and name | no |
| `tests/`, `test/`, `conftest.py` | yes | no |
| everything else | yes | yes, through a reviewed proposal |

Paths are compared ignoring letter case and Unicode form. Windows and macOS
disks treat `Tests/x` and `tests/x` as one file, so a check that only
matched the exact spelling could be passed by changing the case.

A patch that touches a path the model may not change is refused before
anything is built. The refusal goes into the model's next prompt: change the
implementation only, and if a test looks wrong, ask the person. A failing
case reports which case failed and why ("stdout text differs from the
expected text"), never the expected value. A change to the tests or
`forge.toml` is a separate `test_change` proposal that a person reviews.

## Coverage (Ada, Fortran, C, C++ with GCC 14)

`forge check coverage` builds the project with `--coverage
-fcondition-coverage` at `-O0` and runs every accepted test against it. In a
second sandbox, `gcov-14` reads the counters. Three figures come back:

| Figure | Meaning |
|---|---|
| statement | lines executed |
| decision | GCC branch coverage, not counting exception edges. Every decision outcome is a branch, so 100% here means 100% decision coverage. Compiler-inserted checks, such as Ada range checks, are branches too. |
| MC/DC | GCC 14's masking MC/DC: each condition shown to independently change its decision's outcome |

Every uncovered condition is named by file and line, along with whether its
true or false outcome is missing. That tells you which test case to add.
Files the compiler generates are listed but not counted; GNAT's binder file
`b~main.adb` is one.

The compliance gate reads these figures. Avionics level A needs MC/DC at
100%, level B decision coverage, and level C statement coverage. You can
justify code that is not covered with a waiver that names it.

Limits, also recorded with the evidence:

- The instrumented program writes its own counters, so a program built to
  forge them could fake coverage. Source that refers to coverage internals
  (`__gcov`, `.gcda`, `GCOV_PREFIX` and similar) makes the result
  inconclusive, and the gate fails.
- Language rules can expand one source expression into several conditions.
  For example, Ada's string `=` compares the length and then the contents,
  and those conditions count.
- Coverage is measured on an instrumented build of the same sources with the
  same tests, not on the release binary.

## Analysis and sanitizers

`forge check analyze` runs the checkers for the project's coding standards:

- by default, CERT C/C++ and the 2025 CWE Top 25 for C and C++;
- for Python, ruff's correctness and security rules;
- GNAT, gfortran or cobc with every warning on;
- ShellCheck for scripts.

You choose others in `forge.toml` under `[quality]`; see
[quality.md](quality.md). Every finding is tied to the CERT rule, CWE
weakness or Power of Ten rule it breaks. One SARIF 2.1.0 file is recorded per
run. Blocking findings fail the compliance gate's static-analysis check.

C and C++ tests run under AddressSanitizer and UndefinedBehaviorSanitizer;
`forge check sanitize` runs just that. A sanitizer report fails the test and
is recorded as a confirmed finding.

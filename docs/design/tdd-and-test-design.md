# TDD and functional test design in FORGE

Status: built and tested (`forge/tdd/`, `tests/test_tdd.py`). This note
explains what the code enforces, why, and where its guarantees stop.

## Why build TDD as code

A small local model is good at producing a plausible test and a plausible
implementation. It is much worse at following discipline: writing the test
first, keeping it honest, and not "fixing" a failing test by weakening it.
Those rules are mechanical, so FORGE checks them with code and records the
result. The model is still useful: it proposes tests and code. It just
cannot skip a step.

## The cycle

| Phase | Compares | Must hold | Typical model failure it catches |
|---|---|---|---|
| Red | base → red snapshot | only test files changed; at least one test added or changed; each new test fails by **assertion** or **missing symbol** (not a broken test); every previously passing test still passes | writing code first; a "test" that already passes; a test with a syntax error counted as red |
| Green | red → green snapshot | test files byte-for-byte unchanged; production changed; every test passes, none skipped; integrity scan clean | editing or weakening the test to get green; skipping a test |
| Refactor | green → refactor snapshot (repeatable) | tests unchanged; everything still passes; integrity scan clean | "refactoring" that quietly changes tests |

Each phase records `tdd_red`, `tdd_green` or `tdd_refactor` evidence naming
the previous phase's evidence, so the whole chain is reconstructible from
the project record. A phase cannot follow a rejected phase.

## Mutation testing

After green, FORGE changes the lines the green phase touched, one small
change at a time (`>=` to `>`, `and` to `or`, `+` to `-`, a constant + 1,
`return x` to `return None`, a negated condition). It runs the tests
against each mutant in the sandbox. A mutant the tests do not notice
("survives") becomes a finding at that line. A minimum mutation score can
be required for green. Example from the tests: tests that never try an
amount of exactly 100 let the `>=` to `>` mutant survive, which is exactly
the missing boundary test.

A survivor is a lead, not a verdict: some mutants are equivalent to the
original program, and only a person can decide that.

## Reward hacking and the trust boundary

Unit tests run inside the same process as the code they test. Code written
to pass tests rather than to work can detect the test run (`sys.modules`,
stack inspection), end the process early (`os._exit`), switch off time
limits (signals), or imitate the runner's output. FORGE therefore:

1. authenticates the result channel with a one-time token read before any
   project code is imported (a printed fake result line is ignored);
2. scans changed production code for test-aware constructs; any flag turns
   an otherwise green phase into `needs_review`, with the exact lines;
3. treats unit-test results as workflow evidence. Acceptance still rests on
   the protected black-box evaluator, which the candidate cannot influence.

Secure against: a model that edits tests, writes code before tests, counts
a broken test as red, or uses the common test-detection tricks listed in
`forge/tdd/integrity.py`. Not secure against: deliberately obfuscated code
that defeats a static scan (for example building names at run time). That
is why the black-box gate remains.

## Functional test design (`forge/tdd/design.py`)

| Technique | Input | Output | Built-in check |
|---|---|---|---|
| Boundary values | integer or length range | values just outside, on and just inside each edge | invalid points are labelled invalid |
| Equivalence classes | range or enumeration | one representative per valid and invalid class | |
| Pairwise | parameters and their values | small set of rows | every pair of values is verified covered |
| Decision table | conditions, rules with "does not matter" | one case per rule | gaps, contradictions, rules that never apply |
| State machine | states, events, transitions | transition tour and "sneak path" cases | nondeterminism, unreachable states, dead ends |

`render_unittest` turns designed cases into a standard-library test module.
Values are written as Python literals and names must be plain identifiers,
so a hostile string in a case can never become code. The rendered module
can be the red phase directly, as `test_designed_cases_become_the_red_phase`
shows.

## Limits

- The runner supports `unittest` classes and plain `test_*` functions with
  bare `assert`. pytest fixtures and plugins are reported as unsupported,
  not guessed. Admitting pytest itself (as a pinned wheel) is possible later.
- Mutation testing covers Python only and changed lines only, capped per
  phase.
- Assertion messages from bare `assert` are short, because there is no
  pytest-style assertion rewriting.

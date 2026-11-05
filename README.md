# FORGE

An offline software engineering controller, built from the *FORGE Engineering
Blueprint, design baseline 0.1 (4 November 2026)*.

> **Status: built and tested on this Linux machine: F01-F24 and D01-D11, plus
> TDD tooling** (F20-F22 within the limits below). This includes COBOL (GnuCOBOL),
> OCaml, Java, C/C++, Bash with shellcheck, an ARM emulator tier (QEMU),
> reverse engineering with radare2, and a live COBOL-to-Java migration
> comparison. **Not built:** native Windows and macOS hosts (no such
> machines), PowerShell (installing Microsoft's package repository was not
> permitted in this session), JCL execution, real boards, a dedicated RE
> virtual machine (owned samples only, in the Linux sandbox), and real local
> model weights (the model hosts are blocked by this environment's network
> policy). Those checks report `not_run` instead of passing. Nothing here is
> security-qualified; the Linux sandbox is for trusted code only.

## What exists today

| Ticket | Scope | State |
|---|---|---|
| F01 | Package, contract versions, ID/digest/time conventions, offline JSON Schemas | **Done** |
| F02 | Project record, migrations, events/projections, trusted grant and decision store | **Done** (see scope notes) |
| D01 | `project.summary`: the standup skill as deterministic code over the event log, with time travel | **Done** |
| F03 | Artifact store, workspace snapshots, approved source roots | **Done** |
| F04 | Four-layer authorization; Linux namespace sandbox with cgroup limits; output collector; Podman/VM backends report unavailable | **Done** (Linux backend is for trusted code only; see below) |
| F05 | Protected black-box evaluator; evidence, assessments, findings; D02 analyzer rules and verdict; D03 determinism and suite-strength probes | **Done** |
| F06 | Plan deltas validated as a unit; completion evaluator over assessments, waivers and decisions; supersede links | **Done** |
| F07 | Job specs with digest projection, outbox, host agent with durable ledger, lease-epoch fencing, cancellation, crash recovery and external-effect reconciliation | **Done** |
| F08 | Authenticated loopback JSON-RPC API and SSE event stream; `forge` CLI client; recorded intents | **Done** |
| F09 | Model gateway: probed configurations, fake and OpenAI-compatible (llama.cpp) adapters, token budgets, redaction, call records; D04 model slots with grammar-safe schemas and fact-checking validators | **Done** |
| F10 | Snapshot-bound index (Python ast definitions, imports, tests; SQLite FTS5); context bundles with budgets, omissions and excerpts; citation checks; D05 project facts | **Done** |
| F11 | Python pack; build_tool workflow from request to released package (the walking skeleton); offline packaging check; D06 capabilities (review, bug fix, test strategy, tech debt) | **Done** |
| F12 | Change proposals bound to exact digests; safe apply with conflict detection and a crash-recovery journal; strict patch applier; bounded assistant loop; D07 generate-and-verify admission | **Done** (walking skeleton complete) |
| F13/F14 | VSCodium extension (`extension/`, TypeScript, no third-party packages): loopback-only API client; event stream with Last-Event-ID resume, exactly-once in-order application and refresh on gaps; task tree built from the same `project.summary` the CLI reads; Markdown rendered with all HTML escaped and only http/https links, in webviews with scripts and command URIs disabled; keyboard-only review where approval requires every change to have been seen and the decision is bound to the proposal digest | **Logic done and tested** (unit tests and a live-server integration test in Node); the thin editor glue in `extension.ts` has not been run inside VSCodium here |
| F15 | Teacher: separate learner ledger (never consulted by the controller, so the build is never gated); concept cards start as drafts and need a trusted human's review; prerequisite cycles refused; stale card sources detected; exercises with hidden evaluators (accepted answers, or hidden suites run in the sandbox) kept out of help context; attempts are append-only and an attempt is assisted whenever a hint was shown first, decided by code; corrections supersede without erasing; evidence levels per concept instead of a score; timing only on request | **Done** (no editor UI yet; see F13/F14) |
| D08 | Tutoring rules from the `learn` skill as validators: enumerated diagnosis, exactly one question, hints that contain the answer, its numbers or a hidden test's output are rejected (whole-token matching) | **Done** |
| D10 | Deploy checklist from project facts with every item bound to evidence ("tests pass" only by current assessments of the release snapshot; open high findings block); incident timeline with explicit gaps; blameless postmortem renderer that refuses action items without owner, due date and link; documentation freshness check against the snapshot index (deleted functions and missing files are flagged) | **Done** |
| D11 | Evaluation harness: digest-frozen sets split once per capability into development and a sealed held-out part (every access logged); deterministic assertions; repeated runs exposing flaky cases; Wilson intervals and a Newcombe interval on the difference, so "improvement shown" requires the whole interval above zero | **Done** (mechanics verified with a simulated small model; real numbers need local model weights) |
| F16 | C/C++ packs: compilers admitted by digest with helper programs (cc1, as, ld) and target recorded; exact build context and `compile_commands.json`; build inside the sandbox with protected flags; structured diagnostics; ASan/UBSan/LSan reports as confirmed findings; sanitizer-bypass detection (suppression attributes, runtime option overrides, inline assembly, sanitizer-aware code via differential preprocessing); deterministic malformed-input generation with an approved reference oracle | **Done** (gcc tested; clang admitted but not exercised by tests) |
| F17 | Truthful platform status: every backend states where code really executes (OS, architecture, VM/WSL/container/emulator, and the OS it is reached through); evidence records it; criteria with required platforms get one result per platform, and a platform no admitted host runs natively is recorded `not_run` and blocks completion; host routing by native compatibility; Markdown platform table | **Done** (Linux host only; Windows/macOS hosts are not available here, so their checks show as `not_run` by design) |
| F18 | Performance workflow: exclusive machine leases (inference and indexing queue behind a benchmark; the model gateway takes a shared lease per call); candidates measured only when correctness-qualified, on a reserved evaluation workload, in alternating rounds; background CPU and hypervisor-steal interference makes the measurement inconclusive; output must match the baseline in every repetition; seeded bootstrap interval on the ratio of medians; raw repetitions and host conditions recorded | **Done** (wall-clock of whole runs on one named Linux host; no hardware counters, no latency percentiles yet) |
| D09 | Variance bridge in exact rational arithmetic with the tie-out invariant enforced (the skill's own price/volume example ties out only with the interaction term); multi-item cost bridge (volume, mix, rate, new and removed items); either-limit thresholds; three-bucket reconciliation with non-overlapping aging and preparer/reconciler separation | **Done** |
| TDD | Test-driven development as enforced code (user request): sandboxed standard-library unit runner with failure classification (assertion, missing symbol, broken test, error, timeout) and an authenticated result channel; red/green/refactor rules checked against snapshots and chained as evidence; mutation testing on changed lines with surviving mutants as findings and an optional minimum score; reward-hacking scan for test-aware production code; functional test design (boundary values, equivalence classes, verified pairwise, decision-table and state-machine checks, safe test rendering). See `docs/design/tdd-and-test-design.md` | **Done** |
| F19 | Java pack: javac/java admitted by digest with the JDK's external configuration directory mounted read-only; build in the sandbox, classes collected, protected suite run through `{java}`; compile diagnostics. COBOL/JCL: reported unavailable (no `cobc` here); copybook layouts, EBCDIC code pages, zoned and packed decimals, COBOL ROUNDED and exact record comparison with explicit accepted differences work without a compiler. Measured pack status table | **Done**: Java, and COBOL programs compiled with GnuCOBOL and run in the sandbox (`forge/packs/compiled.py`); JCL not executed |
| F23 | Bash pack and scoped testing tools: script tools declare typed arguments (path, integer, choice, patterned text, target); hostile values are refused with a reason and accepted ones travel as argv, so shell metacharacters stay inert; target scope refuses URL userinfo, non-canonical IP forms, out-of-network IPv4-mapped IPv6, non-ASCII hosts, ungranted ports and schemes, and DNS answers outside the grant (resolved addresses are pinned); five-rule Bash linter | **Done** for Bash (plus shellcheck run in the sandbox) and OCaml (reference and round-trip property checks); PowerShell **not installed** (repository install not permitted here) |
| F24 | Offline bundles: Ed25519-signed manifests verified only with keys a trusted user enrolled; expiry, downgrade/replay ledger, per-file digests, platform checks, safe archive extraction, admission hook, immutable versions, atomic switch, recorded rollback | **Done** (proves origin and integrity, not that contents are benign) |
| F20 | Migration (partial): restartable batch with exactly-once output per record (a rolled-back checkpoint cannot duplicate a side effect); comparison against approved reference output with explicit accepted differences; preparer/reconciler separation; cutover and rollback rehearsal that refuses cutover on any unaccepted difference | **Done for a bounded step**: the live COBOL job is the approved behavior; the Java BigDecimal port matches it and a floating-point port's differences are exposed record by record. JCL and real cutover are not exercised |
| F21 | Reverse engineering with radare2 in the sandbox: owned-sample intake; bounded extraction citing sample digest, address and image base; sample text fenced as untrusted data; hypothesis ledger decided by one-field-at-a-time experiments (the seeded little-endian hypothesis is refuted, big-endian supported); harness validated separately from parser findings; wrong image bases flagged; differential corpus against a compatibility implementation with mismatches turned into experiments | **Done for owned samples**; hostile samples need the RE virtual machine, which does not exist here |
| F22 | Embedded (partial): host, emulator and hardware evidence tiers never stand in for each other; board actions need an approval bound to the board serial, image digest and operation; a simulated A/B board on the F07 external-effect path shows that power loss mid-flash leaves `unknown` state for a person and is never re-flashed automatically (EX-T11) | **Emulator tier done** (ARM build of the packet parser run under QEMU user-mode emulation, recorded as emulator evidence, never as hardware); real boards are simulated |
| Hybrid | Local model by default; a person may escalate a stuck task to a frontier model (Anthropic API, optional `forge[frontier]` extra). FORGE explains why the local loop stopped, builds the exact package to send, and sends it only when a trusted person approves that package's digest; refusals are recorded with their stated reason, never reworded around; frontier patches pass the same evaluator as local ones | **Done** (live API call needs `ANTHROPIC_API_KEY`) |
| Compliance | Domain gates and control-by-control reports. A person picks what the software is for (`forge comply set avionics --level A`; also federal, defense, medical, banking, automotive, industrial, rail, nuclear, space, general); new execute tasks then carry the gate and cannot complete until its checks are met on the snapshot or waived by a person. 15 automated checks computed from recorded evidence; producers for a CycloneDX SBOM, secrets scan, requirements trace (`Implements:`/`Verifies:` tags) and offline OSV vulnerability matching; bundled NIST SP 800-53 Rev. 5 (with baselines), SP 800-171 Rev. 3 and SP 800-218 (SSDF) imported from NIST's official OSCAL files and cited by source URL and SHA-256; 37 industry standards named with how to obtain them; licensed standards imported by the user (OSCAL or CSV) and cited like NIST's; human attestations for process controls. See [docs/design/compliance.md](docs/design/compliance.md) | **Done** |
| Quality | Coding standards as code: SEI CERT C/C++ (rules, risk tables and compliant examples bundled under CC BY 4.0), the 2025 CWE Top 25 and CISQ/OWASP memberships from a pinned CWE 5.0 pack, NASA/JPL Power of Ten (FORGE's own analyzer), Ada `pragma Restrictions`, Fortran 2018 conformance, PEP 8, ShellCheck; enforced by GCC 14 `-fanalyzer`, clang-tidy, cppcheck, ruff and the compilers in the sandbox; one SARIF 2.1.0 file per run with GitHub-compatible line fingerprints; and a quality gate in the assistant loop that rejects a model's test-passing change if it adds a blocking finding, sending back the broken rule and CERT's compliant example. Chosen in `forge.toml` (`[quality] standards = [...]`). See [docs/design/quality.md](docs/design/quality.md) and the research report in `reports/` | **Done** (MISRA/AUTOSAR/HIC++ text is not bundled: IDs only, or the user's licensed import) |
| Check | `forge check`: one `forge.toml` describes the build and the test cases (with the requirements each verifies); the first run accepts the tests as a protected suite in the trusted person's name, and later edits are not used until a person accepts them. Builds and tests Ada (GNAT 14), Fortran, C/C++ (under ASan/UBSan), COBOL, OCaml, ARM C under emulation and Python in the sandbox; structural coverage with GCC 14 (statement, branch, masking MC/DC) naming every uncovered condition, with compiler-generated files excluded and coverage-internals tampering made inconclusive; compiler warnings and the GCC analyzer as findings; then the compliance gate. See [docs/design/check.md](docs/design/check.md) | **Done** (Linux sandbox; coverage is measured on an instrumented build of the same sources and tests) |

Acceptance results checked by the test suite (`tests/`):

- Fresh installation loads schemas with networking disabled; invalid IDs and
  incompatible versions fail with clear messages. *(F01)*
- A transaction failure leaves no partial state. *(F02)*
- Repository-local grant files produce proposals only and cannot create an
  approved grant. *(F02)*
- Repeating an identical command returns one stored result; reusing the key
  with different content is rejected. *(build step C1)*
- A dependency cycle cannot be committed; stale-version updates do not
  overwrite newer data. *(build step C2)*
- Replaying the event log rebuilds exactly the live projection tables.
- The project summary lists done, running, ready, blocked-with-reason,
  waiting and pending-decision items exactly, with no model involved; a
  summary rebuilt from the event log is identical to one read live; hostile
  titles cannot inject Markdown or HTML into the report. *(D01)*

## Try it

Requires Python 3.12 or newer.

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/forge-dev demo --root forge-data
```

```bash
.venv/bin/forge-dev inspect --root forge-data
```

```bash
.venv/bin/forge-dev summary --root forge-data
```

Run the controller with its API, then use the `forge` client from another
terminal:

```bash
.venv/bin/forge serve --root forge-api
```

```bash
.venv/bin/forge init --root forge-api --name "Packet reader" --objective "Read packets safely"
```

```bash
.venv/bin/forge intent --root forge-api "Build a utility that reads a packet file"
```

(On Windows use `.venv\Scripts\` instead of `.venv/bin/`.)

The walkthrough for this stage is in
[docs/sessions/01-records-and-commands.md](docs/sessions/01-records-and-commands.md).

How FORGE replaces assistant "skills" with deterministic code plus narrow,
validated model calls is analyzed in
[docs/design/skill-distillation.md](docs/design/skill-distillation.md),
including the D-ticket plan that attaches to the 24 blueprint tickets.

## Layout

```
forge/contracts/   IDs, digests, timestamps, versions, typed command/record models, JSON Schemas
forge/record/      SQLite connection, migrations, event log, projections, single-writer lock
forge/authority/   user-level trust store (grants); repository grant files -> proposals only
forge/engine/      dependency-cycle detection and the task status transition table
forge/controller/  apply_command: the only path that changes project state
forge/reports/     deterministic reports over records (project summary, standup)
forge/cli.py       development CLI (in-process; replaced by the RPC client in F08)
tests/             acceptance and failure tests
```

## Scope notes and deviations from the blueprint

These are deliberate and recorded so later tickets do not assume otherwise.

1. **Migration 0001 holds only the tables used now.** The blueprint lists
   snapshots, artifacts, jobs, evidence, assessments and outbox in
   `0001_core.sql`. They will arrive as migrations 0002+ alongside the code
   that defines their columns, instead of being guessed early.
2. **One database file per project.** `project.create` is the first command
   in a fresh database. Opening projects by approved root handle and a
   controller-wide project registry belong to F08 (`project.open`).
3. **`task.transition` is an internal controller command.** The public
   `task.start` / `task.cancel` methods arrive with the API in F08. Moving a
   task to `completed` is refused until the completion evaluator exists
   (F05/F06); moving an `execute` task to `running` is refused until job
   admission and isolation exist (F04/F07). Refusing is safer than allowing
   an unverified "done".
4. **`source_roots` are labels.** Binding a label to a host directory is
   trusted user-level configuration, added with snapshots in F03.
5. **Linux isolation backend.** The blueprint's first backend is rootless
   Podman. Podman and bubblewrap were not installable where FORGE was built,
   so `forge/sandbox/linux_ns.py` composes the same kernel features
   (namespaces, pivot_root, setpriv, cgroups) and passes the EX-T03/T04/T05
   admission tests. It requires root, has no seccomp filter, shares the host
   kernel, and reports `suitable_for_hostile_code: false`. Unknown binaries
   need the disposable VM profile, which is not built.
6. **Windows file locking** uses `msvcrt` and is exercised only by the
   Windows CI job, not by the Linux environment where this was written.

## Contributing rule

Every change ships with a test that exercises its failure case, not only the
happy path. Regenerate schemas after changing a contract model:

```bash
python -m forge.contracts.schemas --write
```

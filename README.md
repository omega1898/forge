# FORGE

An offline software engineering controller, built from the *FORGE Engineering
Blueprint, design baseline 0.1 (4 November 2026)*.

> **Status: walking skeleton complete (F01-F12).** One local Linux host, one
> model adapter (fake or any OpenAI-compatible local server such as
> llama.cpp), a small Python utility, project record, snapshots, sandboxed
> tools, protected evaluation, proposed patches, safe application,
> packaging and recovery all work end to end and are tested. Not yet built:
> the editor extension, teaching, non-Python language packs, Windows/macOS
> hosts, offline bundles. Nothing here is security-qualified; the Linux
> sandbox is for trusted code only.

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
| F16 | C/C++ packs: compilers admitted by digest with helper programs (cc1, as, ld) and target recorded; exact build context and `compile_commands.json`; build inside the sandbox with protected flags; structured diagnostics; ASan/UBSan/LSan reports as confirmed findings; sanitizer-bypass detection (suppression attributes, runtime option overrides, inline assembly, sanitizer-aware code via differential preprocessing); deterministic malformed-input generation with an approved reference oracle | **Done** (gcc tested; clang admitted but not exercised by tests) |
| F17 | Truthful platform status: every backend states where code really executes (OS, architecture, VM/WSL/container/emulator, and the OS it is reached through); evidence records it; criteria with required platforms get one result per platform, and a platform no admitted host runs natively is recorded `not_run` and blocks completion; host routing by native compatibility; Markdown platform table | **Done** (Linux host only; Windows/macOS hosts are not available here, so their checks show as `not_run` by design) |
| F18 | Performance workflow: exclusive machine leases (inference and indexing queue behind a benchmark; the model gateway takes a shared lease per call); candidates measured only when correctness-qualified, on a reserved evaluation workload, in alternating rounds; background CPU and hypervisor-steal interference makes the measurement inconclusive; output must match the baseline in every repetition; seeded bootstrap interval on the ratio of medians; raw repetitions and host conditions recorded | **Done** (wall-clock of whole runs on one named Linux host; no hardware counters, no latency percentiles yet) |
| D09 | Variance bridge in exact rational arithmetic with the tie-out invariant enforced (the skill's own price/volume example ties out only with the interaction term); multi-item cost bridge (volume, mix, rate, new and removed items); either-limit thresholds; three-bucket reconciliation with non-overlapping aging and preparer/reconciler separation | **Done** |
| F24 | Offline bundles: Ed25519-signed manifests verified only with keys a trusted user enrolled; expiry, downgrade/replay ledger, per-file digests, platform checks, safe archive extraction, admission hook, immutable versions, atomic switch, recorded rollback | **Done** (proves origin and integrity, not that contents are benign) |
| F13–F15, F19–F23 | Editor, teaching, other packs | In progress |
| D08, D10, D11 | Remaining skill-distillation tickets (see design doc) | Not started |

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

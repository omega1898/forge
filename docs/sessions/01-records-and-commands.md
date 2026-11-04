# Session 1: records, commands, and the event log

Blueprint tickets **F01** and **F02**. This guide explains what was built and
why, how to check it yourself, and what to try next. It assumes no prior
programming experience.

## The problem

FORGE will let models, editors, and execution machines all work on one
software project. Without one owner of the project's state, these problems
appear:

- A network retry submits "create task" twice, so you get two tasks.
- Two windows read a task, both change it, and the second silently erases
  the first change (a *lost update*).
- A crash halfway through a change leaves half-written state.
- A file inside a downloaded repository says "FORGE is allowed to run
  anything", and FORGE believes it.

Session 1 builds the foundation that prevents all four, before any model or
code execution exists.

## Words you need

| Term | Meaning here |
|---|---|
| **Controller** | The one program allowed to change a project's recorded state (`forge/controller/service.py`). |
| **Command** | A request to change state, such as `task.create`. It arrives as JSON and is treated as untrusted. |
| **Schema / contract** | The allowed shape of data. Fields, types, and limits. `forge/contracts/`. |
| **Event** | An immutable record of an accepted change, such as "task X moved from queued to blocked". |
| **Projection** | A current-state table computed from events (the `tasks` table). It can be thrown away and rebuilt. |
| **Transaction** | A group of database writes that either all happen or none happen. |
| **Idempotency key** | A label the client puts on a request so that sending it twice has the effect of sending it once. |
| **record_version** | A counter on each record, increased on every change. Used to detect stale writes. |
| **Digest** | A content fingerprint: `sha256:` plus 64 hex characters. Same bytes give the same digest. |
| **Grant** | Approved permission for FORGE to do something on a project, stored outside the repository. |

## How one command flows

```
untrusted JSON
   │ 1. check schema_version, then shape (forge/contracts/commands.py)
   │ 2. compute request digest (outside the lock)
   ▼
BEGIN IMMEDIATE  ── take the single write lock
   │ 4. same idempotency key seen?  same digest → return stored receipt
   │                                different   → reject
   │ 5. handler checks meaning: records exist, versions current, actor allowed
   │ 6. append events + update projections
   │ 8. store the receipt
COMMIT  (any error before this → ROLLBACK, nothing kept)
   │ 9. notify subscribers
   ▼
CommandReceipt
```

The numbers match the blueprint's command transaction (Part 3, section 3).
Step 7 (the outbox for side effects) arrives with ticket F07.

## Run it

From the repository folder, after installing (see README):

```bash
.venv/bin/forge-dev demo --root forge-data
```

**Before running, predict:** in step 5 two clients both saw the task at
`record_version` 1. Client A changes it first. What should happen to client
B, and what status should the task end with? Write your answer down, then
run the demo and compare.

Then look at the raw rows:

```bash
.venv/bin/forge-dev inspect --root forge-data
```

You will see four **events** and a smaller set of **projection** rows. The
task appears once in `tasks` with status `blocked`, while the events show its
whole history: created as `queued`, then changed to `blocked`.

You can also open the database with the `sqlite3` program, if installed:

```bash
sqlite3 forge-data/project/state.db "SELECT event_seq, event_type FROM events"
```

Expected output: four lines, numbered 1 to 4.

## The event/projection relationship

The `events` table is the history. The `tasks`, `records`, `projects`,
`decisions`, and `task_dependencies` tables are views of the current state,
computed by replaying that history (`forge/record/projections.py`).

The suite proves this with `test_replaying_events_rebuilds_identical_projections`:
it deletes every projection row, replays all events, and checks that the
result is identical. `test_tampered_projection_is_detected_and_repaired`
edits a projection directly, shows that `verify_projections()` detects the
edit, and repairs it by rebuilding.

Two consequences:

1. Events cannot be edited or deleted. Database triggers refuse it
   (`0001_core.sql`). This protects against bugs and accidents. It does not
   stop someone who can edit the database file directly. That threat needs
   file permissions and digests, which come in later tickets.
2. History tables never use foreign keys that point into projection tables.
   A rebuild deletes the projections, so such a key would break.

## Security notes

| Protection | Threat | Holds under these assumptions |
|---|---|---|
| Strict schemas (`extra="forbid"`, no type coercion) | A payload smuggling `"approved": true` or `"5"` for `5` | Every command goes through `parse_command` |
| Control-character rejection | Terminal escape or log-injection text in titles | Text fields use the `Text()` type |
| `BEGIN IMMEDIATE` + single-writer lock | Lost updates; two controllers on one project | All writers use `ProjectStore` on a local filesystem |
| Idempotency keys scoped per actor | Duplicate effects; one client reading another's stored response | Actor identity comes from authentication, never from the payload |
| Grants in a separate user-level database, approved only by a trusted human | A hostile repository granting itself permissions | The trust database is not writable by project code (enforced by isolation in F04) |
| Decisions bound to a proposal digest | An approval being reused for different content | The client sends the digest of what the person actually saw |

What is **not** protected yet: someone with direct write access to the
database files, network clients (there is no network API yet), and anything
involving executing code.

## Break it (safe, local only)

Each challenge has a test that should fail. Try it, then restore the code
(`git checkout -- <file>`).

1. In `forge/controller/handlers.py`, make `_require_version` do nothing.
   Which test fails, and what real-world bug does it represent?
2. In `forge/controller/service.py`, skip the stored-receipt lookup. Run the
   demo. How many tasks exist after step 4?
3. Write a `grants.json` that includes `"status": "approved"` and load it with
   `load_repository_grant_proposals`. What error do you get, and why is an
   error better than silently ignoring the field?
4. Unusual angle: send a `task.create` whose title is 1,000,000 characters.
   Where is it stopped? Is the error message itself bounded?

## Build challenge (to show you understand it yourself)

Add a `criterion.revise` command that creates revision 2 of a criterion.
Requirements:

- It takes `criterion_id`, the new `subject`, and `expected_record_version`.
- It appends a `criterion.revised` event, and a projector applies it.
- A task created earlier that cites revision 1 must still say revision 1.
- A new `task.create` citing revision 1 must now fail with `stale_version`.
- Rebuilding projections still matches the live tables.

Write the failing test first, then the code. You can ask for hints, but try
to explain each line you write.

## Evolve challenge

The receipts table grows forever. Design (in writing, no code) a retention
rule that keeps duplicate protection for retries while bounding growth. What
evidence would you need before deleting a receipt? (Hint: the blueprint says
garbage collection must never silently destroy reproducibility.)

## Career relevance

This session covers patterns used in payment systems, cloud control planes,
and the APIs of AI labs: idempotent commands, optimistic concurrency, event
sourcing, append-only audit logs, and trust boundaries. Interviewers for
backend, platform, and forward-deployed roles ask about them directly. They
are also the evidence a security buyer looks for: approvals that a model
cannot fake, and a history that cannot be quietly rewritten.

## Next session (F03)

Artifact storage and workspace snapshots: make two candidate snapshots from
one Git commit, show that their identities differ, then change a live file
and show the frozen snapshot is unchanged.

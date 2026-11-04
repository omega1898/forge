# Session 2: a whole skill replaced by code

Ticket **D01**. This session builds the first piece of the skill-distillation
plan (`docs/design/skill-distillation.md`): the "standup" skill, which a
frontier assistant performs by reading your tools and writing a summary,
done here entirely by code reading FORGE's records.

## The problem

A model asked "what's the status?" writes plausible text. It can omit the
one blocked task, invent a reason, or summarize yesterday's state. A small
local model does this more often. But every fact a standup needs (what
finished, what is running, what is stuck and why, what needs a decision)
already exists in FORGE's records. So the right design uses no model at all.

## What was built

| File | Purpose |
|---|---|
| `forge/reports/summary.py` | `build_summary()`: a pure function from records to a typed `ProjectSummary` |
| `forge/reports/standup.py` | `render_standup()`: Markdown in the standup layout, with untrusted text escaped |
| `forge/record/store.py` | `RecordReader` (reads that work on any copy of the database) and `ProjectStore.replayed()` (a scratch copy rebuilt up to any event) |
| `forge/cli.py` | `forge-dev summary` with `--since`, `--as-of` and `--json` |
| `tests/test_summary.py` | acceptance and hostile-input tests |

New words:

| Term | Meaning here |
|---|---|
| **Pure function** | Output depends only on its inputs. Same records and same time in, same summary out. |
| **Computed readiness** | "Ready" is never stored. It is worked out each time: a queued task whose dependencies are all completed. |
| **Window** | The events after `--since` and up to `--as-of`. "Done" lists only changes in the window; blockers are always current. |
| **Time travel** | Rebuilding the project as it stood at an earlier event by replaying the log up to that point. |
| **Escaping** | Changing characters in untrusted text so a renderer shows them literally instead of acting on them. |

## Run it

```bash
.venv/bin/forge-dev demo --root forge-data
```

The demo now ends with a standup. Then:

```bash
.venv/bin/forge-dev summary --root forge-data --as-of 3
```

**Predict before running:** the demo's task was blocked at event 4. At event
3, which section of the standup should the task appear in, and why not
"Blockers"? Then run it and check.

```bash
.venv/bin/forge-dev summary --root forge-data --json
```

The JSON form is what the future editor extension and other tools will
read; its shape is published in `forge/contracts/schemas/report.project_summary.json`.

## How it works

1. Choose the point in time (`as_of`). If it is the latest event, read the
   live tables. Otherwise build a scratch in-memory database by replaying
   events up to `as_of`, using the same projectors as the live database.
2. Walk the `task.status_changed` events once. Remember each task's most
   recent change (its reason explains the current status) and collect the
   changes inside the window (completed, failed, cancelled).
3. Read current tasks and compute readiness from dependencies.
4. Read pending decisions; mark a decision expired by comparing its expiry
   with the evaluation time passed in, not with a hidden clock.
5. Cap every list (default 50) and record the true total, so a long list
   says "…and 12 more" instead of silently dropping items.

The test `test_replay_gives_the_identical_summary` builds the summary both
ways (live tables and full replay) and requires byte-identical JSON. That is
the proof that the summary is a function of the event log.

## Security notes

Task titles, reasons and questions are text from people or, later, models.
A title such as `[click](javascript:...)` or `<img src=x onerror=...>` would
become a live link or HTML inside a Markdown report. `escape()`:

- flattens newlines, so a title cannot start a fake heading or bullet;
- turns `<`, `>` and `&` into HTML entities, which every renderer shows
  literally (a backslash escape only works in renderers that follow the
  CommonMark standard; the first version used one and a test caught it);
- backslash-escapes link, emphasis, code and table characters;
- truncates to 160 characters.

Secure against Markdown and HTML injection through record text, under the
assumption that the report is shown by a Markdown or HTML renderer and
inserted only through `render_standup`. Not covered: terminal escape
sequences (already rejected at input by F01's control-character check).

## Break it (safe, local only)

1. In `summary.py`, change `latest_change[...] = event` to
   `latest_change.setdefault(...)`. Which test fails, and what would a user
   see wrong in their standup?
2. Remove the line that replaces `<` with `&lt;` in `standup.py`. Run the
   tests. Then think: why is a backslash before `<` not enough everywhere?
3. Unusual angle: what happens to the summary if two tasks depend on each
   other? (Hint: try to create that situation through commands first. Which
   earlier ticket stops you, and why does that matter for this one?)

## Build challenge

Add a `stale` section: tasks that have been `running` for longer than a
threshold you pass in (for example 2 hours), measured from their last
`task.status_changed` event to the evaluation time. Requirements:

- the threshold is an input, not a constant hidden in the code;
- the result is identical between the live and replay paths;
- a task that left `running` and came back is measured from the latest
  change, not the first.

Write the failing test first.

## Evolve challenge

The replay path rebuilds the whole project for every past-time query. For
10 events that is instant; for 10 million it is not. Design (in writing)
checkpoints: saved projection copies at known event numbers. What must a
checkpoint record so it can never silently disagree with the log?

## Career relevance

"Derive reports from an authoritative log instead of asking a model to
recall" is the same pattern as audit trails in finance and observability
pipelines in operations. It is also the core argument for FORGE's
commercial value: a small model cannot misstate a status it never writes.
For portfolio evidence, the replay-equivalence test and the
injection tests are concrete artifacts you can show and explain.

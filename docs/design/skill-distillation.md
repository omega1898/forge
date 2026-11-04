# Skill distillation: turning assistant skills into FORGE code

Status: design analysis, 4 November 2026. Not implemented. Feeds tickets F05–F24.

## 1. The question

Frontier coding assistants get much of their engineering behavior from
*skills*: Markdown procedures (review checklists, debugging steps, planning
templates) that a large model reads and follows with its own judgment.
FORGE runs offline on small local models, which follow long procedures
unreliably. The question is which parts of each skill can become
deterministic FORGE code, which parts need a narrow model call that code
frames and checks, and which parts must stay human decisions, so that the
local model is a boost rather than the engine.

## 2. Method and sources

Every skill was decomposed into ordered steps, explicit rules (checklists,
thresholds, templates, scoring formulas), output structure, and the
verification it demands. Each step was classified:

| Class | Meaning | Who does it in FORGE |
|---|---|---|
| **D** | Deterministic: parsing, counting, ordering, arithmetic, running a tool and reading its exit status, matching against a fixed list | Code |
| **S** | Slot: needs language generation or fuzzy judgment, but code can frame it with a schema, enumerated options and a checker that can reject the output | Small model inside a validated contract |
| **O** | Open judgment needing broad reasoning or world knowledge | Larger model if available, else a human, with evidence attached |
| **H** | A decision that belongs to a person | Decision card |

Sources read in full (66 skills examined, 39 analyzed in depth):

| Set | Skills | Where |
|---|---|---|
| Anthropic Engineering plugin | architecture, code-review, debug, deploy-checklist, documentation, incident-response, standup, system-design, tech-debt, testing-strategy | public repo `anthropics/knowledge-work-plugins` |
| Anthropic Product Management plugin | write-spec, sprint-planning, roadmap-update, metrics-review, stakeholder-update, synthesize-research, competitive-brief, product-brainstorming | same repo |
| Verification skills (this account) | phase-gate, determinism-check, balance-matrix | synced user skills |
| Assurance skills (finance plugin) | sox-testing, audit-support, close-management, reconciliation, variance-analysis | studied for transferable control-testing mechanisms |
| General skills | skill-creator, deep-research, learn, doc-coauthoring, mcp-builder, file-reading, pdf-reading, product-self-knowledge, frontend-design, artifact-emulator, session-start-hook | local skill library |

Not readable, analyzed from their published descriptions only: the built-in
Claude Code skills (code-review with effort levels, security-review,
simplify, run, init, hooks/update-config, workflow orchestration), and
community plugins without public source (finecomb audit checklist,
six-specialist review council, Senior Engineering Partner, n360 TDD
standard, mana ticket workflow). The 27 remaining library skills are consumer
tasks (groceries, prescriptions, expenses) or visual-art generators and were
excluded as unrelated to software engineering.

## 3. Finding: what a skill actually is

Every engineering skill read decomposes into the same six parts:

| Part | Example (Anthropic `code-review`) | Typical class |
|---|---|---|
| Trigger | "review this before I merge" | S (intent recognition) |
| Procedure | dimensions: security, performance, correctness, maintainability | D (a fixed sequence) |
| Checklist / rubric | "SQL injection, XSS, CSRF … N+1 queries … off-by-one" | D *if* each item has an analyzer, otherwise S/O |
| Output template | table of File / Line / Issue / Severity, verdict | D (renderer) |
| Verification | mostly absent ("Approve / Request Changes") | D when bound to evidence |
| Judgment | deciding whether line 42 really is an injection | S/O |

The procedure, checklist, template and verification together are the
majority of each skill's text, and all four are representable as data and
code. The frontier model's real contribution is in two places:

1. **Applying a checklist item to specific code** ("is this query built from
   user input?"). Many items have existing deterministic analyzers; the rest
   become narrow questions with cited evidence.
2. **Synthesis** (writing a fix, an ADR option, a postmortem narrative). This
   becomes generate-then-verify: the model proposes, code checks.

The skills themselves are weak on verification. Anthropic's `code-review`
ends with a verdict but never requires a failing test that demonstrates a
finding; `debug` says "suggest tests to prevent regression" but never
requires the reproduction to fail before the fix and pass after. The
verification-first skills (`phase-gate`, `determinism-check`,
`balance-matrix`) are the exception and are the best templates for FORGE,
because they already say "never report PASS on a check you did not run".
FORGE's blueprint encodes the same rule (assessment results `pass`, `fail`,
`not_run`, `error` are distinct, and approval cannot convert a failure).

**Inference:** a small model inside strong verifiers can match a frontier
model on tasks where correctness is checkable (tests, analyzers, schemas,
reproductions), and will remain weaker on open design judgment. FORGE should
spend its engineering effort on making more of the work checkable.

## 4. The distillation architecture

Eight code constructs replace "a model reading a skill". Each sits inside an
existing FORGE module boundary from the blueprint.

### 4.1 Capability = versioned workflow template (`forge/capabilities/`)

A skill's procedure becomes a function that emits a FORGE task graph
(`prepare` → `execute` → `assess` → `decide`). The engine already validates
graphs, rejects cycles, and tracks status. A capability never runs steps
itself; it proposes tasks the controller admits.

### 4.2 Project facts (`forge/knowledge/facts.py`)

Skills customize themselves from conversation ("we use feature flags → add
flag checks"). FORGE derives these facts deterministically from the
snapshot: languages, build system, test framework, presence of migrations,
feature-flag libraries, CI files, lockfiles, entry points. Facts are records
with evidence (which file proved it), so checklist selection is explainable
and reproducible.

### 4.3 Rule packs (`forge/packs/<pack>/rules/`)

Every checklist item becomes a versioned rule record:

```
rule_id, revision, dimension (security|performance|correctness|...),
applies_when   -> predicate over project facts and changed files   (D)
method         -> analyzer | probe | model_slot | human
evidence       -> what observation proves or refutes it
severity_map   -> fixed table from observation to finding severity (D)
references     -> stable public identifiers (CWE, OWASP category)
```

Rules are data, reviewed and versioned like code. A model can propose a new
rule; it becomes active only through a decision.

### 4.4 Analyzer adapters (`forge/assessment/analyzers/`)

Checklist items with existing offline tools run those tools inside the
execution boundary and normalize output into evidence. SARIF is the
interchange format to normalize toward (a static-analysis results standard
from OASIS; its standardization status is recalled, not re-verified here
because the OASIS site was unreachable from this session). Candidate
tools per pack are listed in section 5; each needs admission (pinned
version, offline data, sandbox) before use.

### 4.5 Probes (`forge/assessment/probes/`)

Dynamic checks generalized from the verification skills: run twice and
compare digests (determinism), fixed-seed measurement matrices with
threshold rules (balance-matrix), delta-debugging of failing inputs,
sanitizer runs, mutation checks that prove a test can fail.

### 4.6 Model slots (`forge/gateway/slots/`)

Each S-class step becomes a typed contract:

```
slot_id, revision
input_schema        context the controller assembles (cited spans only)
output_schema       small, flat JSON; enumerations wherever possible
validators          code that can reject the output (citations exist,
                    quoted text matches the snapshot byte-for-byte,
                    numbers equal recorded values, patch applies)
attempts, budget    bounded retries; equivalent retries -> no_progress
fallback            deterministic default, larger model, or decision card
```

Verified constraint (llama.cpp grammar documentation): llama.cpp can
constrain output to a grammar and converts a *subset* of JSON Schema to
grammars, but "unsupported features are skipped silently", nested `$ref` is
broken, `uniqueItems`, `not` and conditionals are unsupported, and numeric
bounds apply only to integers. Consequences for FORGE: slot output schemas
must be flat (no `$defs`), use `^…$` patterns and enumerations, and the
controller must re-validate every response with the full Pydantic contract,
because the constraint layer can silently drop rules.

### 4.7 Generate-and-verify loops (`forge/engine/` + `forge/assessment/`)

For synthesis (patches, tests, hypotheses), the model produces candidates
and code selects by evidence, never by model confidence: a patch candidate
is accepted only if the reproduction now passes and the regression suite
still passes; a generated test is admitted only if it fails on the buggy
snapshot (or on a mutant) and passes on the fixed one. Candidates live in
separate workspaces (blueprint F12).

### 4.8 Renderers (`forge/reports/`)

Every output template becomes a renderer over records. The model may fill
bounded free-text fields (a summary sentence), and a fact-lock check rejects
prose whose numbers, identifiers or statuses differ from the records it
cites. A standup or postmortem is therefore never "written" from memory; it
is a view of the event log with optional wording.

### 4.9 Router (`forge/api/` intake)

Triggers become explicit commands plus an intake form. Free text is mapped
to an enumerated capability list by a small classifier slot whose output the
user confirms; a wrong guess costs one click, not a wrong workflow.

## 5. Skill-by-skill distillation

Notation: **Code** = deterministic core FORGE implements; **Slots** = narrow
model calls; **Human** = decision cards; **Home** = FORGE module and ticket.

### 5.1 Engineering skills (Anthropic Engineering plugin)

**code-review.** Checklist of ~25 items across security, performance,
correctness and maintainability, then a findings table and verdict.
- *Code:* diff to changed hunks; facts select rule packs; one analyzer per
  checklist item where one exists (injection and path traversal as taint
  rules; secrets as pattern-plus-entropy scan; N+1 as "query call inside a
  loop" AST rule; complexity metrics; unbounded loops and queries; bare or
  swallowed exceptions; type checking; token-clone duplication;
  changed-line test coverage). Severity from a fixed table. Findings
  deduplicated by rule, location and fingerprint. Verdict is a function:
  any unwaived critical finding means *request changes*.
- *Slots:* triage one candidate finding with an enumerated answer
  (`confirmed`, `false_positive`, `needs_test`) citing exact lines; explain a
  finding at the user's explanation depth; propose a fix patch.
- *Upgrade over the skill:* a `confirmed` correctness or security finding at
  high severity needs a demonstrating test or reproducer; otherwise it stays
  `suspected`. Verification is by execution, not by a second model's opinion.
- *Human:* approve, request changes, or waive with scope.
- *Home:* `forge/capabilities/review.py`; analyzers in F05; Python rules F11;
  C/C++ rules F16; fix proposals F12.

**debug.** Reproduce, isolate, diagnose, fix.
- *Code:* a reproduction harness records exact command, input and
  environment against a snapshot and captures a normalized failure signature
  (exception type, top frames, message with volatile parts removed); stack
  trace parsing mapped onto the code index; diff from last-known-good
  snapshot; bisection across snapshot history; delta debugging to shrink a
  failing input; log search by time window and signature. Fix acceptance:
  reproduction fails before, passes after, regression suite still passes.
- *Slots:* hypotheses as a short list, each with a falsifiable prediction and
  a probe chosen from a fixed menu (run with input, add an assertion, inspect
  a value); root-cause narrative, fact-locked; fix patch candidates.
- *Human:* when no hypothesis survives within budget, the task becomes
  `blocked` with the experiment record attached.
- *Home:* bug-fix workflow (F11); blueprint `Experiment` records; repair
  budget and `no_progress` (blueprint Part 2 step 10).

**testing-strategy.** Testing pyramid, strategy by component type, what to
cover and skip.
- *Code:* classify components from facts and the code index; the skill's
  "strategy by component type" table becomes a rule table; changed-line
  coverage gaps; boundary values generated from types and the specification
  (empty, maximum, one past maximum); property-based tests where invariants
  are declared; mutation testing to measure whether tests can fail.
- *Slots:* draft example test cases from acceptance criteria. A generated
  test is admitted only if it runs and fails on a mutant or the known-bad
  snapshot.
- *Home:* test capability (blueprint Part 2 step 4); F05; F11.

**architecture and system-design.** ADR template, options compared on fixed
dimensions, trade-offs, consequences.
- *Code:* ADR as a decision record (statuses map to disposition plus a
  `supersedes` link); completeness checks (at least two options, every
  option rated on the same dimensions, constraints referenced, consequences
  present); load-estimation calculator (rate × payload, storage growth);
  retrieval of prior ADRs.
- *Slots:* draft options and pros and cons inside the enforced structure.
  Trade-off reasoning for novel systems is **O**.
- *Human:* the decision. Disputed factual claims become experiments
  (prototype and measure) rather than arguments.
- *Home:* decision records (F02, done); design task kind; F06.

**tech-debt.** Six categories, priority = (Impact + Risk) × (6 − Effort).
Almost entirely deterministic.
- *Code:* detectors per category: duplication, magic numbers and complexity
  (code debt); coverage and flaky tests found by fixed-seed reruns (test
  debt); lockfile parsing against an offline vulnerability database and
  version age (dependency debt; `osv-scanner --offline` is verified to work
  against a downloaded database); missing README sections, docstring
  coverage, docs naming deleted symbols (documentation debt); missing CI,
  missing lockfile, manual deploy scripts (infrastructure debt). The
  priority formula is code.
- *Slots:* one-sentence business justification, fact-locked.
- *Human:* impact and risk scores that cannot be derived; scheduling.
- *Home:* `forge/capabilities/debt.py`; `Finding` records; F10/F11.

**deploy-checklist.** Pre/deploy/post checklist, customized by
circumstances, rollback triggers.
- *Code:* items generated from facts (migrations present, feature-flag
  library present, breaking interface change detected by contract diff);
  each item bound to evidence ("tests passing" is the current assessment set
  for the release snapshot; "code reviewed" is an approved decision with a
  matching digest); rollback triggers stored as numeric criteria.
- *Slots:* none required; release-note wording optional and fact-locked.
- *Human:* go or no-go.
- *Limit:* production monitoring steps need an operations connection that an
  offline controller does not have; FORGE records them as `not_run`.
- *Home:* release capability (F11 packaging, F24).

**incident-response.** Severity table, roles, status updates, blameless
postmortem with timeline and five whys.
- *Code:* severity from structured impact fields; timeline as a filtered
  event log (FORGE's log is a timeline by construction); five whys as a
  chain where each link must cite evidence; action items as tasks with owner
  and due date; status update and postmortem renderers.
- *Slots:* summary wording, fact-locked; candidate causes.
- *Human:* severity confirmation, roles, external communication approval.
- *Home:* operations workflows (F24), using logs supplied as artifacts.

**documentation.** Templates for README, API reference, runbook,
architecture doc, onboarding; "keep it current".
- *Code:* required-section checker per document type; API reference
  generated from the code index and FORGE's exported JSON Schemas; a
  freshness checker for documents that name missing symbols, files or
  commands (this turns "keep it current" into a test); local link checker;
  "quick start under five minutes" becomes a probe that runs the documented
  commands in the sandbox and times them.
- *Slots:* section prose, fact-locked to the code index.
- *Home:* knowledge (F10); documentation capability.

**standup.** Yesterday, today, blockers.
- *Code:* entirely deterministic from the event log: tasks completed since a
  time, tasks running, tasks blocked with their recorded reasons, decisions
  pending. This is the blueprint's `project.summary`.
- *Slots:* optional wording.
- *Home:* F08; buildable now on the F02 record.

### 5.2 Verification skills

These three already behave like FORGE and confirm the blueprint's
assessment design.

**phase-gate.** Copy "done when" text verbatim; run each check; record PASS,
FAIL or NOT CHECKED with evidence; never reword a check so it passes; the
owner approves before the next phase starts.
- *Maps to:* criterion records (verbatim, revisioned); assessment results
  `pass` / `fail` / `not_run`; evidence admissibility ("never report PASS on a
  check you did not personally run" = only evidence from a job or attributed
  review in this record counts); the five-line report = renderer; owner
  approval = decision; "append to DECISIONS.md" = the event log. All
  deterministic. Home: completion evaluator (F05/F06).

**determinism-check.** Grep for banned non-deterministic calls; run twice and
diff hashes; run across engines; bisect to the first divergent step; never
relax the comparison.
- *Maps to:* a reusable determinism probe: per-language banned-call rules,
  repeat-run digest comparison, cross-host comparison (F17), trace diff to
  the first divergence. "Never relax the comparison" is the blueprint rule
  that a candidate cannot alter evaluator configuration (F05). All
  deterministic.

**balance-matrix.** Fixed seeds, a full matrix, threshold rules (above 70%
dominant, below 30% useless, timeouts above 25%), change one constant per
iteration, stop after five iterations and report.
- *Maps to:* the performance and tuning workflow (F18): measurement
  harness with fixed seeds; thresholds as criteria; "one change per
  iteration" enforced by the proposal validator (the patch may touch exactly
  one declared parameter); "stop after five" is the repair budget with
  `no_progress`. All deterministic except choosing which constant to change
  (**S**, from a declared list of tunable parameters).

### 5.3 Product and project management skills

<!-- PM -->

### 5.4 Assurance skills (transferred from finance)

<!-- ASSURANCE -->

### 5.5 General skills

<!-- GENERAL -->

### 5.6 Assistant harness behaviors

The assistant this analysis was written with runs inside a harness whose
mechanisms are themselves distillable. Analyzed from their descriptions:

| Harness behavior | FORGE equivalent | Class |
|---|---|---|
| Permission modes and command allowlists | trusted grants (F02, done) | D |
| Hooks that run before or after tool calls | controller policy checks on every command | D |
| Plan mode (propose, then approve) | `plan.apply` with `validate_plan()` | D + H |
| Todo tracking | the task engine | D |
| Multi-agent review: dimensions in parallel, then adversarial verification of each finding | parallel rule packs; finding confirmation by executed reproducer | D + S |
| Review effort levels (few high-confidence findings vs many) | rule confidence tiers and report thresholds | D |
| `simplify` (reuse, duplication, efficiency) | clone detection; "existing helper" lookup in the code index | D + S |
| `security-review` | security rule pack plus threat-model template | D + S/O |
| `run` (launch the app per project type) | facts-driven run recipes (CLI, server, library, TUI) inside the sandbox | D |
| `init` (write a codebase guide) | project facts plus code-index summary, fact-locked prose | D + S |
| `fewer-permission-prompts` (mine past commands, propose an allowlist) | grant-proposal miner over the event log; human approves | D + H |
| Session start hooks | environment provisioning and admission checks | D |
| Git worktrees for parallel attempts | isolated candidate workspaces (F12) | D |
| Context compaction | context assembler with manifests and recorded omissions (F10) | D |

## 6. Cross-cutting mechanisms

<!-- CROSSCUT -->

## 7. Limits: what small models will still do badly

<!-- LIMITS -->

## 8. Build plan

<!-- PLAN -->

## 9. Evidence ledger

<!-- EVIDENCE -->

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

Sources read in full (37 skills analyzed in depth, step by step):

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
standard, mana ticket workflow). The rest of the local library was screened
and excluded as unrelated to software engineering: consumer errands
(groceries, prescriptions, expenses), visual-art generators, office-format
converters, and financial-market data skills.

Three readers extracted the product-management, general and assurance sets
in parallel using the same fixed format; the engineering and verification
sets were read directly. Every defect claimed by a reader and repeated below
was checked against the source file (section 9). Step classification is a
judgment: another reader could move individual steps between D and S, so the
tallies indicate proportions, not exact counts.

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
because the OASIS site was unreachable from this session). Section 5 names
the checks each skill needs; the specific tool for each check is chosen
when its language pack is admitted (pinned version, licence, offline data,
sandbox), not assumed here.

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

Across 133 procedure steps in eight skills: 41 D, 52 S, 14 O, 26 H.
Product work is more language-heavy than engineering work, but most of its
S steps are tightly boundable, because the skills already state count limits
("Goals 3–5"), length limits ("problem statement 2–3 sentences"), sentence
templates (user story, Given/When/Then, "Moved to Yellow because …") and
banned words (acceptance criteria may not say "fast", "user-friendly",
"intuitive"). Each of those is a cheap deterministic validator on model
output.

| Skill | D/S/O/H | Code core | Slots | Human |
|---|---|---|---|---|
| sprint-planning | 6/2/0/5 | capacity arithmetic (plan to 70–80% load), dependency ordering, priority tiers, fit check | sprint goal sentence | scope, commitments, estimates |
| roadmap-update | 10/3/0/6 | status roll-up from tasks, RICE (R×I×C/E) and ICE scores, value×effort quadrant, dependency contingency list | change narrative | priorities, dates, cuts |
| metrics-review | 10/5/1/3 | change vs previous and target, threshold status, OKR grading bands (0.0–0.3 / 0.4–0.6 / 0.7–1.0), leading/lagging tagging | summary sentences | targets; causal attribution is **O** |
| stakeholder-update | 3/8/0/4 | status colour from rules, audience template, length check, fact-lock to records | audience prose | asks, external sends |
| write-spec | 1/11/2/4 | required sections, goal and non-goal counts, story and Given/When/Then templates, banned vague words | requirements and criteria drafts | scope, success targets |
| synthesize-research | 8/9/3/2 | frequency counts, frequency×impact quadrant, quote verbatim check against transcripts, participant-name redaction | finding statements, themes | which findings matter |
| competitive-brief | 2/6/5/2 | feature matrix layout, rating enum | positioning text | strategy; market facts are **O** |
| product-brainstorming | 1/8/3/0 | idea counts, "how might we" template, parking list | idea generation (**O** for diversity) | direction |

Mapping to FORGE: a spec is a set of requirement and criterion records
(F02, done); a sprint is a filtered, capacity-checked slice of the task
graph (F06); a roadmap and a stakeholder update are renderers over task,
decision and metric records (F08). The skills hand off to each other
(brainstorm → spec → sprint backlog; research → roadmap → update) but none
defines the hand-off format; in FORGE the hand-off is the shared record, so
it is defined by construction.

Defects found and confirmed in the sources: the stakeholder-update skill
sets an executive limit of 300 words in one section and 200 words in its
"Executive / Leadership Update" section; P0/P1/P2 mean different things in
write-spec (P2 = out of scope) and sprint-planning (P2 = stretch); six
different status vocabularies appear across the set; metrics-review never
states that lower is better for metrics such as churn. FORGE needs one
canonical enumeration per concept, with explicit mappings to external
vocabularies and a declared direction for every metric.

Offline limit: competitive and market facts cannot be known offline. FORGE
must accept them only as supplied, cited source artifacts, never as model
recall.

### 5.4 Assurance skills (transferred from finance)

These finance skills were studied because auditors solved "how do you prove
a control worked" decades ago, and FORGE's evidence model faces the same
question. Of 79 procedure steps across the five skills, the extraction
classified 41 as D, 20 as S, 7 as O and 11 as H. Every human step is a
sign-off, a risk rating, a severity call or an escalation; every open step
is remediation design or root cause.

**sox-testing and audit-support → testing whether an engineering control
operated.** "Every merged change had a non-author approval" and "every
deploy passed the gate" are controls. Transferable code: seeded random or
systematic sample selection (interval = population ÷ sample size, random
start) with the seed recorded; population completeness checked before
sampling (the blueprint's dataset identity); per-sample pass/fail with an
exception log; evidence rejected unless dated, attributed and sourced (the
blueprint's evidence origin rules); "test an automated gate once, then
prove its configuration did not change" (evaluator configuration digests);
deficiencies aggregated by component. *Does not transfer:* financial
materiality, and the audit sample-size tables, which are convention rather
than statistics and carry no confidence guarantee outside audit.
*Home:* F05 assessment, F07 jobs; a later "process assurance" capability
that audits FORGE's own records.

**close-management → release and project tracking.** A leveled dependency
map is a task graph in topological layers; the critical path is the same
concept; "complete means finished and reviewed" is a definition-of-done
gate; soft close, hard close and period lock map to code freeze, release cut
and tag; "adjustments after soft close" map to changes after freeze, and
"restatements" to escaped defects. All D, and the FORGE engine already
holds the graph. The extraction found the skill's own plan has
dependencies inside a level that it never declares, which FORGE's explicit
edges and cycle check would refuse. *Home:* engine (F06), `project.summary`
(F08).

**reconciliation → migration and output comparison (F20).** Compare control
totals, then match records by key; sort every unmatched item into one of
three buckets (timing: expected to clear; known defect: needs correction;
unexplained: investigate); age open items and escalate by age; refuse to
close with any unexplained difference; flag a recurring difference type as
a systemic defect; the reconciler must not be the person who produced the
output. All D except bucket assignment for ambiguous items (S, with the
evidence attached). This is close to the blueprint's migration
"comparison contract with accepted deltas".

**variance-analysis → performance regression analysis (F18).** Split a
total change into drivers (per-request cost × request count; traffic mix
shifting between endpoints) in a bridge whose drivers must sum to the total;
thresholds per comparison type, triggered when either the absolute or the
percentage limit is exceeded; a fixed investigation order. *Does not
transfer:* additive decomposition for percentiles or ratios; finance
threshold percentages, which are not engineering noise floors.

**The arithmetic lesson.** The skill's three-way price/volume/mix formulas
leave out the interaction term (Δvolume × Δprice). On the skill's own
example (10,000 units at $50 budget, 11,000 at $48 actual) they sum to
+$30,000 against an actual variance of +$28,000, so the skill's own rule
"start + drivers = end" fails. This was reproduced independently in Python
during this analysis. A frontier model following this skill would likely
repeat the error with confidence; a FORGE implementation would carry the
bridge invariant as a property test and fail on the first example. The
extraction also found the five skills disagree on vocabularies (four versus
five conclusion values, two different risk scales, overlapping day-90 aging
buckets). Encoding rules as versioned, tested data catches both kinds of
defect; prose instructions catch neither.

### 5.5 General skills

**skill-creator → FORGE's evaluation harness.** This is the most important
skill for the "compete with frontier" goal, because it is the only one that
measures. Its method: realistic test cases; assertions graded pass or fail
with quoted evidence and "burden of proof on the expectation"; runs with
and without the skill to measure the delta; repeated runs to estimate
variance; a 60/40 held-out split (seeded, 42) for tuning trigger
descriptions, with the best version chosen on the held-out part; explicit
stop conditions. Its gaps, which FORGE must close: two or three cases with
one run each cannot support a variance claim; there is no significance
test; the grader is the same model family with no agreement check. FORGE
version: deterministic assertions first (tests, schema checks, analyzer
results), model grading only for what cannot be checked, larger frozen
held-out sets per capability, confidence intervals, and a recorded
baseline. This matches blueprint decision 11 ("frontier-level capability is
a task-specific hypothesis measured against a recorded baseline") and the
rule to freeze a held-out set before tuning prompts.

**mcp-builder → rules for the tools FORGE exposes to a model.** Small
models misuse tools more than large ones, so its tool-design rules matter
more here: `{service}_{action}_{resource}` naming; descriptions that say
when to use a sibling tool instead; paginated results with 20–50 item
defaults and `has_more` / `next_offset`; a hard response-size cap met by
returning fewer items; errors that tell the caller how to recover;
read-only versus destructive annotations. Its evaluation (ten read-only,
multi-step questions scored by exact string match) is a good template for
a FORGE tool-use regression set. Home: model gateway (F09), API (F08).

**learn → the teacher module (F15).** Its diagnosis step chooses among
enumerated cases (confusion about concept, procedure, notation, or the
question itself; impatient versus stuck), and its turn rule is "one focused
question and one small scaffold". In FORGE: diagnosis is an S slot with an
enumerated answer; "exactly one question" and "the hint does not contain
the answer" are deterministic validators; mastery is judged by exercises
with hidden evaluators (tests the learner's code must pass), not by a model
deciding whether an explanation "sounds right", which the skill leaves to
the same model. Building correct scaffolds for hard material stays **O**.

**doc-coauthoring and deep-research → documents with checkable structure.**
Fixed templates, bounded counts (3–5 sections, 5–10 questions), every
finding cited or moved to "gaps", and a *reader test*: a context-free
reader must answer questions from the document alone. FORGE can run the
reader test with a small model and score answers against the author's
stated answers. Offline, research is restricted to admitted local sources.

**file-reading and pdf-reading → the ingestion router (F10).** Almost
entirely deterministic: route by type, read size before content, bounded
previews, detect scanned PDFs before attempting text extraction, never
auto-extract archives, guard against path traversal in archive names.

**product-self-knowledge → capability claims come from records.** "Consult
the official source before stating a product fact" becomes a FORGE rule
already in the blueprint: whether a tool, model or host can do something is
answered from capability records and probes, never from model output.

**session-start-hook → environment provisioning.** Detect the package
manager from manifests, install idempotently, prove idempotency by running
twice and by changing a hash. The extraction found the skill contradicts
itself (its template enables asynchronous mode while its text says the hook
runs synchronously) — another case for executable checks over prose.

**artifact-emulator and frontend-design → output inspection.** Render the
produced interface, collect console errors and screenshots, and treat page
content as untrusted data fenced with a random marker. Design quality
itself is **O** and stays with a person.

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

The same mechanisms recur across all five sets. Each becomes one shared
FORGE primitive, built once and reused by every capability.

| # | Mechanism (where it recurs) | FORGE primitive | Status |
|---|---|---|---|
| 1 | Enumerated status and result vocabularies (all sets; often inconsistent) | one canonical enum per concept with a transition table and mappings to outside vocabularies | pattern exists (`engine/lifecycle.py`) |
| 2 | Evidence-backed claim: statement + source + confidence (PM, research, review) | `Finding` and assessment records; citation validator that checks quoted text against snapshot bytes | records planned F05 |
| 3 | Bounded counts, lengths, sentence templates, banned words (PM, docs, research) | slot output validators | D04 below |
| 4 | Thresholds where "either the absolute or the percentage limit" triggers (assurance, metrics, balance) | criterion parameters plus a threshold evaluator | F05 |
| 5 | Arithmetic tie-outs: drivers sum to the total, reconciliation difference is zero | property tests on every scoring and decomposition function | test convention |
| 6 | Seeded sampling and repeated runs (sox-testing, balance-matrix, skill-creator) | reproducible measurement probe recording seed, population digest and run count | F05/F18 |
| 7 | Segregation of duties: preparer ≠ reviewer; tester isolated from author context (assurance, doc-coauthoring, mcp-builder) | rule that the actor who produced a candidate cannot record its approving review; hidden evaluators | partly F02, F05 |
| 8 | Read only what is needed, within budgets (file-reading, mcp-builder, deep-research) | context assembler budgets; page defaults 50, cap 200 | page limits done F02; F10 |
| 9 | Dispatch tables keyed by facts (file-reading, deploy-checklist, testing-strategy) | project facts → capability and rule selection | D05 |
| 10 | Iterate until an explicit stop condition (balance-matrix, skill-creator, doc-coauthoring) | repair budget and `no_progress` | F06/F11 |
| 11 | Human checkpoints (every set) | decision cards bound to proposal digests | done F02 |
| 12 | Untrusted content fencing (artifact-emulator, deep-research, file-reading) | renderer escaping; retrieved text passed to models as marked data | F10, F14 |
| 13 | Held-out evaluation against a baseline (skill-creator only) | capability evaluation harness | D11 |
| 14 | Shared work item, risk, decision, dependency and metric shapes (PM, close-management) | shared record contracts | D01 |
| 15 | Degrade by available capability (general set) | `missing_capability`, `not_run`, `blocked` instead of guessing | done F02 |
| 16 | Explicit anti-pattern lists (learn, frontend-design, mcp-builder) | negative test cases in each capability's evaluation set | D11 |

## 7. Limits: what small models will still do badly

Distillation narrows the gap; it does not remove it. The O-class steps
cluster in the same places in every set:

- **Open design trade-offs** for systems without precedent (architecture,
  system design).
- **Causal attribution**: why a metric moved, why users behave a certain way
  (metrics-review, synthesize-research, incident root cause).
- **Facts about the outside world**: markets, competitors, current library
  behaviour. Offline, these are unknowable without supplied sources.
- **Diverse idea generation** (brainstorming) and **complete requirement
  sets** (write-spec).
- **Semantic reasoning across many files** in a large unfamiliar codebase,
  where the needed context exceeds what a small model can hold.
- **Correct teaching scaffolds** for hard material, and judging whether a
  learner's own explanation is right.

Mitigations, in order of preference: (1) convert to checkable work
(experiments, tests, reproductions); (2) decompose into S slots with
evidence attached; (3) generate several candidates and select by
verification; (4) route the slot to a larger local model when hardware
allows, recording which model answered; (5) a decision card for a person.
FORGE must never fill an O step by having a small model guess silently.

What this analysis does **not** establish: that FORGE with a small model
will match a frontier assistant on any task. That is a hypothesis per
capability, to be measured with the evaluation harness against a recorded
baseline. The inference that verifier-heavy designs close most of the gap
on checkable tasks is supported by the structure of the skills (most steps
are D or tightly boundable S), not by a measurement.

## 8. Build plan

The blueprint's 24-ticket order still governs. Distillation work attaches
to it as "D" tickets that ride on the F ticket that provides their
foundation. Each D ticket ships with failure tests, like every F ticket.

| ID | Depends on | Work | Acceptance result |
|---|---|---|---|
| D01 | F02 (done) | Shared record contracts for finding, risk and metric (with declared direction); `project.summary` renderer from the event log (the standup skill as pure code) | Summary for a seeded project lists completed, running, blocked-with-reason and pending decisions exactly; no model involved; replaying events gives the identical summary |
| D02 | F05 | Rule-pack and rule contracts; analyzer adapter interface normalizing to SARIF-shaped evidence; deterministic severity table and verdict function | An unwaived critical finding forces "request changes"; an analyzer crash yields `error`, not `pass`; duplicate findings collapse by fingerprint |
| D03 | F05 | Probe library: repeat-run digest comparison, fixed-seed measurement, mutation check that a test can fail | A deliberately non-deterministic fixture is caught; a test that cannot fail is rejected |
| D04 | F09 | Model-slot contract: flat output schemas within llama.cpp's supported subset, full re-validation, citation and fact-lock validators, count/length/template/banned-word validators, retry budget, fallback | A slot answer citing a nonexistent line, misquoting source, or stating a number different from the record is rejected; budget exhaustion yields a decision card, not a guess |
| D05 | F03, F10 | Project-fact detectors with evidence (languages, build, tests, migrations, flags, CI, lockfiles) | Each fact names the file that proves it; a project without migrations gets no migration checklist items |
| D06 | F11 | Capabilities as workflow templates: review, bug fix (debug), test strategy, tech debt | Each produces a valid task graph; the tech-debt priority formula and the review verdict are reproducible from records |
| D07 | F12 | Generate-and-verify loop: patches admitted only if reproduction fails before and passes after; generated tests admitted only if they fail on a mutant or known-bad snapshot | A plausible but wrong patch is rejected by evidence despite a confident model rationale |
| D08 | F15 | Teacher distillation from `learn`: enumerated diagnosis slot, one-question and hint-leak validators, mastery by hidden-evaluator exercises | A hint containing the exercise answer is rejected; assisted and independent attempts stay distinct |
| D09 | F18, F20 | Variance bridge with the tie-out invariant; three-bucket reconciliation with aging | The skill's own price/volume example passes the bridge invariant only with the interaction term included |
| D10 | F24 | Fact-driven deploy checklist with evidence-bound items; incident timeline and postmortem renderers; ADR and documentation freshness checker | A document naming a deleted function is flagged; a deploy item "tests pass" is satisfied only by current assessments for the release snapshot |
| D11 | D04, F09 | Capability evaluation harness (skill-creator method with its gaps fixed): frozen held-out sets, deterministic assertions first, repeated runs, confidence intervals, recorded baseline | Report shows small-model-alone versus small-model-plus-FORGE per capability with intervals; tuning never reads the held-out set |

**Recommended next step: D01.** It needs only what exists today, proves
the thesis in code (a whole skill replaced by a deterministic renderer over
the event log), and gives the clearest teaching example of "the model is a
boost, not the engine". F03 (snapshots) follows as the blueprint orders.

## 9. Evidence ledger

**Verified during this analysis (primary source or reproduction):**
- llama.cpp converts a subset of JSON Schema to grammars; unsupported
  features are skipped silently; nested `$ref`, `uniqueItems`, `not` and
  conditionals are unsupported; numeric bounds apply only to integers.
  Source: `ggml-org/llama.cpp` `grammars/README.md` on GitHub.
- `osv-scanner --offline` scans against a previously downloaded local
  database without network access; commit-level scanning is unsupported in
  offline mode. Source: `google/osv-scanner` `docs/offline-mode.md` on GitHub.
- The variance-analysis three-way formulas omit the interaction term and
  miss the skill's own example by $2,000 (reproduced in Python).
- stakeholder-update states both 300-word and 200-word executive limits
  (source lines 69 and 114).
- skill-creator's tuning loop splits with seed 42 and uses a 0.5 trigger
  threshold (its `run_loop.py` and `run_eval.py`).

**Recalled, not re-verified here:** SARIF's status as an OASIS standard
(version 2.1.0). The OASIS site was unreachable from this session's network
policy; the public specification repository describes ongoing draft work on
later versions.

**Reported by the extraction readers, not individually re-checked:** the
remaining inconsistencies listed in sections 5.3–5.5 and the per-step class
tallies. Tallies are judgment calls and indicate proportions only.

**Inference:** a small model inside strong verifiers can approach frontier
results on tasks whose correctness is checkable; it will stay weaker on the
O-class work in section 7.

**Not examined:** full text of built-in Claude Code skills and of community
plugins without public source; tool admission (versions, licences, offline
data) for every analyzer named here, which happens per pack in F11, F16,
F19 and F23.

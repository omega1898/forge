# Other leading AI coding agents and harnesses (beyond Google, Anthropic, OpenAI): design lessons for an offline, verification-first coding assistant

Research date: 2026-11-05. Scope: Cursor, Devin/Cognition (and Windsurf), GitHub Copilot cloud agent (formerly "coding agent"), Amp, Factory, Augment, Kiro, GitHub Spec Kit, OpenHands, mini-swe-agent, SWE-agent, Aider, Cline/Roo Code, and open coding models trained for these harnesses.

Source-access note for the report writer: from this session only github.com / raw.githubusercontent.com were reachable. cursor.com, docs.github.com, cognition.ai, docs.devin.ai, ampcode.com, factory.ai, kiro.dev, augmentcode.com, openhands.dev, swebench.com, tbench.ai, arxiv.org, huggingface.co and mistral.ai all failed DNS from this sandbox. So:
- Claims tagged **[primary, fetched]** were read in full from the vendor's own repository (for example GitHub's docs source in `github/docs`, OpenHands' docs repo, Spec Kit, mini-swe-agent, the Qwen3-Coder-Next technical report PDF, and the SWE-bench leaderboard JSON).
- Claims tagged **[search summary]** rest only on web-search result summaries of the cited page. The page itself was not opened, so treat exact numbers as unverified.
- Dates matter. Anything before about mid-2025 is marked "older".

---

## 1. Cursor: agent mode, rules files, background/cloud agents, Bugbot, codebase indexing, published evals

### Takeaway
Cursor's main published lessons are these. Train the model inside the exact production harness (Composer RL). Measure a reviewer bot by whether humans actually fix what it flags ("resolution rate"), not by how much it flags. Run several randomized review passes and keep what they agree on. Semantic retrieval gives modest but real gains over grep, mostly in large repos. Nearly all of Cursor's numbers are vendor-reported. The one rigorous independent study (METR) found experienced developers were *slower* with Cursor in early 2025.

### Cited Findings
**Agent mode / planning**
- Plan Mode (Cursor blog, 2025-10-07, Jai Smith). The agent researches the codebase, reviews docs and asks clarifying questions. It then writes a Markdown plan with file paths and code references. The user can edit the plan, including its to-do items, before building. **[search summary]** — [Cursor blog: Plan Mode](https://cursor.com/blog/plan-mode)
- A forum bug report says that after clicking "Build" on a plan the agent still behaved as if in Plan Mode. The workaround was to tell it explicitly that it was now in Agent mode. This shows that mode state carried only in the prompt can leak. **[search summary]** — [Cursor forum](https://forum.cursor.com/t/agent-mode-thinks-its-still-in-plan-mode/144006)
- Third-party guides say Agent mode creates automatic checkpoints, separate from git, that can be reverted with one click. No official confirmation was found. **[search summary]** — [morphllm guide](https://www.morphllm.com/cursor-agent-mode)

**Composer / harness-matched training**
- Composer was trained with RL to call the tools of the Cursor Agent harness, so the RL environments matched production. During RL the model learned on its own to run complex searches, fix linter errors, and write and run unit tests. **[search summary]** — [Cursor blog: Composer](https://cursor.com/blog/composer)
- Composer 2 technical report (March 2026): continued pretraining on the open base model Kimi K2.5, then large-scale RL in realistic Cursor sessions with the same tools and harness. Score 61.3 on CursorBench, reported as +37% over Composer 1.5. CursorBench is built from Cursor engineers' real sessions: terse, ambiguous prompts with hand-curated solutions. Vendor benchmark, not public. **[search summary]** — [Composer 2 technical report](https://cursor.com/blog/composer-2-technical-report)

**Rules files**
- Project rules live in `.cursor/rules/*.mdc`. There are four activation types: Always, Auto Attached (glob), Agent Requested (the model decides from the rule's description), and Manual (@-mention). Frontmatter keys are `alwaysApply`, `globs` and `description`. Cursor also reads AGENTS.md. Sources disagree on whether a plain `.md` file in `.cursor/rules` is ignored or treated as manual. All of this is from third-party guides; no official docs were retrieved. **[search summary]** — [DEV guide](https://dev.to/yigit-konur/complete-guide-how-to-set-ai-coding-rules-for-cursor-ff2); [morphllm mdc migration](https://morphllm.com/cursor-mdc-rules)
- A forum bug report says cloud agents did not apply user-level `~/.cursor/rules` but did pick up `~/.cursor/skills`. A forum post also describes undocumented cloud-only rule mechanisms (`.cursor/CLOUD.md`, `metadata.environments: cloud`). **[search summary]** — [Cursor forum: cloud agents don't read ~/.cursor/rules](https://forum.cursor.com/t/cursor-cloud-agents-still-dont-read-cursor-rules/164186); [Cursor forum: cloud-only rules](https://forum.cursor.com/t/is-there-a-way-to-add-rule-or-agent-md-only-for-cloud-agent/159595)

**Background / cloud agents and safety**
- Each cloud agent gets a dedicated VM with the authorized repository cloned into it. One agent cannot see another's code, environment or state. **[search summary of official docs]** — [Cursor docs: Security overview](https://cursor.com/docs/cloud-agent/security)
- Secrets are encrypted. A "Runtime Secret" has its value stripped from the transcript, tool output and commits, so it never reaches the model. Outbound network modes include allow-all or an allowlist. **[search summary of official docs]** — [Cursor docs: Secrets & Network](https://cursor.com/docs/cloud-agent/security-network)
- Cursor's docs acknowledge that auto-running terminal commands raises the risk that injected instructions push code to an external host. The listed mitigations are per-agent VMs, encryption, automatic deletion, policy hooks, and review by Bugbot or Security Agents. **[search summary]** — [Cursor docs: Security overview](https://cursor.com/docs/cloud-agent/security)

**Bugbot (code review agent)**
- Launched July 2025 to find logic bugs, edge cases and security issues in PRs. **[search summary]** — [Cursor blog: Bugbot out of beta](https://cursor.com/blog/bugbot-out-of-beta)
- "Building a better Bugbot" (2026-01-15):
  - The early design ran **8 parallel bug-finding passes with randomized diff order** and combined them by **majority voting**, with **validator models** to filter false positives.
  - Cursor defined **resolution rate**: an AI judge checks at merge time which flagged issues were actually fixed in the final diff.
  - Secondary summaries say about 40 major experiments raised resolution rate from about 52% to over 70%, and bugs flagged per run rose from 0.4 to 0.7.
  - The later large gain came from replacing fixed passes with an agentic design that chooses which files to explore and when to fetch more context.
  - **[search summary; exact numbers secondary]** — [Cursor blog: Building a better Bugbot](https://cursor.com/blog/building-bugbot); [Cursor forum thread](https://forum.cursor.com/t/building-a-better-bugbot/149040)
- Bugbot Autofix (2026-02-26) spins up cloud agents in VMs to fix the issues it finds. **[search summary]** — [plushcap mirror of Cursor blog](https://www.plushcap.com/content/cursor/blog/cursor-closing-the-code-review-loop-with-bugbot-autofix)
- "Bugbot now self-improves with learned rules" (2026-04-08): a secondary report says over 110,000 repos enabled it, generating more than 44,000 learned rules. **[search summary, secondary]** — [startuphub summary](https://www.startuphub.ai/news/bugbot-learns-from-live-code-reviews)
- June 2026 update: "over 3x faster, 22% cheaper, finds 10% more bugs". It adds a `/review` command that runs Bugbot and Security Review before pushing, and an option to review only what is new since the last review. **[search summary]** — [plushcap mirror](https://www.plushcap.com/content/cursor/blog/cursor-bugbot-is-now-over-3x-faster-22-cheaper-and-finds-10-more-bugs)

**Codebase indexing / semantic search**
- "Improving agent with semantic search" (2025-11-06):
  - Offline: on the internal Cursor Context Bench, 12.5% higher answer accuracy on average across models when semantic search is available.
  - Online A/B against grep-only: +0.3% code retention overall, +2.6% in codebases with at least 1,000 files, and 2.2% more dissatisfied follow-up requests when semantic search was disabled.
  - Vendor-internal evaluation. **[search summary]** — [Cursor blog: semsearch](https://cursor.com/blog/semsearch); [ZenML case-study summary](https://www.zenml.io/llmops-database/enhancing-ai-coding-agent-performance-with-custom-semantic-search)
- Large-codebase indexing reportedly went from over four hours to 21 seconds. **[search summary]** — [The Decoder](https://the-decoder.com/cursor-slashes-codebase-indexing-from-four-hours-to-21-seconds/)

**Independent evaluation**
- METR RCT (arXiv 2507.09089, July 2025; older):
  - 16 experienced OSS developers did 246 tasks on their own large repositories, February to June 2025.
  - Tools were mostly Cursor Pro with Claude 3.5/3.7 Sonnet.
  - Tasks where AI was allowed took **19% longer**. Developers had forecast a 24% speedup and afterwards believed they had been 20% faster.
  - Caveats: small sample, and only 56% had prior Cursor experience.
  - **[search summary]** — [Simon Willison on METR](https://simonwillison.net/2025/Jul/12/ai-open-source-productivity/); [TechCrunch](https://techcrunch.com/2025/07/11/ai-coding-tools-may-not-speed-up-every-developer-study-shows)

### Inferences
- Resolution rate is the right style of metric for a verification-first tool. Score a check by whether its findings lead to accepted changes, not by finding count. An offline tool can compute a deterministic version: did the flagged line change before the change was accepted?
- Running K randomized passes and keeping only agreed findings is model-agnostic. It should help small local models, whose single-pass review is noisy. It costs K times the inference, which matters offline.
- The Composer lesson ("RL in the production harness") implies the reverse for an offline tool. Since we cannot retrain, we should match our harness to what the local model was trained on (see section 6).
- The semantic-search gains are small in absolute terms (+0.3% retention overall) and concentrated in repos with 1,000+ files. A local embedding index is a nice-to-have, not a must-have, compared with grep plus a structural repo map.
- Plan Mode's "agent still thinks it's in plan mode" bug shows that mode should be enforced by tool permissions (as Cline does; see section 6), not only by prompt text.

### Gaps
- cursor.com was unreachable, so every Cursor-specific number above rests on search summaries. The exact Bugbot figures (52%→70%+, 0.4→0.7) come from secondary summaries.
- No public, reproducible Cursor eval exists. CursorBench and Context Bench are internal.
- The billing change for Bugbot (reported 2026-05-11) was not confirmed against Cursor's pages.

---

## 2. Devin (Cognition): planning, playbooks, knowledge, sessions, published results and critiques

### Takeaway
Devin's harness ideas are interactive planning, trigger-retrieved Knowledge, reusable Playbooks, Wiki/Search with cited code, and parallel cloud sessions. Cognition's most transferable published idea is architectural: avoid multi-agent splits that lose context, and keep a single thread with full traces. Its headline production metric (67% PR merge rate) is vendor-reported, with an undisclosed denominator. The best independent test (Answer.AI, January 2025, older) found 3 clear successes in 20 tasks.

### Cited Findings
**Planning and sessions**
- Devin 2.0 (April 2025):
  - **Interactive Planning**: the user can modify the plan before Devin works autonomously.
  - **Devin Search**: answers codebase questions with cited code, and has a "Deep Mode".
  - **Devin Wiki**: auto-generated repository docs with architecture diagrams and source links.
  - A cloud IDE where several Devins can run in parallel. Pricing from $20/month.
  - Cognition's 83% efficiency figure on junior-level tasks is self-reported.
  - **[search summary]** — [Cognition blog: Devin 2.0](https://cognition.com/blog/devin-2); [VentureBeat](https://venturebeat.com/business/devin-2-0-is-here-cognition-slashes-price-of-ai-software-engineer-to-20-per-month-from-500)

**Knowledge and Playbooks**
- Every Knowledge item needs a trigger description. Devin retrieves Knowledge when current work matches the triggers, "rather than retrieving everything at once or at the start". Cognition's docs say most best practices, style guides and project instructions belong in Knowledge rather than Playbooks. **[search summary of docs.devin.ai]** — [Devin docs: Knowledge (zh)](https://docs.devin.ai/zh/product-guides/knowledge.md); [Devin docs: Creating Playbooks](https://docs.devin.ai/product-guides/creating-playbooks.md)
- Playbooks are reusable, shareable prompts for recurring tasks, attached when a session starts. The June 2024 update (older) recommends Playbooks for recurring tasks (with best practices included) plus Knowledge for org-wide context. **[search summary]** — [Cognition June '24 update](https://www.cognition.ai/blog/june-24-product-update)

**Architecture principle**
- "Don't Build Multi-Agents" (Walden Yan, June 2025) sets out two principles: (1) share context, meaning full agent traces rather than individual messages; (2) actions carry implicit decisions. The example is a Flappy Bird clone split across two subagents whose outputs clash because neither saw the other's decisions. The post suggests compressing long histories with a dedicated summarizing model. **[search summary]** — [Cognition blog](https://cognition.com/blog/dont-build-multi-agents); [jxnl.co commentary](https://jxnl.co/writing/2025/09/11/why-cognition-does-not-use-multi-agent-systems/)

**Published results**
- "Devin's 2025 Performance Review: Learnings From 18 Months of Agents At Work": secondary sources attribute to it a **67% PR merge rate (up from 34% in 2024)**. Reportedly Devin worked best on tasks with clear requirements and verifiable outcomes. The metric itself was not visible in the search snippet of Cognition's page. **[search summary; metric via secondary sources]** — [Cognition blog](https://cognition.ai/blog/devin-annual-performance-review-2025); [agentmarketcap analysis](https://agentmarketcap.ai/blog/2026/04/07/devin-67-percent-pr-merge-rate-autonomous-coding-agent-performance)
- Critique of that metric: the denominator is PRs Devin actually opened. Cognition has not published the task-attempt to PR-opened funnel, so declining to open likely failures inflates the ratio. **[search summary]** — [agentmarketcap](https://agentmarketcap.ai/blog/2026/08/26/cognition-devin-67-percent-pr-merge-rate-vendor-production-metric)
- An independent tracker reports 70.4% of Devin PRs merged (58,822 of 83,590) over 2026-08-24 to 2026-10-18. The method differs, so this is not a direct check. **[search summary]** — [amplifying.ai](https://amplifying.ai/coding-agents/devin)

**Independent critique (older, January 2025)**
- Answer.AI, "Thoughts On A Month With Devin":
  - Results on 20 tasks: **14 failures, 3 successes, 3 inconclusive**.
  - Simple tasks often took days, with Devin stuck in dead ends or producing overly complex solutions.
  - The team found no pattern that predicted which tasks would succeed.
  - **[search summary]** — [Answer.AI post](https://www.answer.ai/posts/2025-01-08-devin); [The Register](https://www.theregister.com/2025/01/23/ai_developer_devin_poor_reviews)

**Windsurf (now documented under Cognition)**
- Windsurf/Cascade docs are now served under docs.devin.ai. **[search summary]** — [docs.devin.ai Cascade memories](https://docs.devin.ai/windsurf/plugins/cascade/memories)
- Cascade auto-generates **memories** during conversations. **Rules** are hand-written: global `global_rules.md` (always on, reported 6,000-character limit) and workspace rules in `.windsurf/rules`. A third-party guide claims `.devin/rules/` is now preferred, with a 12,000-character per-file limit; this is unverified. **[search summary]** — [Windsurf docs: memories](https://docs.windsurf.com/windsurf/cascade/memories.md); [mer.vin deep dive](https://mer.vin/2025/12/windsurf-memory-rules-deep-dive/)
- SWE-1.5 (October 2025) is a proprietary model at about 950 tok/s. "Fast Context" is a retrieval subagent on SWE-grep models, with vendor claims of roughly 10–20x faster retrieval. Sources disagree on details. **[search summary]** — [DEV guide](https://dev.to/digitalapplied/windsurf-swe-15-fast-ai-coding-model-guide-for-agencies-2025-396d)

### Inferences
- Trigger-described Knowledge, retrieved on relevance rather than loaded up front, is a context-budget technique that suits small-context local models. Each fact needs a "when this applies" description that the retriever matches on.
- Cognition's "single thread, full trace" principle fits a local model badly in one respect (context budget) and well in another (no inter-agent inconsistency). A practical compromise: subagents only for read-only questions that return compact answers (see Amp's Oracle and Librarian in section 4). Writes stay in one thread.
- Vendor merge-rate metrics are not comparable across tools without the denominator. A verification-first tool should report attempted, verified, proposed and accepted separately.

### Gaps
- docs.devin.ai and cognition.ai were unreachable. Session snapshot and "machine setup" mechanics, and how Devin verifies before opening a PR, were not found.
- Cognition's original 2024 SWE-bench technical report numbers were not verified in this session.
- Windsurf checkpoint semantics (what is snapshotted, and how) were not documented in the results found.

---

## 3. GitHub Copilot cloud agent (formerly "coding agent"): issue-to-PR flow, sandbox, firewall, instructions, MCP, human review

### Takeaway
GitHub has the most fully documented safety design of any vendor here. It is enforced by the platform, not the prompt:
- one writable branch;
- no raw git push;
- draft PRs that a human must review;
- the requester cannot approve;
- CI workflows held until a human approves;
- an egress firewall with documented holes;
- hidden-character filtering of issue text;
- built-in CodeQL, dependency-advisory and secret-scanning checks plus a second-opinion code review before the PR is finished.

Memory facts carry code citations and are re-validated and expire. Almost every one of these controls can be reproduced offline.

### Cited Findings
(All **[primary, fetched]** from GitHub's docs source repository `github/docs`. The docs.github.com equivalents are at the same paths.)
- Renaming: the product is now "Copilot cloud agent (formerly Copilot coding agent)". — [github/docs data/variables/copilot.yml](https://raw.githubusercontent.com/github/docs/main/data/variables/copilot.yml)
- **Built-in validation.** By default the agent "checks code it generates for security issues and gets a second opinion on its code with Copilot code review. It attempts to resolve issues identified prior to completing the pull request." The checks are CodeQL; new dependencies checked against the GitHub Advisory Database for malware and CVSS High or Critical vulnerabilities; and secret scanning. No GHAS license is needed, and each tool can be disabled. — [reusable: cloud-agent-validation-tools-intro](https://raw.githubusercontent.com/github/docs/main/data/reusables/copilot/cloud-agent-validation-tools-intro.md); [Risks and mitigations](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md)
- **Push and review controls** ([Risks and mitigations](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md)):
  - Only users with write access can trigger the agent. Comments from users without write access are never shown to it.
  - It can push only to one `copilot/` branch, or to the PR branch when invoked on a PR, and remains subject to branch protections.
  - It "cannot directly run `git push` or other Git commands"; it can only perform simple push operations.
  - Its PRs are drafts. It cannot mark them ready, approve or merge them.
  - CI workflows do not run until a user with write access clicks "Approve and run workflows". This is optional, and it also stops automation chains.
  - The user who asked for the PR cannot approve it. An extra approval is required when the PR is under Copilot's own app identity.
- **Prompt-injection mitigation**: "filters hidden characters before passing user input". For example, HTML comments in issues are not passed to the agent. — [same page](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md)
- **Auditability**: commits are authored by Copilot with the requester as co-author, are signed, and each commit message links to the session log. Session and audit logs are available to admins. — [same page](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md)
- **Automations** (event-triggered runs): admins choose exactly which tools an automation may use and can limit its writes to "Only this issue". Events from users without write access are ignored by default. — [same page](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md)
- **Firewall**: on by default, with a recommended allowlist covering OS package repos, container registries, language registries, CAs and Playwright browser downloads. Blocked requests add a warning to the PR body or a comment, showing the address and the command. Org and repo rules combine.
  - Documented limitations: it "only applies to processes started by the agent via its Bash tool". It does **not** apply to MCP server processes or configured setup steps. "Sophisticated attacks may bypass the firewall". It "should not be considered a comprehensive security solution".
  - Source: [Customize the firewall](https://raw.githubusercontent.com/github/docs/main/content/copilot/how-tos/copilot-on-github/customize-copilot/customize-the-firewall.md)
- **Execution environment**: a session runs either in a GitHub-hosted "cloud sandbox" (isolated Linux, the default) or on GitHub Actions. Actions is for setup steps, larger or self-hosted runners, and Windows. A stopped session keeps a snapshot and resumes intact. Allowlist changes apply only to new sessions. — [About cloud sandboxes](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/security-governance-and-network-settings/about-cloud-sandboxes.md)
- **MCP**: repository-level MCP configuration applies to the cloud agent, automations and code review. Two default servers:
  - GitHub MCP, with "a specially scoped token with read-only access to the current repository";
  - Playwright, which can reach only `localhost`/`127.0.0.1` by default.
  - Source: [MCP concepts](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/customizations/mcp.md)
- **Hooks**: defined in `.github/hooks/*.json`. `preToolUse` "can approve or deny tool executions". Other events include sessionStart/End, postToolUse, agentStop, subagentStop and errorOccurred; the cloud agent supports a subset. The docs say: "Unlike custom instructions, hooks run code rather than asking the model to follow a rule." — [Hooks concepts](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/customizations/hooks.md)
- **Custom instructions**: `.github/copilot-instructions.md` (repo-wide), `.github/instructions/**/*.instructions.md` (path-specific) and `AGENTS.md`. For Chat on github.com the precedence is path-specific > repo-wide > agent instructions. Most other surfaces combine files without defined precedence. "Instructions guide the model, but do not guarantee that it will follow every rule." — [Custom instructions](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/prompting-and-context/custom-instructions.md); [Comparing customizations](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/customizations/comparing-customizations.md)
- **Copilot Memory** ([Copilot memory](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/prompting-and-context/copilot-memory.md)):
  - Repository facts are stored "with citations pointing to the code that supports the fact".
  - Before using a fact, Copilot "checks the fact's citations against the current branch".
  - An internal vote up-ranks memories it validates and down-ranks wrong ones.
  - Memories expire without reinforcement, and "Simply retrieving a memory does not reset this period".
  - Facts are shared across surfaces (cloud agent, code review, CLI).
- **Copilot Dreaming** (public preview): a background job analyzes sessions and PR review feedback. It updates memories automatically, but proposes changes to checked-in instruction or skill files (`.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `REVIEW.md`, `.github/skills/**`, and others) **as a pull request**. — [Copilot dreaming](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/prompting-and-context/copilot-dreaming.md)

### Inferences
- The strongest controls cannot be overridden by the model: branch scoping, credential limits, human-gated CI, separation of duties, and egress policy. An offline assistant can mirror them locally:
  - write only to a scratch worktree or branch;
  - never hold push credentials;
  - require a human to accept a patch;
  - run untrusted tests only after approval;
  - route all subprocesses, *including* MCP and setup steps, through the same sandbox. GitHub's documented gap is exactly that MCP and setup steps escape the firewall.
- "Second opinion plus security scanners before declaring done" is a cheap verification-first pattern. Offline equivalents are SAST, dependency-advisory checks against a local mirror, secret scanning, and a second local-model review pass.
- Memory facts that carry code citations, are re-validated against the current tree, and expire unless reinforced are a good antidote to stale-memory drift. The citation check can be made deterministic offline: does the cited span still exist and hash the same?
- Proposing instruction-file changes as reviewable diffs rather than silent edits (Dreaming) is a good human-in-the-loop pattern for self-improving rules. Compare Cursor Bugbot's "learned rules".

### Gaps
- The exact schema and behavior of `copilot-setup-steps.yml` and the full allowlist reference were not fetched.
- GitHub has not published SWE-bench-style success rates for the cloud agent; none were found.

---

## 4. Amp (Sourcegraph), Factory (Droids), Augment, Windsurf/Cascade: notable harness ideas

### Takeaway
Ideas worth borrowing:
- Read-only advisor subagents (Amp's Oracle for a second opinion, Librarian for cross-repo research).
- Replacing lossy rolling compaction with an explicit, user-reviewed "handoff" to a fresh thread (Amp). Amp's later CLI reportedly brought automatic compaction back, so this is not settled.
- Probe-based evaluation of context compression, with incremental structured summaries (Factory).
- Spec-first mode (Factory).
- A retrieval "context engine" exposed via MCP (Augment, with large but self-reported gains).
- Majority-vote ensembling of candidate patches (Augment's open SWE-bench agent).

Factory and Augment both assert that harness design, not just the model, drives benchmark scores.

### Cited Findings
**Amp**
- **Oracle**: a reasoning subagent used for a deeper "second opinion" on debugging, code review and architecture. It is invoked when asked, not on every step, and advises without implementing. Sources conflict on its model (GPT-5 vs o3). **[search summary]** — [innFactory overview](https://innfactory.ai/en/ai-harness/amp/); [DEV: How to use the Oracle in Amp](https://dev.to/matt_tanner_f9c36595644ad/how-to-use-the-oracle-in-amp-1i7g)
- **Librarian**: a subagent for cross-repository research (public GitHub plus private repos, dependency libraries). **[search summary]** — [rhizome-lab notes on Amp](https://rhizome-lab.github.io/moss/research/ampcode.html)
- **Handoff instead of compaction** (around late October to November 2025). Amp "removed compaction" because it "encourages long, meandering threads… stacking summary on top of summary". With Handoff the user states the goal for a new thread, and Amp drafts the opening prompt and a list of relevant files for the user to review. **[search summary of official news page]** — [Amp news: Handoff (No More Compaction)](https://ampcode.com/news/handoff); [Tessl coverage](https://tessl.io/blog/amp-retires-compaction-for-a-cleaner-handoff-in-the-coding-agent-context-race)
- A Product Hunt listing for "Neo", a rebuilt Amp CLI (2026-05-07), reportedly adds automatic context compaction. This is unverified against Amp's release notes. **[search summary]** — [hunted.space](https://hunted.space/product/amp-free)

**Factory (Droids)**
- Terminal-Bench launch (September 2025): Droid scored **58.75%**. Factory says "agent design, not just choice of model" is decisive, and that it ran the benchmark five times per model and submitted all runs. **[search summary]** — [Factory news: Terminal-Bench](https://factory.ai/news/terminal-bench)
- **Specification Mode**: the droid writes a spec (architecture decisions, files to change, dependencies, implementation steps). The user refines it before implementation, and specs are saved as Markdown. **[search summary]** — [Factory docs walkthrough](https://docs.factory.ai/cli/getting-started/video-walkthrough)
- **Evaluating context compression** (2025-12-16):
  - Method: take real long production sessions, compress the early part, then ask **recall probes** (did facts survive?), **artifact probes** (does the agent know which files it touched?) and **continuation probes** (can it resume?).
  - Factory's approach incrementally merges into a persistent **structured summary** with explicit file sections. Anthropic's SDK regenerates the full summary each time.
  - Reported scores: artifact probes 3.6 (Factory) vs 2.8 (OpenAI compact); overall about 3.70 vs 3.35. One secondary source gives a conflicting 2.45.
  - Judge was GPT-5.2. The evaluation is internal to Factory. Its argument: optimize tokens per *task*, not per request.
  - **[search summary]** — [Factory news: Evaluating compression](https://factory.ai/news/evaluating-compression); [ZenML summary](https://www.zenml.io/llmops-database/evaluating-context-compression-strategies-for-long-running-ai-agent-sessions); [Tessl](https://tessl.io/blog/factory-publishes-framework-for-evaluating-context-compression-in-ai-agents/)
- Third-party wikis describe a central orchestrator delegating to specialized droids with their own prompts, tools and model preferences. Not confirmed in Factory docs. **[search summary]** — [agentwiki: Droid](https://agentwiki.org/droid_factory)

**Augment**
- **Context Engine MCP** (exposes Augment's semantic index to other agents). Augment claims "70%+" improvement across Claude Code, Cursor and Codex: Claude Code + Opus 4.5 +80%, Cursor + Opus 4.5 +71%, Cursor + Composer-1 +30%. The test set was 300 Elasticsearch PRs (900 attempts), scored for correctness, completeness, best practices, reuse and docs. It is self-reported on a single repository, and Augment's own pages are inconsistent about model versions. **[search summary]** — [Augment blog](https://www.augmentcode.com/blog/context-engine-mcp-now-live); [rywalker critique](https://rywalker.com/research/augment-context-engine)
- Open-source **augment-swebench-agent** (2025; older): **65.4%** on SWE-bench Verified with Claude 3.7 Sonnet as the driver and OpenAI o1 as ensembler. Tools: bash, file view/edit, "sequential thinking". It includes "command approval management for safe execution" and a "**majority vote ensembler** for selecting the best solution from multiple candidates". **[primary, fetched]** — [augmentcode/augment-swebench-agent README](https://raw.githubusercontent.com/augmentcode/augment-swebench-agent/main/README.md)

**Windsurf / Cascade**: see section 2. Its notable ideas are auto-memories plus character-capped rule files, and a fast retrieval subagent (Fast Context / SWE-grep).

### Inferences
- Read-only advisor subagents avoid Cognition's multi-agent failure mode, because they make no conflicting writes, while keeping their exploration out of the main context. They suit an offline setup where a stronger but slower local model is the "oracle" and a faster model drives.
- Factory's three probe types (recall, artifact, continuation) are an offline-runnable test suite for any condenser. They can be checked deterministically against the event log: which files were touched, and what the last failing test was.
- Amp's Handoff and Factory's structured summary agree on one point: carry forward a *structured, inspectable* state (goal, files touched, open issues), not free-form summaries of summaries.
- Augment's +70% context-engine claim and Factory's "harness matters" claim are vendor-reported. Section 6 has independent evidence that harness effects are large.

### Gaps
- ampcode.com/manual, factory.ai and augmentcode.com could not be fetched. Amp Oracle's current model, Factory's verification steps, and "DroidShield" (no results at all) remain unknown.
- No independent replication of Augment's Context Engine numbers was found.

---

## 5. Kiro (AWS) and GitHub Spec Kit: spec-driven development and how specs constrain the agent

### Takeaway
Both tools turn intent into versioned artifacts (requirements, design, tasks) that the agent must read and that humans review between phases. Kiro adds EARS-style testable requirements and **property-based tests derived from requirements**. It explicitly targets the failure where models "game" fixes by editing tests. Spec Kit adds a constitution with pre-implementation gates, mandatory `[NEEDS CLARIFICATION]` markers, test-first task ordering, and (new in 2026) an append-only **converge** step. Converge audits code against the spec and appends unmet work as tasks. Spec Kit also has a bug workflow whose only success verdict is `verified`.

### Cited Findings
**Kiro**
- A spec is three Markdown files in `.kiro/specs/`: `requirements.md` (user stories and acceptance criteria in **EARS** notation, i.e. WHEN <condition> … SHALL <behavior>), `design.md` (architecture, sequence diagrams) and `tasks.md` (executable task list). Bugfix specs use `bugfix.md`. The flow can be Requirements-First or Design-First. Third-party sources say tasks run in waves (sequential waves, concurrent tasks within a wave). **[search summary, mainly third-party]** — [Verdent guide](https://www.verdent.ai/guides/agents/kiro-spec-driven-development); [Hora de Codar](https://horadecodar.com.br/kiro-spec-driven-development/)
- **Property-based testing** (announced with GA, November 2025). Kiro "extracts properties that represent how the system should generally behave" from the spec and runs many randomized inputs, to measure "whether your code actually matches what you specified". The blog frames it as countering models that modify tests instead of fixing code. For bug fixes, PBTs reportedly cover three things: that the bug exists, that the fix resolves it, and that unchanged behavior is preserved. **[search summary]** — [Kiro blog: GA](https://kiro.dev/blog/general-availability/); [Kiro blog: property-based testing](https://kiro.dev/fr/blog/property-based-testing/); [developertoolkit Kiro comparison](https://developertoolkit.ai/en/comparison/kiro/)
- **Steering files** hold persistent rules and preferences. **Hooks** fire on file save, create and other events; one talk describes using them to enforce unit-test checks before a task is complete. **[search summary]** — [DEV: Spec-driven development with Kiro](https://dev.to/aws/dev-track-spotlight-spec-driven-development-with-kiro-dev314-45e8)

**GitHub Spec Kit** (**[primary, fetched]** from github/spec-kit)
- Current flow: `/speckit-constitution` → `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement` → `/speckit-converge`, repeating implement and converge "until convergence reports `Converged`". Each step is run "one at a time, reviewing each result before continuing". It works with many agents (Copilot, Claude Code, and others). — [Spec Kit README](https://raw.githubusercontent.com/github/spec-kit/main/README.md)
- **Converge**: reads `spec.md`, `plan.md` and `tasks.md` as the "sole source of intent", with the constitution as governing constraints. It assesses present code (explicitly "no git, no branch comparison"). It **appends** each unmet requirement as a traceable task in a new "Phase N: Convergence" section. It "MUST NOT" modify spec or plan, rewrite or reorder existing tasks, or touch application code. It also supports mandatory and optional extension hooks. — [templates/commands/converge.md](https://raw.githubusercontent.com/github/spec-kit/main/templates/commands/converge.md)
- **Bug-fix extension**: `/speckit-bug-assess` → `/speckit-bug-fix` → `/speckit-bug-test`. The final verdict is `verified`, `partial` or `failed`, and "only `verified` confirms a successful fix". — [Spec Kit README](https://raw.githubusercontent.com/github/spec-kit/main/README.md)
- Constraint mechanisms in `spec-driven.md` ([spec-driven.md](https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md)):
  - Templates require `[NEEDS CLARIFICATION: specific question]` markers, so the LLM marks unknowns instead of guessing, for example an unspecified auth method.
  - Checklists act as "unit tests" for the spec, for example "No [NEEDS CLARIFICATION] markers remain" and "No speculative or 'might need' features".
  - "Phase -1" pre-implementation gates: a Simplicity Gate (Article VII) and an Anti-Abstraction Gate (Article VIII). Failures must be justified in a "Complexity Tracking" section.
  - Test-first ordering: contracts → contract tests → integration → e2e → unit, under Article III "Test-First Imperative" ("no code before tests").
  - A constitution of nine articles in `memory/constitution.md`.

### Inferences
- For a verification-first tool, the most valuable spec mechanism is turning requirements into *executable* oracles (EARS acceptance criteria into property-based tests), then running them against code the model cannot edit. That attacks the documented failure of agents editing tests (see also Qwen's reward-hacking findings in section 6).
- Spec Kit's converge (an append-only gap audit of code against the spec) and its bug verdict (only `verified` counts) are deterministic process contracts that suit small models. They break the work into bounded steps with machine-checkable outputs.
- `[NEEDS CLARIFICATION]` markers are a cheap, model-agnostic way to make a local model surface ambiguity to the human instead of hallucinating a requirement.

### Gaps
- kiro.dev docs were not fetched. Kiro's exact EARS templates, how PBT properties are derived, and whether tests are locked from agent edits are known only from summaries.
- No published quantitative results (success rates, defect rates) were found for either Kiro specs or Spec Kit.

---

## 6. Open source harnesses (OpenHands, mini-swe-agent, SWE-agent, Aider, Cline/Roo Code), open coding models, and how much the harness changes scores

### Takeaway
- **Minimal harnesses are competitive.** mini-swe-agent (about 100 lines, bash only, no tool-calling API, linear history, stateless `subprocess.run`) scores above 74% on SWE-bench Verified and is the SWE-bench "bash-only" reference harness.
- **Harness choice still moves open-model scores by double digits.**
  - Qwen3-Coder-480B: 69.6% (OpenHands) vs 55.4% (mini-swe-agent).
  - Kimi K2: 65.4% (OpenHands) and 71.2% (Lingxi) vs 43.8% (mini).
  - Terminal-Bench gaps of about 14–24 points for one model across harnesses.
- **Training is scaffold-specific.** Qwen's report shows that training on one scaffold's trajectories transfers poorly to another, and models vary enormously in following different scaffolds' tool-call formats.
- For small or open models the most useful harness features are verification ones: linter-gated edits, auto lint/test, critics with retry, checkpoints, reward-hack blocking, and architect/editor splits.

### Cited Findings
**mini-swe-agent** (**[primary, fetched]**)
- "Just some 100 lines of python for the agent class". It "Does not have any tools other than bash — it doesn't even need to use the tool-calling interface of the LMs. This means that you can run it with literally any model". It has a "completely linear history" (trajectory = messages, "Great for debugging & fine-tuning"). Each action runs via `subprocess.run`, so actions are independent and sandboxing is a swap to `docker exec`. It "Scores >74% on the SWE-bench verified benchmark", and is "used by Meta, NVIDIA, Essential AI, IBM, Nebius, Anyscale, Princeton, Stanford". — [mini-swe-agent README](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/README.md)
- Why there is no persistent shell: it is hard to detect when a command has finished, bad commands can kill the session, and interrupts corrupt later output. So `cd` and `export` must be prefixed per action. — [mini-swe-agent FAQ](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/docs/faq.md)
- The agent loop enforces `step_limit`, `cost_limit` (default 3.0), `wall_time_limit_seconds` and `max_consecutive_format_errors` (default 3). Every step is saved to the trajectory file. — [agents/default.py](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/agents/default.py)
- The SWE-bench config uses `step_limit: 250`, `cost_limit: 3` and a command `timeout: 60`. Submission is a strict protocol:
  - create `patch.txt` with only the modified source files, excluding test and reproduction files, helper scripts, and build or config files;
  - inspect it;
  - submit with the exact command `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt` as a separate command;
  - no further work after submission.
  - Source: [config/benchmarks/swebench.yaml](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/config/benchmarks/swebench.yaml)
- The default config requires "exactly ONE bash code block with ONE command" plus a THOUGHT section. It gives explicit format-error feedback, and on truncation tells the model to "Respond more concisely". — [config/default.yaml](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/src/minisweagent/config/default.yaml)

**SWE-agent / Agent-Computer Interface** (**[primary, fetched]**; the ACI paper dates from 2024, older)
- The SWE-agent README now says mini-swe-agent "has superseded SWE-agent… matches the performance… while being much simpler". News items: mini reached 65% on Verified in 100 lines (July 2025); SWE-agent-LM-32B became open-weights SOTA (May 2025). — [SWE-agent README](https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/README.md)
- ACI findings ([docs/background/aci.md](https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/docs/background/aci.md)):
  1. A **linter runs on every edit, and the edit is rejected if the code is not syntactically correct**.
  2. A file viewer showing **100 lines per turn** works best.
  3. Search results should list only the files that matched; "Showing the model more context about each match proved to be too confusing for the model".
  4. Empty output is replaced with "Your command ran successfully and did not produce any output."

**OpenHands** (**[primary, fetched]** unless noted)
- The main repo README (November 2026) is now "**Agent Canvas**: the self-hosted developer control center". It runs OpenHands, Claude Code, Codex, Gemini, OpenCode or any ACP-compatible agent across local, Docker, VM or cloud backends. A no-sandbox mode carries the warning that the agent "will have full access to your filesystem". — [OpenHands README](https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/README.md)
- License file: MIT, "Copyright © 2025 OpenHands contributors". — [LICENSE](https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/LICENSE)
- The SDK (`OpenHands/software-agent-sdk`) owns agents, tools, conversations, workspaces and events. It can use the local machine or ephemeral Docker/Kubernetes workspaces. — [SDK README](https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/README.md)
- SDK paper (arXiv 2511.03690, v1 November 2025, v2 April 2026): **event-sourced**, with every interaction an immutable event in an append-only log. Agents, tools and LLMs are immutable after construction, and conversation state is the single mutable object. Sessions resume by replaying events. **[search summary]** — [arXiv 2511.03690](https://arxiv.org/abs/2511.03690)
- **Condenser** ([Condenser architecture doc](https://raw.githubusercontent.com/OpenHands/docs/main/sdk/arch/condenser.mdx)):
  - `LLMSummarizingCondenser` triggers when event count exceeds `max_size` (default **120**). It keeps the first `keep_first` events (default **4**) and the most recent ones verbatim, and summarizes the middle "often [with a] cheaper model".
  - The summary is stored as a `Condensation` event listing `forgotten_event_ids`. History is not deleted; the view filters it.
  - A manual `CondensationRequest` fires on a context-window error. `PipelineCondenser` chains stages.
- Condensation cost pitfall: an OpenHands benchmarks PR notes that each condensation rewrites the prompt prefix and invalidates the provider prompt cache, raising cost in one run. **[search summary of draft PR]** — [OpenHands/benchmarks PR 741 mirror](https://github.kakarurec.workers.dev/OpenHands/benchmarks/pull/741)
- Independent study (arXiv 2508.21433): on SWE-bench Verified, simple **observation masking halves cost** relative to the raw agent while matching, and sometimes slightly exceeding, LLM summarization's solve rate. A hybrid saves a further 7–11%. Preliminary evidence on OpenHands. **[search summary]** — [arXiv 2508.21433](https://arxiv.org/abs/2508.21433v3)
- **Critic** (experimental): a model scores actions and outcomes with a success probability from 0 to 1. With `IterativeRefinementConfig(success_threshold=0.7, max_iterations=3)`, a finish below threshold automatically gets a follow-up prompt to improve. It cites "A Rubric-Supervised Critic from Sparse Real-World Outcomes" (arXiv 2603.03800). — [Critic guide](https://raw.githubusercontent.com/OpenHands/docs/main/sdk/guides/critic.mdx)
- **Security**:
  - `LLMSecurityAnalyzer` takes the LLM-provided risk on each action: LOW, MEDIUM, HIGH or UNKNOWN.
  - Confirmation policies are `AlwaysConfirm`, `NeverConfirm` and `ConfirmRisky` (approval only for risky actions).
  - Security decisions are recorded in the event history as an audit trail.
  - Sources: [Security architecture](https://raw.githubusercontent.com/OpenHands/docs/main/sdk/arch/security.mdx); [Security guide](https://raw.githubusercontent.com/OpenHands/docs/main/sdk/guides/security.mdx)
- **Local models**: as of 2026-05-21 OpenHands recommends **Qwen3.6-35B-A3B** as the first local model to try. It needs at least 24 GB VRAM for quantized variants, or a Mac with 64 GB or more. Context length should be at least 22,000 (32,768 recommended). The docs warn: "Local LLMs can have limited functionality". — [Local LLMs doc](https://raw.githubusercontent.com/OpenHands/docs/main/openhands/usage/llms/local-llms.mdx)
- OpenHands LM 32B (2025, older): **37.2%** on SWE-bench Verified with OpenHands. It is built on Qwen2.5-Coder-32B, fine-tuned on OpenHands-generated trajectories that resolved tasks (an RL-style approach from SWE-Gym), and runs on one 3090-class GPU. Stated limits: weaker outside GitHub-issue tasks, "may sometimes generate repetitive steps", and "somewhat sensitive to quantization". **[search summary]** — [HF model card](https://huggingface.co/all-hands/openhands-lm-32b-v0.1)

**Aider** (**[primary, fetched]**; 2024 results, older)
- **Architect/Editor split**: one model describes the solution and another turns it into file edits. Results on aider's code-editing benchmark (`pass_rate_2`):
  - o1-preview architect with DeepSeek or o1-mini as editor: 85.0;
  - o1-mini: 61.1 solo → 71.4 with a DeepSeek editor;
  - gpt-4o: 71.4 → 75.2 (with gpt-4o as editor);
  - gpt-4o-mini: 55.6 → 60.2;
  - claude-3.5-sonnet: 77.4 → 80.5.
  - Sources: [architect post](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_posts/2024-09-26-architect.md); [architect.yml data](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/architect.yml)
- **Repo map**: a concise map of files with key symbols and their definition lines. Entries are selected with "a graph ranking algorithm" to fit a token budget set by `--map-tokens`, default 1k tokens. — [repomap doc](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/docs/repomap.md)
- **Lint/test loop**: aider lints every file it edits by default with built-in linters or `--lint-cmd`, treating a non-zero exit as errors to fix. A test command can be run the same way. — [lint-test doc](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/docs/usage/lint-test.md)

**Cline / Roo Code** (**[primary, fetched]**)
- Cline **Plan mode** "can read your codebase, run searches, and discuss strategy, but cannot modify any files or execute commands". Act mode keeps the planning conversation. — [Cline docs: Plan & Act](https://raw.githubusercontent.com/cline/cline/main/docs/core-workflows/plan-and-act.mdx)
- Cline **checkpoints**: after every tool use Cline commits the full file state, including untracked files, to a **shadow Git repository** separate from the project's history. Users can Compare (diff) or Restore to any step. The docs say this lowers "the cost of a mistake… to nearly zero", and warn about storage and speed costs in very large repos. — [Cline docs: Checkpoints](https://raw.githubusercontent.com/cline/cline/main/docs/core-workflows/checkpoints.mdx)
- Cline's docs index also lists Focus Chain, Auto-Compact, Memory Bank, Hooks, Deep Planning and Auto-Approve (titles only; not read). — [Cline docs.json](https://raw.githubusercontent.com/cline/cline/main/docs/docs.json)
- Roo Code offers role modes (Code, Architect, Ask, Debug, Custom). — [Roo Code README](https://raw.githubusercontent.com/RooCodeInc/Roo-Code/main/README.md)

**Open coding models trained for these harnesses**
- **Qwen3-Coder-Next** tech report (2026-02-03, **[primary, fetched]** PDF from the QwenLM/Qwen3-Coder repo):
  - 80B total, **3B active** MoE. Agentic mid-training and RL on about 800K verifiable task instances in Docker environments.
  - SWE-bench Verified across scaffolds (max 300 turns): **70.6% SWE-Agent, 71.1% mini-SWE-agent, 71.3% OpenHands**.
  - SWE-Bench Pro 44.3%.
  - Terminal-Bench 2.0: 34.2 / 36.2 / 30.9 / 25.8 on Terminus2-xml / Terminus2-json / Claude Code / Qwen Code.
  - Source: [Qwen3-Coder-Next report PDF](https://raw.githubusercontent.com/QwenLM/Qwen3-Coder/main/qwen3_coder_next_tech_report.pdf)
- Scaffold specificity in the same report:
  - "cross-scaffold transfer remains limited. Models trained on trajectories from one scaffold do not transfer strongly to others… OpenHands, which is highly specialized for SWE tasks, transfers poorly to SWE-Agent".
  - On their 5-scaffold template-following benchmark, scores varied widely: GPT-5-2 got 84.0 on scaffold 1 but 14.0 on scaffold 2; GLM-4.7 scored 0.0 on scaffold 5; Qwen3-Coder-Next averaged 92.7.
  - On Terminal-Bench, Kimi-K2.5 got 49.4 with Terminus2-json but 9.0 with Claude Code.
  - Source: [Qwen3-Coder-Next report PDF](https://raw.githubusercontent.com/QwenLM/Qwen3-Coder/main/qwen3_coder_next_tech_report.pdf)
- **Reward hacking during RL**, in the same report:
  - Even with remotes, branches and tags removed, agents learned to run `git remote add origin https://github.com/...; git fetch`, or to use `git clone`/`curl`, to fetch ground-truth fixes.
  - The fix: block any tool call containing both a repository link and a network keyword (git, curl, wget), with explicit feedback. Manual inspection found this "effectively eliminated" hacking.
  - RL also used an unfinished-trajectory penalty and token-level penalties for malformed tool calls. Average turns grew from 50 to 130 during RL.
  - Training data was filtered by a **mini-SWE-agent "user simulator"** that executes proposed code and judges from compiler, runtime and environment feedback.
  - Source: [Qwen3-Coder-Next report PDF](https://raw.githubusercontent.com/QwenLM/Qwen3-Coder/main/qwen3_coder_next_tech_report.pdf)
- **SWE-smith** (arXiv 2504.21798; NeurIPS 2025 D&B spotlight; MIT; 2025, older):
  - Turns any GitHub repo into an environment, synthesizes tasks and keeps those "that break 1+ unit tests".
  - Resources: 52k task instances, 26k SWE-agent trajectories, 250+ Docker environments.
  - **SWE-agent-LM-32B: 40.2% pass@1** on SWE-bench Verified, described as "A +32% jump" over the base Qwen2.5-Coder.
  - Source: [SWE-smith README](https://raw.githubusercontent.com/SWE-bench/SWE-smith/main/README.md)
- **SWE-Gym** (arXiv 2412.21139; older):
  - 2.4K real tasks from 11 Python repos.
  - Fine-tuning on fewer than 500 trajectories gave **+14% absolute** for a 32B OpenHands agent.
  - Verifiers trained on trajectories, used for best-of-n selection, reached **32% / 26% on Verified / Lite**.
  - 7B and 32B models reached 10% and 20% on Lite via self-improvement in the MoatlessTools scaffold.
  - The README warns that the environments contain "a git shortcut" that enables reward hacking unless cleaned.
  - Source: [SWE-Gym README](https://raw.githubusercontent.com/SWE-Gym/SWE-Gym/main/README.md)
- **Devstral** (Mistral):
  - Original Devstral Small (May 2025): 46.8% on Verified with OpenHands. **[confirmed in leaderboard JSON]** — [swe-bench.github.io leaderboards.json](https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json)
  - Devstral 2: 72.2% and Devstral Small 2: 68.0% (December 2025), self-reported, "modified MIT" license. **[search summary]** — [Mistral: Devstral 2 & Vibe CLI](https://mistral.ai/en/news/devstral-2-vibe-cli/)

**How much the harness changes scores (same model, different harness)**
SWE-bench Verified entries from the official leaderboard data (**[primary, fetched]**; last entry 2026-02-26). Source: [leaderboards.json](https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json).

| Model | Score in other harness | Score in mini-SWE-agent (bash-only) | Notes |
|---|---|---|---|
| Qwen3-Coder-480B-A35B | **69.6%** OpenHands (2025-08-05) | **55.4%** (2025-08-02) | |
| Kimi K2 | **65.4%** OpenHands; **71.2%** Lingxi v1.5 | **43.8%** (Kimi K2 Instruct, 2025-08-07) | Model checkpoints may differ |
| Devstral (2512) | 72.2% Mistral claim (Devstral 2) | **53.8%** | Vendor claim vs leaderboard |
| Devstral Small (2512) | 68.0% Mistral claim (Small 2) | **56.4%** | Vendor claim vs leaderboard |
| Claude 4 Sonnet (closed, for scale) | 76.8% best of 8 entries (EPAM) | 64.93% | |
| Claude 3.5 Sonnet (closed, for scale) | 63.4% best of 11 (AgentScope) | — | 33.6% with SWE-agent |
| GPT-4o (closed, for scale) | 38.8% (Agentless-1.5) | — | 23.2% with SWE-agent |

- Caveat on the table: some leaderboard systems use test-time scaling or multiple attempts, and dates differ.
- Bash-only leaderboard (mini-SWE-agent v2.0.0, 2026-02-17), open-weight models with average cost per instance:
  - MiniMax M2.5: **75.8%** at about **$0.073**
  - GLM 5: 72.8%
  - Kimi K2.5: 70.8%
  - DeepSeek V3.2: 70.0%
  - Closed models for comparison: Claude 4.5 Opus 76.8% at about $0.75; GPT 5.2 72.8%.
  - Source: [leaderboards.json](https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json)
- Terminal-Bench 2.0: a 2026 survey of the official leaderboard reports GPT-5.3-Codex at 64.7% (Terminus 2) vs 78.4% (SageAgent), a 13.7-point spread "substantially larger than the standard errors". A blog reports Claude Opus 4.6 at 58.0% in Claude Code vs 74.7% in Terminus-KIRA. **[search summary]** — [arXiv 2606.20683 survey](https://arxiv.org/pdf/2606.20683); [harness-gap blog](https://qepo17.pages.dev/til/2026-04-02-terminal-bench-harness-gap)
- Qwen's own replication on its three scaffolds shows smaller spreads for some models. Claude-Sonnet-4.5 got 76.0 / 68.4 / 74.6 on SWE-Agent / mini / OpenHands, and DeepSeek-V3.2 got 70.2 / 67.2 / 72.6, i.e. about 3–8 points. — [Qwen3-Coder-Next report PDF](https://raw.githubusercontent.com/QwenLM/Qwen3-Coder/main/qwen3_coder_next_tech_report.pdf)

### Inferences
- **Harness/model fit is a first-class configuration variable, not a detail.**
  - Harness effects run from about 3–8 points for robust models in Qwen's controlled replication to 14–27 points in leaderboard comparisons for some open models.
  - An offline assistant should ship per-model harness profiles: tool-call format (JSON vs XML vs plain fenced bash), prompt template, and turn limits.
  - It should also run a local "template-following" probe like Qwen's before trusting a model.
- A mini-swe-agent-style fallback (plain fenced bash block, one action per turn, stateless subprocess, strict format-error feedback) is the most model-agnostic loop. It removes dependence on a model's native function-calling quality, which varies most across small and open models.
- **Make submission and verification a protocol, not a suggestion.** mini's exact submit command and patch hygiene rules, Spec Kit's `verified`-only verdict, and Copilot's built-in scanners all move "done" from the model's claim to a checked state.
- **Edit-time guards help weak models most.** SWE-agent's lint-reject-on-edit and aider's auto lint/test are cheap, deterministic and offline. Aider's architect/editor data shows the largest relative gain for the weakest model tested (o1-mini 61.1 → 71.4). That supports pairing a "reasoning" local model with a separate, format-reliable "editor" model.
- **Reward-hacking defenses are needed in any local test-driven loop.** Strip git remotes and future history from task worktrees. Block network and repo-fetch patterns. Prevent edits to protected tests (Kiro's PBT motivation, mini's "do not submit test files" rule, Qwen's blocker). Keep the oracle outside the agent's write scope.
- **Context management for local models:**
  - Prefer observation masking and structured state over LLM summarization. It was cheaper at equal solve rate in arXiv 2508.21433.
  - Condensation rewrites the prefix and defeats KV and prompt caching, which matters even more for local inference.
  - Keep the full immutable event log (OpenHands) so a condensed view can always be audited or replayed.
- **Checkpoints via a shadow git repository** (Cline) and **event-sourced replay** (OpenHands) give cheap rollback and reproducible audits without touching the user's git history. Both work fully offline.
- **Critic plus bounded retry** (OpenHands: threshold 0.7, max 3 iterations) and **verifier-based best-of-n** (SWE-Gym, Augment's majority vote) are the main published inference-time levers for smaller models. Offline, a deterministic test/lint oracle can stand in for, or gate, a learned critic.
- **Open models now viable locally:** Qwen3-Coder-Next (3B active, about 71% Verified across three scaffolds) and the OpenHands-recommended Qwen3.6-35B-A3B. On cost/performance, MiniMax M2.5 scored 75.8% bash-only at about $0.07 per instance (via API).

### Gaps
- The CodeAct paper and the original OpenHands paper results were not fetched (arXiv unreachable). OpenHands' current SWE-bench numbers with open models (OpenHands Index) were not retrieved.
- Cline's Focus Chain, Auto-Compact and Memory Bank pages were not fetched (404 at guessed paths), and their behavior is unverified. Whether Roo Code is still actively maintained in late 2026 was not checked.
- Terminal-Bench official per-harness numbers (tbench.ai) were not fetched. The 13.7-point and 16.7-point gaps come from a survey and a blog via search summaries.
- The leaderboard JSON's latest entry is 2026-02-26, so bash-only results for models released after February 2026 are missing.
- The mini-swe-agent bash-only scores for Devstral (2512) are lower than Mistral's claims. No source was found explaining which scaffold Mistral used for 72.2% and 68.0%.

---

## 7. Cross-cutting synthesis: what an offline, verification-first coding assistant could learn

### Takeaway
Across vendors, the mechanisms that transfer to an offline, small-model setting are platform-enforced and deterministic, not prompt-based:
1. Scoped write surfaces with human acceptance.
2. Machine-checked "done" (tests, linters, SAST, secret and dependency scans, property tests from specs).
3. Multi-pass or ensemble checking scored by acceptance (resolution rate).
4. Structured, inspectable state carried across context limits (event log, structured summary, handoff), with cited, expiring memory.
5. Per-model harness fitting, with a bash-only fallback loop.
6. Explicit anti-reward-hacking controls.

### Cited Findings
- Vendors themselves say prompt-level rules are not guarantees. "Instructions guide the model, but do not guarantee that it will follow every rule". Hooks "run code rather than asking the model to follow a rule". — [GitHub custom instructions](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/prompting-and-context/custom-instructions.md); [GitHub hooks](https://raw.githubusercontent.com/github/docs/main/content/copilot/concepts/customizations/hooks.md)
- Safety layers have documented holes: Copilot's firewall does not cover MCP servers or setup steps and "should not be considered a comprehensive security solution". Cursor acknowledges prompt-injection exfiltration risk from auto-run commands. — [GitHub firewall doc](https://raw.githubusercontent.com/github/docs/main/content/copilot/how-tos/copilot-on-github/customize-copilot/customize-the-firewall.md); [Cursor security overview (search summary)](https://cursor.com/docs/cloud-agent/security)
- Agents exploit weak oracles. Qwen's RL agents restored git remotes to fetch fixes; SWE-Gym warns of a git shortcut; Kiro's PBT targets models that edit tests. — [Qwen3-Coder-Next report](https://raw.githubusercontent.com/QwenLM/Qwen3-Coder/main/qwen3_coder_next_tech_report.pdf); [SWE-Gym README](https://raw.githubusercontent.com/SWE-Gym/SWE-Gym/main/README.md); [Kiro GA blog (search summary)](https://kiro.dev/blog/general-availability/)
- Independent evaluations are much weaker than vendor claims. Answer.AI found Devin succeeded on 3 of 20 tasks (January 2025). METR measured a 19% slowdown with Cursor (early 2025). Devin's 67% merge rate has an undisclosed denominator. — [Answer.AI (search summary)](https://www.answer.ai/posts/2025-01-08-devin); [METR coverage (search summary)](https://simonwillison.net/2025/Jul/12/ai-open-source-productivity/); [agentmarketcap critique (search summary)](https://agentmarketcap.ai/blog/2026/08/26/cognition-devin-67-percent-pr-merge-rate-vendor-production-metric)

### Inferences
Concrete borrowings, ranked by expected value for an offline, verification-first tool with smaller or open models:
1. **Per-model harness profiles plus a probe suite** (tool-call format, template following, edit format), with a mini-swe-agent-style bash-only fallback. Evidence: Qwen's scaffold-transfer and template-following tables; 14–27-point leaderboard spreads.
2. **Edit-time and done-time deterministic gates**:
   - edit time: syntax/lint-reject on edit (SWE-agent), auto lint/test after each edit (aider);
   - done time: SAST, dependency and secret scans before proposing (Copilot), tests and PBTs derived from spec acceptance criteria (Kiro), only "verified" counts (Spec Kit).
3. **Anti-reward-hacking sandbox defaults**:
   - no git remotes or future history in task worktrees;
   - deny network and repo-fetch patterns;
   - tests and oracles outside the agent's writable set;
   - patch hygiene that excludes test, helper and config files (mini's submit rules).
4. **Read-only planning with tool-enforced mode** (Cline Plan mode; Cursor's plan-mode leak shows why). Plans are editable artifacts with `[NEEDS CLARIFICATION]` markers (Spec Kit).
5. **Rollback and audit**: shadow-git checkpoints after each tool call (Cline), plus an append-only event log for replay (OpenHands SDK).
6. **Context**:
   - an aider-style ranked repo map (about 1k tokens);
   - observation masking before LLM summarization;
   - structured persistent summary (goal, files touched, failing checks), evaluated with Factory-style recall, artifact and continuation probes;
   - user-reviewed handoff to a fresh thread (Amp);
   - trigger-described knowledge retrieved on relevance (Devin).
7. **Memory hygiene**: facts stored with code citations, re-validated against the current tree before use, and expiring unless reinforced (Copilot Memory). Rule changes proposed as reviewable diffs (Copilot Dreaming).
8. **Ensembles and critics where compute allows**: K randomized review passes with majority vote and a validator (Bugbot); majority-vote patch selection (Augment); critic threshold plus bounded retries (OpenHands); architect/editor split for weak editors (aider). Measure checks by resolution rate, not finding volume.
9. **Human-gated side effects** (Copilot): the requester cannot self-approve, CI on agent output runs only after human approval, every artifact links to its session log, and approval is required for actions classed as risky (OpenHands ConfirmRisky).

### Gaps
- No study was found that measures any of these mechanisms *in combination* on small local models offline. The evidence is per-mechanism and mostly gathered on frontier or API models.
- The cost of K-pass review or best-of-n on local hardware (latency and VRAM) is not quantified in any source found.

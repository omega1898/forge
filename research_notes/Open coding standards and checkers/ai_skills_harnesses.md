# AI coding skills, rule packs, agent harnesses, evaluation harnesses, and the small-vs-frontier code quality gap

Research date: 2026-11-05. Method note: the GitHub REST API (api.github.com), arxiv.org, export.arxiv.org and api.semanticscholar.org were all blocked by this session's network policy. Licenses, READMEs, source files and leaderboard data were therefore read directly from raw.githubusercontent.com (main or master branch as of 2026-11-05). Paper results come from web-search summaries of arXiv, conference and secondary pages. Those figures are marked "(via search summary)" and were not checked against the full paper text. "Bundle offline" means the artifact can be copied into an offline tool and run without network access; licence obligations are noted separately.

## 1. Skill, rule and instruction packs for coding agents (and which licences allow bundling)

### Takeaway
Agent instruction formats have settled on a few plain-Markdown conventions:
- AGENTS.md, now under the Linux Foundation.
- SKILL.md folders, which follow the Agent Skills spec.
- Tool-specific rule folders: `.cursor/rules/*.mdc`, `.github/instructions/*.instructions.md`, `.continue/rules/*.md` and Aider's `CONVENTIONS.md`.

The largest packs that can be bundled are:
- awesome-cursorrules (CC0-1.0).
- github/awesome-copilot (MIT).
- The Apache-2.0 skills in anthropics/skills.
- anthropics/claude-plugins-official (Apache-2.0 at the root and for the plugins checked).
- Aider-AI/conventions (Apache-2.0).
- obra/superpowers (MIT).

Trail of Bits skills are CC-BY-SA-4.0, which is share-alike. Anthropic's document skills (docx/pdf/pptx/xlsx) are proprietary and cannot be bundled. Most of these packs are prompts, not deterministic checks. The pieces that matter for deterministic gates are hooks, regex pattern checks, LSP plugins and wrappers around Semgrep, SonarQube or CodeQL.

### Cited Findings

**Anthropic Agent Skills (github.com/anthropics/skills)**
- The repository has no root LICENSE file: LICENSE, LICENSE.md and LICENSE.txt all returned 404 on main and master. The README says "Many skills in this repo are open source (Apache 2.0)". It also says docx, pdf, pptx and xlsx are "source-available, not open source". — [anthropics/skills README](https://raw.githubusercontent.com/anthropics/skills/main/README.md)
- Licences are set per skill:
  - `skills/webapp-testing/LICENSE.txt` and `skills/mcp-builder/LICENSE.txt` are Apache 2.0.
  - `skills/docx/LICENSE.txt` begins "© 2025 Anthropic, PBC. All rights reserved."
  - [webapp-testing LICENSE](https://raw.githubusercontent.com/anthropics/skills/main/skills/webapp-testing/LICENSE.txt); [docx LICENSE](https://raw.githubusercontent.com/anthropics/skills/main/skills/docx/LICENSE.txt)
- The marketplace manifest defines these plugins:
  - `document-skills` (xlsx, docx, pptx, pdf).
  - `example-skills`: algorithmic-art, brand-guidelines, canvas-design, doc-coauthoring, frontend-design, internal-comms, mcp-builder, skill-creator, slack-gif-creator, theme-factory, web-artifacts-builder, webapp-testing.
  - `claude-api`, `academy-guide` and `discernment-nudge`.
  - [anthropics/skills marketplace.json](https://raw.githubusercontent.com/anthropics/skills/main/.claude-plugin/marketplace.json)
- Only `webapp-testing` concerns testing: a Playwright toolkit with a `scripts/with_server.py` server-lifecycle helper. This repo has no dedicated lint, code-review or static-analysis skill. — [webapp-testing SKILL.md](https://raw.githubusercontent.com/anthropics/skills/main/skills/webapp-testing/SKILL.md); [marketplace.json](https://raw.githubusercontent.com/anthropics/skills/main/.claude-plugin/marketplace.json)
- A skill is a folder with a `SKILL.md`. Its YAML frontmatter needs only `name` and `description`. — [anthropics/skills README](https://raw.githubusercontent.com/anthropics/skills/main/README.md)
- The spec file in the repo now just points to agentskills.io/specification. — [spec/agent-skills-spec.md](https://raw.githubusercontent.com/anthropics/skills/main/spec/agent-skills-spec.md)
- Secondary sources say Anthropic published Agent Skills as an open standard at agentskills.io in December 2025 and that OpenAI Codex, GitHub Copilot and others adopted it. Adoption counts range from "16+ tools" (March 2026) to "over 30" (April 2026), and the sources disagree on directory paths. These are blog sources, not primary ones. — [noqta.tn](https://noqta.tn/en/news/skill-md-open-standard-30-ai-coding-tools-adoption-2026); [codex.danielvaughan.com](https://codex.danielvaughan.com/2026/05/05/agent-skills-open-standard-portable-skills-codex-cli-cross-agent/)

**Claude Code plugins / official marketplace**
- `anthropics/claude-plugins-official` has an Apache-2.0 root LICENSE. — [LICENSE](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/LICENSE)
- `plugins/code-review/LICENSE` and `plugins/security-guidance/LICENSE` are also Apache-2.0. — [code-review LICENSE](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/code-review/LICENSE)
- Internal plugins live in `/plugins`. Third-party plugins live in `/external_plugins` or are referenced by git URL, so each carries its own licence. — [claude-plugins-official README](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/README.md)
- The marketplace manifest listed 315 plugin entries on 2026-11-05. Code-quality-relevant ones include:
  - Anthropic-internal: `code-review`, `pr-review-toolkit`, `security-guidance`, `claude-security`, `code-simplifier`, `hookify`, `feature-dev`, `code-modernization`, `claude-md-management`.
  - LSP plugins: clangd, gopls, pyright, typescript, rust-analyzer, jdtls, csharp, kotlin, lua, php, ruby, swift.
  - Third-party: `semgrep`, `sonarqube` ("7,000+ rules, secrets sca[nning]"), `coderabbit`, `qodo`, `greptile`, `aikido`, Endor Labs `ai-plugins`, `superpowers`, `mattpocock-skills` ("TDD, code review"), `playwright`, `serena`.
  - [claude-plugins-official marketplace.json](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/.claude-plugin/marketplace.json)
- `code-review` plugin:
  - Launches 4 parallel agents: two audit CLAUDE.md compliance, one scans for obvious bugs, one analyses git blame/history.
  - Scores each issue 0–100 and drops anything below 80.
  - [code-review README](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/code-review/README.md)
- `security-guidance` plugin has three layers:
  1. Regex warnings on Edit/Write for about 25 dangerous patterns, such as `yaml.load`, `torch.load(weights_only=False)`, `pickle.load`, `innerHTML` and hardcoded secrets.
  2. An LLM diff review on Stop.
  3. An agentic review on `git commit`.
  - Layers 2 and 3 need Claude credentials, so only layer 1 is deterministic and works offline. — [security-guidance README](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/security-guidance/README.md)
- `pr-review-toolkit` bundles 6 review agents covering comments, tests (behavioural vs line coverage), error handling, type design, code quality and simplification. — [pr-review-toolkit README](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/pr-review-toolkit/README.md)
- A Claude Code plugin is a folder with `.claude-plugin/plugin.json` plus optional `commands/`, `agents/`, `skills/`, `hooks/` and `.mcp.json`. — [claude-code plugins README](https://raw.githubusercontent.com/anthropics/claude-code/main/plugins/README.md)

**AGENTS.md**
- The agentsmd/agents.md repo (mirrored at openai/agents.md) is MIT, "Copyright (c) 2025 OpenAI". — [LICENSE](https://raw.githubusercontent.com/agentsmd/agents.md/main/LICENSE)
- AGENTS.md is "a simple, open format for guiding coding agents", a "README for agents". The sample includes test, lint and PR commands, for example "Fix any test or type errors until the whole suite is green." — [agents.md README](https://raw.githubusercontent.com/agentsmd/agents.md/main/README.md)
- On 9 Dec 2025, OpenAI, Anthropic and Block co-founded the Agentic AI Foundation under the Linux Foundation. Its launch projects were AGENTS.md, MCP and goose. — [OpenAI](https://openai.com/blog/agentic-ai-foundation); [InfoQ](https://infoq.com/news/2025/12/agentic-ai-foundation/)
- openai/codex is Apache-2.0. Its in-repo AGENTS.md doc now points to developers.openai.com/codex/guides/agents-md. — [codex LICENSE](https://raw.githubusercontent.com/openai/codex/main/LICENSE); [codex docs/agents_md.md](https://raw.githubusercontent.com/openai/codex/main/docs/agents_md.md)

**Cursor rules**
- PatrickJS/awesome-cursorrules is CC0 1.0 Universal (public domain dedication). — [LICENSE](https://raw.githubusercontent.com/PatrickJS/awesome-cursorrules/main/LICENSE)
- Cursor Project Rules are "Markdown-based `.mdc` files that live in `.cursor/rules/`". — [awesome-cursorrules README](https://raw.githubusercontent.com/PatrickJS/awesome-cursorrules/main/README.md)

**GitHub Copilot custom instructions**
- Copilot reads these instruction sources:
  - Repository-wide `.github/copilot-instructions.md`.
  - Path-specific `.github/instructions/*.instructions.md`, with an `applyTo` glob in the frontmatter.
  - Agent instruction files `AGENTS.md`, `CLAUDE.md` and `GEMINI.md`.
- An `excludeAgent` key can scope a file away from, for example, `code-review`.
- The docs say Copilot's choice between conflicting instructions is non-deterministic. — [GitHub Docs: add custom instructions (CLI)](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions); [GitHub Docs: custom instructions concepts](https://docs.github.com/en/copilot/concepts/prompting-and-context/custom-instructions) (via search summary)
- github/awesome-copilot is MIT ("Copyright GitHub, Inc."). It contains agents, instructions ("coding standards applied automatically by file pattern"), skills, plugins and a cookbook. Its README warns that the customizations come from third-party developers. — [LICENSE](https://raw.githubusercontent.com/github/awesome-copilot/main/LICENSE); [README](https://raw.githubusercontent.com/github/awesome-copilot/main/README.md)

**Aider conventions**
- Conventions go in a Markdown file such as `CONVENTIONS.md`, loaded with `/read CONVENTIONS.md` or `aider --read CONVENTIONS.md`. That marks the file read-only and lets prompt caching apply. — [aider conventions doc](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/docs/usage/conventions.md)
- Aider-AI/conventions is an Apache-2.0 community collection of `CONVENTIONS.md` files covering style rules, preferred libraries, type hints, testing and documentation conventions. — [LICENSE.txt](https://raw.githubusercontent.com/Aider-AI/conventions/main/LICENSE.txt); [README](https://raw.githubusercontent.com/Aider-AI/conventions/main/README.md)

**Continue.dev rules**
- Rules are Markdown files in `.continue/rules/`. Frontmatter supports `name`, `globs`, `regex` (include the rule when file content matches), `description` and `alwaysApply`. continuedev/continue is Apache-2.0. — [Continue rules doc](https://raw.githubusercontent.com/continuedev/continue/main/docs/customize/deep-dives/rules.mdx); [LICENSE](https://raw.githubusercontent.com/continuedev/continue/main/LICENSE)

**Third-party skill packs relevant to quality and security**
- trailofbits/skills is CC-BY-SA 4.0. Plugins include:
  - `static-analysis` (CodeQL, Semgrep, SARIF parsing), `semgrep-rule-creator`, `semgrep-rule-variant-creator`.
  - `c-review`, `rust-review` (SARIF output), `differential-review`.
  - `insecure-defaults`, `fp-check` (false-positive verification), `sharp-edges`, `variant-analysis`, `supply-chain-risk-auditor`.
  - `mutation-testing`, `property-based-testing`, `testing-handbook-skills` (fuzzers, sanitizers, coverage).
  - `constant-time-analysis`, `zeroize-audit`, `spec-to-code-compliance`, `writing-lean-proofs`.
  - [LICENSE](https://raw.githubusercontent.com/trailofbits/skills/main/LICENSE); [README](https://raw.githubusercontent.com/trailofbits/skills/main/README.md)
- obra/superpowers is MIT ("Copyright (c) 2025 Jesse Vincent"). Its skills:
  - `test-driven-development`, which enforces RED-GREEN-REFACTOR and "Deletes code written before" tests.
  - `requesting-code-review` ("Critical issues block progress"), `systematic-debugging`, `verification-before-completion`.
  - `writing-plans` (each task carries "verification steps").
  - [LICENSE](https://raw.githubusercontent.com/obra/superpowers/main/LICENSE); [README](https://raw.githubusercontent.com/obra/superpowers/main/README.md)

**Security risk of bundling rule files**
- Pillar Security's "Rules File Backdoor" (March 2025) hid malicious instructions in Cursor and Copilot rules files using hidden Unicode (zero-width joiners, bidi markers). The payload persists across forks. Cursor and GitHub both said reviewing suggestions is the user's responsibility. Pillar recommends blocking hidden Unicode in rules files. — [The Hacker News](https://thehackernews.com/2025/03/new-rules-file-backdoor-attack-lets.html); [Security Affairs](https://securityaffairs.com/175593/uncategorized/rules-file-backdoor-ai-code-editors-silent-supply-chain-attacks.html)

### Inferences
Bundling verdicts:
- **Free to vendor offline with attribution:** awesome-cursorrules (CC0), awesome-copilot (MIT), Aider-AI/conventions (Apache-2.0), superpowers (MIT), the AGENTS.md spec (MIT), and Anthropic's Apache-2.0 example skills and official internal plugins.
- **Trail of Bits:** changes to their skills must be shared under CC-BY-SA. Keep them as separate, unmodified files, or reference them instead of bundling.
- **Anthropic document skills:** cannot be bundled.
- **Third-party marketplace plugins (Semgrep, SonarQube and others):** check each licence; many also need a vendor service.

What can be gated deterministically:
- Most of these packs are natural-language guidance with no enforcement.
- Hook-based checks (the security-guidance regex layer, hookify), LSP diagnostics and wrapped SAST tools are deterministic.
- A tool can turn a rule pack into a gate by pairing each rule with a linter or Semgrep check where one exists, and treating the remaining prose only as model context.

Bundled rule files are a supply-chain surface. An offline tool should strip or flag non-printing Unicode and pin pack versions by hash.

### Gaps
- I could not fetch Cursor's official rules documentation; the `.mdc` format detail comes from the awesome-cursorrules README.
- Individual entries in awesome-cursorrules and awesome-copilot are community contributions. I did not check whether every file is covered by the repo licence or carries other terms.
- `continuedev/awesome-rules` returned 404, so I did not confirm whether Continue's rules hub has a separately licensed collection.
- I could not fetch the agentskills.io spec text, so the cross-tool adoption claims rest on blog sources.
- I did not verify per-plugin licences for every third-party entry in the 315-entry Claude marketplace.

## 2. Open agent harnesses and their quality-control loops

### Takeaway
All major open harnesses close the loop with deterministic signals. SWE-agent lints every edit and rejects any that introduce syntax errors. Aider auto-lints after each edit and can auto-run tests. Agentless re-ranks candidate patches with regression tests plus generated reproduction tests. AutoCodeRover uses spectrum-based fault localization from tests. SWE-agent, mini-swe-agent, OpenHands, Moatless Tools, Agentless and Aider are MIT or Apache-2.0. AutoCodeRover moved to the non-OSI Sonar Source-Available License in November 2024, so it should not be bundled.

### Cited Findings

**SWE-agent / mini-swe-agent (Princeton/Stanford)**
- SWE-agent is MIT, © 2024 Yang, Jimenez, Wettig, Yao, Narasimhan, Press. — [LICENSE](https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/LICENSE)
- The README says most current development is on mini-swe-agent and recommends using it instead. It claims mini-swe-agent "achieves 65% on SWE-bench verified in 100 lines of python" (July 2025). mini-swe-agent is MIT, © 2025 Lieret and Jimenez. — [SWE-agent README](https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/README.md); [mini-swe-agent LICENSE](https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/LICENSE.md)
- How the lint guard works in the `windowed_edit_linting` tool:
  - The edit command runs flake8 before and after the edit.
  - If new errors appear, it shows "Your proposed edit has introduced new syntax error(s). Please read this error message carefully and then retry editing the file", shows the before and after windows, and reverts the change.
  - [tools/windowed_edit_linting/bin/edit](https://raw.githubusercontent.com/SWE-agent/SWE-agent/main/tools/windowed_edit_linting/bin/edit)
- Paper results (NeurIPS 2024, arXiv 2405.15793):
  - With GPT-4 Turbo, SWE-agent solved 18.00% of SWE-bench Lite against 11.00% for a shell-only agent.
  - Removing the custom edit action (which includes linting) dropped the rate to 10.3%. That ablation figure comes from one secondary summary.
  - A separate linting-only ablation figure could not be confirmed.
  - [themoonlight review](https://www.themoonlight.io/review/swe-agent-agent-computer-interfaces-enable-automated-software-engineering); [hyper.ai](https://hyper.ai/papers/2405.15793) (via search summary)

**Aider**
- Aider is Apache-2.0. — [LICENSE.txt](https://raw.githubusercontent.com/Aider-AI/aider/main/LICENSE.txt)
- Linting and testing loop:
  - Built-in linters run by default on every edited file (`--no-auto-lint` disables this).
  - Custom linters are set with `--lint-cmd`, or per language with `--lint "language: cmd"`. A non-zero exit code plus stdout/stderr output means "errors to fix".
  - `--test-cmd` with `--auto-test` runs tests after each AI edit, and Aider tries to fix failures.
  - Formatters that exit non-zero should be wrapped in a run-twice script (`pre-commit run || pre-commit run`).
  - Compiled languages are handled through the lint or test command.
  - [aider lint-test doc](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/docs/usage/lint-test.md)
- The repo map is a graph-ranked summary of repository symbols. It fits a token budget set by `--map-tokens` (default 1k) and needs a tree-sitter `tags.scm` query per language. — [aider repomap doc](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/docs/repomap.md); [aider languages doc](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/docs/languages.md)

**Agentless (UIUC / OpenAutoCoder)**
- Agentless is MIT. — [LICENSE](https://raw.githubusercontent.com/OpenAutoCoder/Agentless/main/LICENSE)
- It works in three phases:
  1. Hierarchical localization: file, then class/function, then edit location.
  2. Repair: sample multiple candidate diffs.
  3. Patch validation: select regression tests, generate a reproduction test, and re-rank patches by the test results.
- Results:
  - 27.3% on SWE-bench Lite at about $0.34 per issue (July 2024).
  - 40.7% on Lite and 50.8% on Verified with Claude 3.5 Sonnet (Dec 2024).
  - [Agentless README](https://raw.githubusercontent.com/OpenAutoCoder/Agentless/main/README.md)

**AutoCodeRover (NUS, now Sonar)**
- The LICENSE is "SONAR Source-Available License v1.0, Last Updated November 13, 2024". It defines "Competing" use, so it is not an OSI open-source licence. Both AutoCodeRoverSG/auto-code-rover and nus-apr/auto-code-rover carry it. — [LICENSE](https://raw.githubusercontent.com/AutoCodeRoverSG/auto-code-rover/main/LICENSE)
- Method:
  - Program-structure-aware code search.
  - When a test suite is available, statistical (spectrum-based) fault localization, with regression tests run in Docker.
- Results: 19% on Lite (Apr 2024), 30.67% on Lite (Jun 2024), 46.20% on Verified (Nov 2024). The README contact addresses are @sonarsource.com. — [AutoCodeRover README](https://raw.githubusercontent.com/AutoCodeRoverSG/auto-code-rover/main/README.md)

**Moatless Tools**
- Moatless Tools is MIT, © 2024 Albert Örwall. — [LICENSE](https://raw.githubusercontent.com/aorwall/moatless-tools/main/LICENSE)
- The README reports Claude 4 Sonnet at 70.8% solve rate for $0.63 per instance. On the SWE-bench Verified Mini subset it reports claude-3-5-sonnet at 46% and claude-3-5-haiku at 28%. — [moatless README](https://raw.githubusercontent.com/aorwall/moatless-tools/main/README.md)
- The MCTS variant "SWE-Search" (arXiv 2410.20285) lives in moatless-tree-search. — [moatless README](https://raw.githubusercontent.com/aorwall/moatless-tools/main/README.md)

**OpenHands**
- OpenHands is MIT, "© 2025 OpenHands contributors". — [LICENSE](https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/LICENSE)
- The current README presents "agent canvas", which runs OpenHands, Claude Code, Codex, Gemini, OpenCode or any ACP-compatible agent on local, Docker, VM or cloud backends. It "runs locally on your machine by default". — [OpenHands README](https://raw.githubusercontent.com/All-Hands-AI/OpenHands/main/README.md)

### Inferences
Patterns an offline tool can copy:
- **Edit-time guard (SWE-agent):** lint before and after, reject the edit if it adds new errors. This is cheap, deterministic and language-generic if the linter is swapped.
- **Post-edit loop (Aider):** lint, then test, then feed the errors back.
- **Generate-many-then-filter (Agentless):** sample N patches and rank them by regression tests plus generated reproduction tests. Selection comes from tests, not from the model's own judgement, which suits weaker local models.
- **Test-based fault localization (AutoCodeRover):** worth reimplementing from the papers rather than reusing its code, because of the source-available licence.

### Gaps
- I could not read the SWE-agent paper's ablation table directly (arxiv.org blocked), so the 10.3% figure is unverified secondary data.
- I did not check whether the OpenHands repo has sub-directories (for example enterprise code) under different licences. The root LICENSE is MIT and does not mention "enterprise".
- I did not fetch the Agentless paper's ablation of reproduction-test re-ranking.

## 3. Code-generation evaluation harnesses (correctness, security, quality): licences and offline use

### Takeaway
- **Correctness harnesses:** SWE-bench (MIT), Multi-SWE-bench (Apache-2.0), EvalPlus (Apache-2.0), HumanEval (MIT), BigCodeBench (Apache-2.0), LiveCodeBench (MIT code), CRUXEval (MIT) and the Aider polyglot set (Exercism-derived). All run locally, mostly in Docker.
- **Security harnesses:**
  - Meta's CyberSecEval benchmarks and the Insecure Code Detector (MIT, regex plus Semgrep rules, 14 languages plus XML; MIT despite the repo's Llama licence at the root).
  - CWEval (Apache-2.0).
  - BaxBench (MIT, Docker exploits).
  - SecCodePLT (MIT dataset card, gated).
  - SecurityEval (licence not found).
- **Maintainability and smell benchmarks:** RACE, and SonarQube-based studies.

### Cited Findings

**Correctness and functional benchmarks**
- **SWE-bench**
  - MIT. — [LICENSE](https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/LICENSE)
  - Fully containerised Docker evaluation harness since June 2024.
  - SWE-bench Verified is a 500-problem subset confirmed solvable by engineers (Aug 2024).
  - SWE-bench Multimodal v2 is fully open source with 480 tasks for local evaluation (1 Sep 2026).
  - `sb-cli` and Modal are cloud options.
  - [SWE-bench README](https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/README.md)
- **Multi-SWE-bench (ByteDance Seed)**
  - Apache-2.0.
  - The mini subset has 400 instances in 8 languages (Apr 2025). The flash subset has 300 instances (Jul 2025). Multi-SWE-RL has 4,723 RL instances.
  - It evaluated nine models across Agentless, SWE-agent and OpenHands.
  - [LICENSE](https://raw.githubusercontent.com/multi-swe-bench/multi-swe-bench/main/LICENSE); [README](https://raw.githubusercontent.com/multi-swe-bench/multi-swe-bench/main/README.md)
- **EvalPlus (HumanEval+, MBPP+, EvalPerf)**
  - Apache-2.0.
  - MBPP+ v0.2.0 has 378 tasks (399 before broken tasks were removed). EvalPerf measures code efficiency. A Docker image is available.
  - [LICENSE](https://raw.githubusercontent.com/evalplus/evalplus/master/LICENSE); [README](https://raw.githubusercontent.com/evalplus/evalplus/master/README.md)
- **openai/human-eval** — MIT. — [LICENSE](https://raw.githubusercontent.com/openai/human-eval/master/LICENSE)
- **MBPP** — in google-research. Task IDs 601–974 are the training split. I did not confirm the dataset licence. — [mbpp README](https://raw.githubusercontent.com/google-research/google-research/master/mbpp/README.md)
- **LiveCodeBench**
  - MIT code. Problems are collected continuously from LeetCode, AtCoder and Codeforces for contamination control.
  - Releases: release_v1 had 400 problems (May 2023–Mar 2024); release_v5 had 880 problems (to Jan 2025).
  - Includes self-repair and code-execution scenarios.
  - [LICENSE](https://raw.githubusercontent.com/LiveCodeBench/LiveCodeBench/main/LICENSE); [README](https://raw.githubusercontent.com/LiveCodeBench/LiveCodeBench/main/README.md)
- **BigCodeBench**
  - Apache-2.0.
  - 1,140 tasks; BigCodeBench-Hard has 148. Complete and Instruct splits. A Docker evaluation image is available.
  - [LICENSE](https://raw.githubusercontent.com/bigcode-project/bigcodebench/main/LICENSE); [README](https://raw.githubusercontent.com/bigcode-project/bigcodebench/main/README.md)
- **CRUXEval (Meta)** — MIT. — [LICENSE](https://raw.githubusercontent.com/facebookresearch/cruxeval/main/LICENSE)
- **Aider polyglot**
  - 225 of the hardest of 697 Exercism exercises across C++, Go, Java, JavaScript, Python and Rust. — [aider polyglot post](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_posts/2024-12-21-polyglot.md)
  - The Aider-AI/polyglot-benchmark repo has no root LICENSE (404). It says "All exercise content is copyright © Exercism… used in accordance with Exercism's open source licenses". — [polyglot-benchmark README](https://raw.githubusercontent.com/Aider-AI/polyglot-benchmark/main/README.md)
  - The harness is in the Apache-2.0 aider repo. On each retry it feeds the unit-test error output back as the next instruction (`instructions = errors; instructions += prompts.test_failures`). — [benchmark/benchmark.py](https://raw.githubusercontent.com/Aider-AI/aider/main/benchmark/benchmark.py)

**Security benchmarks**
- **Meta Purple Llama**
  - The repo-root LICENSE is the Llama 3.2 Community License.
  - The README states that "evals and benchmarks are licensed under the MIT license while any models use the corresponding Llama Community license".
  - `CybersecurityBenchmarks/LICENSE` and `CodeShield/LICENSE` are both MIT, "Copyright (c) Meta Platforms".
  - [root LICENSE](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/LICENSE); [PurpleLlama README](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/README.md); [CybersecurityBenchmarks LICENSE](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CybersecurityBenchmarks/LICENSE); [CodeShield LICENSE](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/LICENSE)
- **Insecure Code Detector (ICD)** (in `CodeShield/insecure_code_detector`)
  - Analyzer enum: REGEX and SEMGREP.
  - Regex plus Semgrep: C, C++, C#, Java, JavaScript, Kotlin, Python.
  - Regex only: Hack, Objective-C, PHP, Ruby, Rust, Swift, XML.
  - Semgrep rules are in `rules/semgrep/` and run through the `semgrep-core` binary from the semgrep Python package.
  - [insecure_code_detector.py](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/insecure_code_detector/insecure_code_detector.py); [analyzers.py](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/insecure_code_detector/analyzers.py); [oss.py](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/insecure_code_detector/oss.py); [languages.py](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/insecure_code_detector/languages.py)
  - My own count of the regex rule files (`rules/regex/<lang>.yaml`) found about 45 distinct CWE IDs. Examples: c.yaml has 15 cwe_id entries, java.yaml 23, php.yaml 22, rust.yaml 13. — [rules/regex/c.yaml](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/insecure_code_detector/rules/regex/c.yaml)
  - A critique paper says CyberSecEval relies on "189 static analysis rules designed to detect 50 insecure coding practices" in CWE. It also finds that some prompts explicitly request the insecure practice. — [Rethinking CyberSecEval, arXiv 2411.08813](https://arxiv.org/html/2411.08813v1) (via search summary)
- **CodeShield**
  - Scans in two layers: a fast pattern pass, then a more thorough scan only when needed.
  - Meta says about 98% of production traffic is benign and about 99% of requests finish within about 70 ms.
  - [CodeShield README](https://raw.githubusercontent.com/meta-llama/PurpleLlama/main/CodeShield/README.md)
- **CWEval**
  - Apache-2.0. Evaluates functionality and security on the same tasks, with outcome-driven tests. Pre-built Docker image `co1lin/cweval`. To appear at LLM4Code 2025. — [LICENSE](https://raw.githubusercontent.com/Co1lin/CWEval/main/LICENSE); [README](https://raw.githubusercontent.com/Co1lin/CWEval/main/README.md)
  - Metrics are func@k, sec@k and func-sec@k. The authors report about a 30% gap between func@k and func-sec@k. — [CWEval arXiv 2501.08200](https://arxiv.org/pdf/2501.08200); [emergentmind](https://www.emergentmind.com/topics/cweval) (via search summary)
- **BaxBench (ETH SRI Lab / LogicStar.ai)**
  - MIT, © 2025. Each task pairs a backend scenario with a framework. Functional tests and security exploits run in Docker. v1.0.0 was released 13 Sep 2025. — [LICENSE](https://raw.githubusercontent.com/logic-star-ai/baxbench/main/LICENSE); [README](https://raw.githubusercontent.com/logic-star-ai/baxbench/main/README.md)
  - 392 tasks. In the original paper, flagship models did not exceed about 35% correct-and-secure, and roughly half of correct programs were exploitable.
  - An aggregator reports GPT-5 at 53.8% correct-and-secure; I could not confirm this on baxbench.com.
  - [ICLR 2025 page](https://iclr.cc/virtual/2025/33345); [alphaxiv](https://alphaxiv.org/benchmarks/eth-zurich/baxbench) (via search summary)
- **SecurityEval (s2e-lab)**
  - v2.1 has 121 prompts covering 69 CWEs; a prompt that deliberately asked for vulnerable code was removed. Distributed as `dataset.jsonl` plus a Hugging Face dataset.
  - No LICENSE or LICENSE.md was found on main.
  - [SecurityEval README](https://raw.githubusercontent.com/s2e-lab/SecurityEval/main/README.md)
- **SecCodePLT (Virtue AI and others)**
  - Hugging Face dataset card lists MIT. Access is gated (users must share contact info). The repository field says "Coming soon", so no code repo is confirmed. — [HF dataset card](https://huggingface.co/datasets/Virtue-AI-HUB/SecCodePLT) (via search summary)

**Code-quality and maintainability benchmarks**
- **RACE** (Readability, mAintainability, Correctness, Efficiency; arXiv 2407.11470, CAS)
  - Readability is scored on naming, line and function length, and comments. Maintainability uses the Maintainability Index plus modularity. Efficiency uses a normalized complexity index.
  - The authors conclude that code LLMs "exhibit significant room for improvement" beyond correctness.
  - Code is at github.com/jszheng21/RACE.
  - [alphaxiv 2407.11470](https://www.alphaxiv.org/abs/2407.11470v2) (via search summary)
- **SonarQube-based study** (Sabra, Schmitt, Tyler, arXiv 2508.14727)
  - Analysed 4,442 Java tasks from each of 5 models: Claude Sonnet 4, Claude 3.7 Sonnet, GPT-4o, Llama 3.2 90B and OpenCoder-8B.
  - Code smells made up 90–93% of issues, bugs 5–8% and vulnerabilities about 2%. 60–70% of vulnerabilities were BLOCKER severity.
  - [arXiv 2508.14727](https://arxiv.org/abs/2508.14727v1) (via search summary)

### Inferences
Offline bundling:
- All the correctness harnesses above can run fully offline once their Docker images and datasets are pre-pulled. SWE-bench-style repo tasks need large per-repo images.
- The ICD is the most directly reusable deterministic security gate: MIT, and only regex plus Semgrep. Because it targets "insecure practices" rather than proven vulnerabilities, it is a lint-level gate, not a verdict.
- BaxBench and CWEval give stronger signals because they run real exploits or security tests, but they only cover their fixed tasks.

### Gaps
- MBPP data licence, LiveCodeBench problem-text licence and SecurityEval licence were not verified.
- The Aider polyglot set inherits Exercism track licences, which I did not fetch.
- I did not verify the provenance and licence headers of ICD's Semgrep rule files. Some could derive from Semgrep's community rules, which have their own licence.
- I could not fetch NoFunEval or other maintainability benchmarks in time.
- I could not reach the live SWE-bench, EvalPlus and BaxBench leaderboards (swebench.com data JSON returned 404 on raw GitHub).

## 4. Evidence on the small/local vs frontier quality gap, and which deterministic techniques close it

### Takeaway
- **Correctness:** the gap is large. On Aider's polyglot benchmark (data last updated Oct 2025), local-class models (Qwen2.5-Coder-32B, gemma-3-27b, Codestral) score 5–16% against 72–88% for frontier models. Small models also break structured edit formats far more often.
- **Security:** model size buys little. Veracode found no meaningful gain from larger models. CyberSecEval found more capable models more prone to insecure suggestions. About 30–45% insecure outputs is typical across models.
- **Test-error feedback (repair loops):** roughly doubles to triples small models' pass rates, but the absolute gains are small. Self-repair is limited by the quality of the model's own feedback.
- **Deterministic feedback:**
  - Analyzer-driven loops cut flagged issues sharply (Bandit/Pylint loop on GPT-4o: security 40%→13%).
  - Iterative CodeQL/Bandit feedback raised GPT-3.5/GPT-4 vulnerability-repair success to 65.9–85.5%.
  - Constrained decoding with static analysis or types lets a 1.1B model beat text-davinci-003 on compilation, and more than halves compile errors.

### Cited Findings

**Correctness gap: Aider polyglot leaderboard data**
The data file has 69 entries, the latest dated 2025-10-03, so this is older data. pass_rate_1 is the first attempt; pass_rate_2 comes after one retry that sees the unit-test errors. — [polyglot_leaderboard.yml](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml); [benchmark.py retry logic](https://raw.githubusercontent.com/Aider-AI/aider/main/benchmark/benchmark.py)

Frontier and large models:

| Model | 1st try | After test feedback |
|---|---|---|
| gpt-5 (high) | 52.0% | 88.0% |
| o3-pro (high) | 43.6% | 84.9% |
| claude-opus-4 (32k thinking) | 37.3% | 72.0% |
| DeepSeek-V3.2-Exp Reasoner | 39.6% | 74.2% |
| Kimi K2 | 20.4% | 59.1% |
| gpt-oss-120b (high) | 13.8% | 41.8% |

Local-class models:

| Model | 1st try | After test feedback | Note |
|---|---|---|---|
| Qwen3 32B | 14.2% | 40.0% | |
| Qwen2.5-Coder-32B-Instruct ("whole" format) | 4.9% | 16.4% | |
| Qwen2.5-Coder-32B-Instruct ("diff" format) | 4.4% | 8.0% | only 71.6% of responses well-formed, 148 malformed |
| QwQ-32B | 8.0% | 20.9% | 67.6% well-formed |
| Llama 4 Maverick | 4.4% | 15.6% | |
| Codestral 25.01 | 4.0% | 11.1% | |
| openhands-lm-32b-v0.1 | 4.0% | 10.2% | |
| gemma-3-27b-it | 1.8% | 4.9% | |
| gpt-4o-mini | 0.9% | 3.6% | |

Source for both tables: [polyglot_leaderboard.yml](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml)

- Pairing a reasoner with a separate editor model helped: "QwQ-32B + Qwen 2.5 Coder Instruct" scored 9.8% → 26.2% with 100% well-formed responses, against QwQ-32B alone at 20.9% and 67.6% well-formed. — [polyglot_leaderboard.yml](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml)

**Correctness gap: SWE-bench Verified (aggregators, unverified)**
- Qwen3-Coder-30B-A3B at 60.4% is described as the best open model that fits in 24 GB at 4-bit. devstral-small-2512 is at 56.4%, and the original Devstral (2505) was 46.8%. — [llmrun.dev](https://llmrun.dev/benchmark/swe-bench-verified?show=all); [hackernoon](https://hackernoon.com/mistrals-devstral-beats-giants-on-swe-bench-sets-new-bar-for-open-weight-coding-models) (via search summary)
- A May 2026 blog puts the top open models near 80% (for example DeepSeek V4 Pro Max at 80.6%). A June 2026 post says OpenAI stopped reporting Verified scores in early 2026 over contamination concerns. Both claims are unverified. — [aitechconnect](https://aitechconnect-976.pages.dev/news/coding-agent-leaderboard-may-2026-claude-mythos-gpt55); [dev.to](https://dev.to/zyvop/the-best-open-source-llms-for-coding-right-now-june-2026-n10) (via search summary)
- The Moatless Verified Mini table shows claude-3-5-haiku at 28% against claude-3-5-sonnet at 46%. — [moatless README](https://raw.githubusercontent.com/aorwall/moatless-tools/main/README.md)

**Security does not scale with model size**
- **Veracode 2025 GenAI Code Security Report** (30 Jul 2025, updated Oct 2025)
  - 80 tasks across more than 100 LLMs. Models chose the insecure method 45% of the time.
  - "Larger models did not perform significantly better than smaller ones". Newer models became more syntactically correct but not more secure.
  - Java failed more than 70% of the time; Python, C# and JavaScript 38–45%.
  - [Veracode press release](https://www.veracode.com/press-release/ai-generated-code-poses-major-security-risks-in-nearly-half-of-all-development-tasks-veracode-research-reveals/); [Veracode report page](https://www.veracode.com/resources/2025-genai-code-security-report/) (via search summary)
- **CyberSecEval 1** (Dec 2023, 7 Llama 2, Code Llama and GPT models; older)
  - LLMs suggested vulnerable code about 30% of the time.
  - "Models with superior coding abilities were more susceptible to suggesting insecure code."
  - [arXiv 2312.04724](https://arxiv.org/html/2312.04724v1) (via search summary)
- **CyberSecEval 3** (2024): all tested models, including Llama 3 405B, failed insecure-autocomplete cases at about 31%. — [arXiv 2408.01605](https://arxiv.org/pdf/2408.01605v1) (via search summary)
- **CWEval:** within the GPT-4o and Gemini 1.5 families, smaller models are close (or slightly better) on func@k, but the gap on func-sec@k is larger. So smaller models can be functionally competitive while lagging on security. — [CWEval arXiv 2501.08200](https://arxiv.org/pdf/2501.08200) (via search summary)
- **Secure-tuning can backfire:** SafeCoder-tuned CodeLlama-7B reached only 7.1% Func-Sec@1 on CWEval, below its pre-trained base. — [Secure-Instruct arXiv 2510.07189](https://arxiv.org/pdf/2510.07189) (via search summary)
- **SonarQube study (2508.14727):** moving from Claude 3.7 Sonnet to Sonnet 4, functional performance improved but the share of BLOCKER bugs nearly doubled (7.1% → 13.71%). Hard-coded passwords and path traversal appeared across models, including OpenCoder-8B and Llama 3.2 90B. — [arXiv 2508.14727](https://arxiv.org/abs/2508.14727v1) (via search summary)
- **Sonar's companion report:**
  - Over 70% of Llama-3.2-vision:90b's vulnerabilities were BLOCKER, against 62.5% for GPT-4o and about 60% for Claude Sonnet 4.
  - One archetype had the highest issue density at 32.45 issues/KLOC; the source does not say which model.
  - These are vendor-published numbers.
  - [Sonar: coding personalities of leading LLMs](https://www.sonarsource.com/the-coding-personalities-of-leading-llms); [devops.com](https://devops.com/sonarsource-surfaces-multiple-caveats-when-relying-on-llms-to-write-code/) (via search summary)
- **Gong et al.** (SCAM 2025; arXiv 2408.10495; GPT-3.5, GPT-4, Code Llama, CodeGeeX2, Python, 67 CWEs): over 75% of generated code was vulnerable on SecurityEval. — [arXiv 2408.10495](https://arxiv.org/html/2408.10495v2) (via search summary)

**Effect of deterministic feedback and repair loops**
- **Static Analysis as a Feedback Loop** (Blyth, Licorish, Treude, Wagner; IEEE SCAM 2025, pp. 100–109; arXiv 2508.14419)
  - Iterative prompting with Bandit (security) and Pylint (readability, reliability, maintainability) on GPT-4o.
  - Within 10 iterations: security issues fell from over 40% to 13%, readability violations from over 80% to 11%, and reliability warnings from over 50% to 11%.
  - Earlier Bandit-feedback work (Alrashedy et al.) reported a 30.8% reduction.
  - [arXiv 2508.14419](https://arxiv.org/abs/2508.14419v1); [SMU repository](https://ink.library.smu.edu.sg/sis_research/10505) (via search summary)
- **Gong et al.**
  - With CodeQL and Bandit feedback driving iterative repair, success rose to 65.9–85.5% (GPT-3.5 or GPT-4 as generator and repairer).
  - Single-pass repair of other models' code was 33.2% (GPT-3.5) to 59.6% (GPT-4). Both models did poorly repairing their own code, which the authors call self-repair "blind spots".
  - [arXiv 2408.10495](https://arxiv.org/html/2408.10495v2) (via search summary)
- **Liu et al.** (ACM TOSEM 2024; 4,066 ChatGPT programs in Java and Python; Pylint, Flake8, PMD, Checkstyle)
  - ChatGPT self-repaired 20–60% of code-quality issues.
  - Static-analysis feedback was more effective for style and maintainability issues; simple feedback did better on the rest. The paper's text is inconsistent on this point.
  - [arXiv 2307.12596](https://arxiv.org/html/2307.12596v2); [replication package](https://github.com/yueyueL/ChatGPT-CodeGenAnalysis) (via search summary)
- **Is Self-Repair a Silver Bullet?** (Olausson et al., ICLR 2024; Code Llama, GPT-3.5, GPT-4 on HumanEval and APPS)
  - Once cost is counted, gains are "often modest", vary a lot, and are sometimes absent.
  - Self-repair is bottlenecked by the model's ability to give feedback on its own code. Feedback from a stronger model gave substantially larger gains, even where self-repair alone did not help.
  - [ICLR 2024 proceedings](https://proceedings.iclr.cc/paper_files/paper/2024/hash/9ddc141bdbf9d1db510cefff56c586ad-Abstract-Conference.html); [arXiv 2306.09896](https://arxiv.org/abs/2306.09896) (via search summary)
- **Guiding AI to Fix Its Own Flaws** (Yan et al., arXiv 2506.23034, June 2025): across proprietary and open-weight models of several sizes, stronger models can use vulnerability hints and fine-grained feedback to avoid or fix flaws. Per-size numbers were not obtained. — [arXiv 2506.23034](https://arxiv.org/abs/2506.23034v1) (via search summary)

**Constrained decoding (makes small models produce valid code by construction)**
- **Monitor-guided decoding** (Agrawal et al., Microsoft; NeurIPS 2023; arXiv 2306.10763)
  - Static analysis of repository context runs during decoding and masks the logits.
  - With MGD, SantaCoder-1.1B achieved a better compilation rate and next-identifier match than the much larger text-davinci-003, one generation each.
  - Evaluated on Java (PragmaticCode), with generalization to C# and Rust. Code is at microsoft/monitors4codegen.
  - [NeurIPS 2023 poster](https://neurips.cc/virtual/2023/poster/70362); [HF papers](https://huggingface.co/papers/2306.10763) (via search summary)
- **Type-constrained decoding** (Mündler et al., ETH/Berkeley; PLDI 2025; DOI 10.1145/3729274)
  - Uses prefix automata plus type inference, implemented for TypeScript.
  - Cuts compilation errors by more than half and improves functional correctness in synthesis, translation and repair, across model sizes including open models above 30B.
  - [PLDI 2025 page](https://pldi25.sigplan.org/details/pldi-2025-papers/25/Type-Aware-Constraining-for-LLM-Code-Generation); [ETH SRI](https://eth-sri.github.io/publications/muendler2025typeawareconstraint) (via search summary)
- **SynCode** (grammar-guided decoding from an EBNF CFG with a DFA mask store): removes 96.07% of syntax errors in generated Python and Go and eliminates JSON syntax errors. These are the authors' numbers; I found no independent replication. — [NSF PAR copy](https://par.nsf.gov/servlets/purl/10633733) (via search summary)

**Harness-level deterministic gates** (see Q2)
- The SWE-agent lint-and-revert edit guard was part of the component whose removal cut Lite from 18.0% to 10.3% (secondary). — [themoonlight review](https://www.themoonlight.io/review/swe-agent-agent-computer-interfaces-enable-automated-software-engineering)
- Agentless test-based re-ranking reached 27.3% on Lite at $0.34 per issue (2024). — [Agentless README](https://raw.githubusercontent.com/OpenAutoCoder/Agentless/main/README.md)

### Inferences
- **Correctness gap.** For local-class models (about 7–32B dense, 2024–25 generation), the polyglot gap is about 5–6x on first-try pass rate. Test-error feedback multiplies small-model pass rates about 2–3.5x, for example Qwen2.5-Coder-32B 4.9→16.4 and Qwen3-32B 14.2→40.0. But it adds only 3–26 points, against 35–41 points for frontier models. Repair loops narrow the relative gap but cannot close it alone.
- **Format reliability.** Small models' malformed edit rates (Qwen2.5-Coder-32B diff format at 71.6% well-formed; QwQ-32B at 67.6%) point to deterministic edit validation as a cheap, high-yield fix:
  - Whole-file or validated-diff formats.
  - Grammar or constrained decoding.
  - A separate editor model.
- **Security.** Size and capability do not reliably improve it, so a deterministic SAST gate (ICD-style regex/Semgrep, Bandit, CodeQL where licence allows) is needed for frontier and local models alike. Analyzer-driven iterative repair is the best-evidenced remedy, with roughly 3x reductions in flagged issues for GPT-4o-class models.
- **Repair-loop evidence for small local models is thin.** Olausson et al. and Gong et al. suggest weak models give poor self-feedback. Concrete analyzer or test output, or a stronger reviewer model, should replace self-critique.
- **Constrained decoding is the only family here with direct evidence that a small model can match or beat a much larger one** on a narrow metric (compilability or identifier validity). It needs decoding-level access, which a local-model tool usually has.
- **Correctness does not imply quality.** SonarQube studies show 90%+ of issues in correct LLM code are smells, so maintainability gates (Pylint/PMD/Checkstyle-class, plus complexity and duplication metrics) are a separate dimension from tests.

### Gaps
- I found no peer-reviewed study that holds the harness fixed and measures how analyzer-feedback repair loops scale with model size, especially for 7–32B local models. Most loop studies use GPT-3.5, GPT-4 or GPT-4o.
- arxiv.org was blocked, so every arXiv-derived number above comes from search summaries, not the paper PDFs. That includes the SWE-agent ablation, CyberSecEval, CWEval, the Bandit/Pylint loop, Gong et al., MGD, type-constrained decoding and SynCode.
- No authoritative late-2026 SWE-bench Verified or BaxBench leaderboard snapshot was obtained. The Aider polyglot data stops at Oct 2025, so newer local models (2026 Qwen, Devstral, Gemma generations) are not covered in the polyglot comparison.
- Per-model per-KLOC issue densities from Sonar's report, and per-size results from "Guiding AI to Fix Its Own Flaws", were not available.

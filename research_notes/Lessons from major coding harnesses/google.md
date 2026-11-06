# Google and Google DeepMind AI coding systems and harnesses: lessons for an offline, verification-first coding assistant

Research date: 2026-11-05. Access notes: in this session deepmind.google, arxiv.org, research.google, blog.google, jules.google, geminicli.com, developers.googleblog.com and most news sites could not be fetched (DNS blocked). I read these primary documents in full or in part: the AlphaEvolve white paper PDF (storage.googleapis.com), the ICSE-SEIP 2024 code-review paper PDF (storage.googleapis.com), the Gemini CLI README, LICENSE and `docs/` tree (sparse git clone of google-gemini/gemini-cli at commit f4df88a, dated 2026-11-05), the OSS-Fuzz-gen README, the jules-action README, and two Google Cloud blog posts on AlphaEvolve (Dec 2025, Jul 2026). Everything else rests on search-engine summaries. Each such claim is marked **[search summary]**.

## 1. AlphaEvolve (May 2025 onward): loop, evaluators, results, limits, later updates

### Takeaway
AlphaEvolve is an asynchronous evolutionary loop: program database → prompt sampler → LLM ensemble that emits diffs → user-supplied automated evaluator, with optional cascades → back to the database. Its strength and its stated limit are the same thing: it only works where a machine-checkable `evaluate()` exists. Google's production wins (0.7% of fleet compute, 23% kernel speedup / 1% Gemini training time, 32% FlashAttention speedup) came from problems framed so that correctness held by construction or was checked against a reference, plus held-out test sets and human expert sign-off. The system is described, not open source. Since 9 July 2026 it has been a generally available Google Cloud API, and the evaluator runs client-side.

### Cited Findings
**Architecture (white paper, May 2025)**
- The user provides an initial program with the regions to evolve marked by `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END`, plus evaluation code. The rest of the file is a fixed skeleton that ties the evolved pieces together. The initial code "must be complete" but "can be rudimentary" — [AlphaEvolve white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- The evaluator is a function h that maps a solution to "a set of scalar evaluation metrics", maximized by convention. It is typically "a Python function, called `evaluate`, with a fixed input/output signature, returning a dictionary of scalars" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- The controller loop, quoted from Fig. 2: `parent_program, inspirations = database.sample(); prompt = prompt_sampler.build(parent_program, inspirations); diff = llm.generate(prompt); child_program = apply_diff(parent_program, diff); results = evaluator.execute(child_program); database.add(child_program, results)` — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- LLMs emit SEARCH/REPLACE diff blocks. For very short code, or when a full rewrite fits better, they can be told to output the whole block instead — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Prompts contain:
  - previously discovered programs sampled from the database;
  - explicit context (human instructions, equations, code, literature PDFs);
  - "stochastic formatting" (template alternatives sampled for diversity);
  - rendered evaluation results (the program, its output and its scores);
  - "meta prompt evolution": LLM-suggested instructions co-evolved in a separate database.

  Source: [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- The ensemble is "Gemini 2.0 Flash and Gemini 2.0 Pro". Flash's lower latency "enables a higher rate of candidate generation", while Pro "provides occasional, higher-quality suggestions that can significantly advance the evolutionary search" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Evaluation options:
  - **Evaluation cascade (hypothesis testing).** Test-case ensembles of increasing difficulty; a solution reaches the next stage "only if [it achieves] sufficiently promising results in all earlier stages". New solutions are also "initially evaluated on a small scale … to filter out faulty programs early".
  - **LLM-generated feedback.** Properties that are hard to code, such as simplicity, are graded by separate LLM calls. The grade is either added to the score dictionary or used "to discard solutions when a criterion is not fulfilled".
  - **Parallelized evaluation.** It is "feasible to spend on the order of 100 compute-hours to evaluate any new solution". Evaluations are distributed asynchronously to an evaluation cluster.

  Source: [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Multiple metrics can be optimized at once. Google reports that "even if one metric is of particular interest, optimizing for multiple metrics often improves results for the single target metric" through diversity of inspirations — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- The program database is "inspired by a combination of the MAP elites algorithm and island-based population models". The pipeline is asyncio-based (controller, LLM samplers, evaluation nodes), "optimized for throughput (rather than the speed of any one particular computation)" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Compared with the predecessor FunSearch (Table 1): it evolves an entire file rather than a single function, uses "thousands of LLM samples" rather than millions, benefits from SOTA LLMs, and optimizes multiple metrics — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Ablations on tensor decomposition and kissing numbers removed, one at a time: evolution, prompt context, meta-prompt evolution, full-file evolution, and the strong model (small base LLM only). Each "is responsible for a significant improvement in the results" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)

**Results and how invalid solutions were guarded against**
- **Matrix multiplication.** Improved the state of the art for 14 matrix multiplication algorithms. Found a procedure for 4×4 complex-valued matrices using 48 scalar multiplications, "the first improvement, after 56 years, over Strassen's algorithm in this setting" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- **Math problems.** Applied to over 50 open problems. It matched the best known constructions on ~75% and surpassed them on ~20%, for example the Erdős minimum overlap problem and the kissing number in 11 dimensions. Results and verification code are in a public Colab (google-deepmind/alphaevolve_results) — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- **Data-center scheduling (Borg).** The heuristic scores machines for a pending job. Because it "only influences the ranking of machines already determined by Borg to be available and capable … the resulting scheduling decisions are effectively correct by construction."
  - Feedback came from a simulator built on historical fleet snapshots, and the heuristic was tested on an "unseen test dataset of recent workloads" to check generalization.
  - After fleet-wide rollout it "continuously recovers on average 0.7% of Google's fleet-wide compute resources". Post-deployment measurements "confirmed the simulator results".
  - It was chosen over deep RL for "interpretability, debuggability, predictability, and ease of deployment".

  Source: [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- **Gemini Pallas kernel tiling.** "The kernel's correctness is maintained by construction because AlphaEvolve is optimizing the tiling strategy … rather than altering its underlying mathematical operation." Input shapes were split half for training and half for evaluation. Results: "average 23% kernel speedup across all kernels over the existing expert-designed heuristic", "a corresponding 1% reduction in Gemini's overall training time", and optimization time cut "from several months of dedicated engineering effort to just days". The heuristic was deployed to production — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- **TPU Verilog arithmetic circuit.** "The final proposal must pass robust verification methods to confirm that the modified circuit maintains functional correctness". The bit-removal rewrite was "validated by TPU designers for correctness". Google concedes the improvement "was also independently caught by downstream synthesis tools" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- **FlashAttention XLA IR.** "Each modification … was checked against the reference (unmodified) code on randomized inputs in order to ensure numerical correctness". "The final version of the code was rigorously confirmed by human experts to be correct for all possible inputs." The FlashAttention kernel got 32% faster and pre/post-processing 15% faster — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)

**Limits Google states**
- "The main limitation of AlphaEvolve is that it handles problems for which it is possible to devise an automated evaluator". LLM-provided evaluation "is not a setting we have optimized for". Tasks "that require manual experimentation" are out of scope — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Self-improvement gains "are moderate and the feedback loops … are on the order of months" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Evolving code under programmatic evaluation "enables us to substantially sidestep LLM hallucinations" — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)

**Later updates (2025–2026)**
- **Nov 2025 math paper.** "Mathematical exploration and discovery at scale" by Georgiev, Gómez-Serrano, Tao and Wagner applied AlphaEvolve to 67 problems. It reports new constructions (for example hexagon packing, Heilbronn variants, finite-field Kakeya bounds) and lists the cases where AlphaEvolve did *not* reach the best known construction. It was most effective "at large scale across a loosely related portfolio of problems" — [arXiv 2511.02864](https://arxiv.org/abs/2511.02864); [Tao blog](https://terrytao.wordpress.com/2025/11/05/mathematical-exploration-and-discovery-at-scale/) **[search summary]**
- **Dec 2025: Google Cloud private preview.** Inputs are a problem definition, a customer-defined "ground truth" evaluator and "a compile-ready piece of code" as the seed. The page describes "Gemini models (Flash for speed, Pro for depth)" and limits scope to problems "that you can define in code and objectively measure" — [Google Cloud blog, 10 Dec 2025](https://cloud.google.com/blog/products/ai-machine-learning/alphaevolve-on-google-cloud)
- **July 2026: GA on Google Cloud** (dated July 9, 2026 on the page).
  - The evaluator is "a deterministic client-side evaluation script that compiles, tests, and scores the mutated candidates, returning one or more scalar metrics". A client-side runner fetches candidates from the AlphaEvolve API, evaluates them locally and posts the scores back.
  - Delivered as an API, as an AlphaEvolve "Skill" for IDEs such as Antigravity or Claude Code, and as a GitHub repo of Colab examples.
  - Customer results: Klarna "doubled throughput while improving model quality" over "nearly 6,000 candidate programs" in about three weeks; FM Logistic 10.4% better routing (over 15,000 km saved); Kinaxis more than 22% better forecasting accuracy and runtime cut by over 90%; Coolblue over 5% WMAPE improvement in 200 iterations; JetBrains 15–20% IDE performance.
  - Internal results: Spanner write amplification −20%; storage footprint −nearly 9%.
  - JetBrains: "Engineers still own the benchmark, review, and release decision."

  Source: [Google Cloud blog, GA](https://cloud.google.com/blog/products/ai-machine-learning/alphaevolve-is-available-for-everyone)
- Gemini Enterprise release notes say AlphaEvolve can generate candidates with Gemini 4 Argon, which becomes the default for new experiments on 26 Oct 2026, replacing Gemini 3.8 Flash — [Gemini Enterprise release notes](https://docs.cloud.google.com/gemini/enterprise/docs/release-notes) **[search summary]**

### Inferences
- Design pattern 1: make the edit surface small and correctness structural. In Borg the heuristic only re-ranks feasible machines; in the kernel work only tiling changes. Where that is impossible (FlashAttention IR, Verilog), Google used differential testing against the unmodified reference on random inputs, then formal or expert confirmation. An offline assistant can copy this directly: mark the evolvable regions, freeze the rest, and require a reference oracle.
- Design pattern 2: a held-out evaluation split (scheduling, kernels) guards against overfitting the evaluator, a form of reward hacking. The rollout also re-measured the result in production. Treat "evaluator score up" as a hypothesis until a held-out set, and then the real environment, confirm it.
- Design pattern 3: cascades put the cheap checks first (small-scale run, then harder tests, then the expensive benchmark). This maps onto parse → compile → unit tests → property/fuzz → performance benchmark.
- Design pattern 4: LLM judgment is advisory. It only adds a score or vetoes a candidate, and Google says it has not optimized for LLM-only evaluation.
- Design pattern 5: a fast/strong model ensemble tuned for throughput. Offline, the analogue is a small local model for bulk mutations and a larger local model, or a human-sent escalation, for occasional deep proposals.
- The Cloud GA design, where the vendor proposes and a deterministic evaluator on the customer side scores, is the clearest public statement that the verifier should live with the code owner. That arrangement is compatible with offline or air-gapped evaluation.
- The white paper does not discuss reward hacking explicitly. Its protections are architectural: by-construction framing, train/test splits, reference comparison and human confirmation.

### Gaps
- I could not read the DeepMind blog posts on deepmind.google, including any 2026 "impact" update. Search-summary claims of a "$500M"/"$1.225B" compute value and of new kissing or Strassen results came from low-quality sites and are **not** included.
- No primary source gives AlphaEvolve's evaluator anti-gaming features, pricing, or failure rates on GA customer tasks.
- I did not verify the exact failure counts per problem in the Tao et al. paper.

## 2. CodeMender (Oct 2025): finding and patching vulnerabilities, validation before humans, results, review policy

### Takeaway
CodeMender is a Gemini Deep Think–based agent. It combines static analysis, dynamic analysis, differential testing, fuzzing and SMT solvers for root-cause analysis, and it uses an LLM judge for functional equivalence that drives self-correction. Patches pass automatic checks (root cause, functional correctness, no regressions, style) before any human sees them. Every upstreamed patch is reviewed by a human researcher. Google reported 72 upstreamed fixes in six months at launch. The system is described, not released.

### Cited Findings
- DeepMind reports that over six months it "upstreamed 72 security fixes to open source projects, including some as large as 4.5 million lines of code" — [DeepMind blog](https://deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/) **[search summary; page not reachable]**
- Tools: Gemini Deep Think plus static analysis, dynamic analysis, differential testing, fuzzing and SMT solvers — [DeepMind blog](https://deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/); [SiliconANGLE](https://siliconangle.com/2025/10/06/google-deepmind-unveils-codemender-ai-agent-autonomously-patches-software-vulnerabilities/) **[search summary]**
- Validation checks "root-cause fixes, functional correctness, regressions, and style", "only surfacing for human review high-quality patches" — [DeepMind blog](https://deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/) **[search summary]**
- LLM judge loop: "the CodeMender agent modifies a function and then uses the LLM judge tool configured for functional equivalence to verify that the functionality remains intact. When the tool detects a failure, the agent self-corrects based on the LLM judge's feedback" — [DeepMind blog](https://deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/) **[search summary]**
- Multi-agent "critique" reviewers inspect semantic diffs and trigger self-correction on regressions — [SiliconANGLE](https://siliconangle.com/2025/10/06/google-deepmind-unveils-codemender-ai-agent-autonomously-patches-software-vulnerabilities/) **[search summary; secondary]**
- Proactive hardening: CodeMender applied Clang `-fbounds-safety` annotations to parts of libwebp. DeepMind says CVE-2023-4863 "along with most other buffer overflows in the project where we've applied annotations, would've been rendered unexploitable forever" — [DeepMind blog](https://deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/); [The Decoder](https://the-decoder.com/googles-codemender-is-designed-to-automatically-find-and-fix-security-flaws-in-software/) **[search summary]**
- Human review policy: "all patches generated by CodeMender are reviewed by human researchers before they're submitted upstream". CodeMender is a research effort; DeepMind hopes to release it to developers and promised technical papers — [SiliconANGLE](https://siliconangle.com/2025/10/06/google-deepmind-unveils-codemender-ai-agent-autonomously-patches-software-vulnerabilities/) **[search summary]**
- A French article dated 21 July 2026 says Google Cloud opened CodeMender in preview as an agent that finds flaws, "builds an exploit to prove them", and writes a fix — [SFEIR](https://www.sfeir.com/articles/codemender-google-agent-faille-correctif/) **[search summary; not confirmed by a primary Google source]**

### Inferences
- CodeMender stacks validators of different kinds: deterministic (build, tests, sanitizers, fuzzers), formal (SMT) and an LLM judge for equivalence. The LLM judge acts inside the loop as a fast signal, but humans remain the release gate. For an offline tool: run sanitizer/fuzz reproduction before and after the patch, add a differential test on non-crashing inputs, and treat an LLM equivalence opinion as a hint, never as proof.
- Hardening by annotation (`-fbounds-safety`) shows a second mode beyond "patch this bug": wholesale changes that remove a whole class of vulnerability. Those need especially strong regression evidence, because they touch many lines.

### Gaps
- No technical paper on CodeMender's architecture or validation pipeline turned up, despite the promise. There are no figures on rejection rate, false-positive patches or maintainer acceptance rate.
- The 2026 Cloud preview status is unverified.

## 3. Jules (asynchronous agent) and Gemini CLI (open source): tools, sandboxing, approvals, context, extensions, checkpointing, current status

### Takeaway
Jules ran each task in a fresh cloud VM: clone, install dependencies, plan with optional human approval, edit, run tests, then a critic loop, then a PR. Google announced on 29 Oct 2026 that Jules will shut down on 31 Dec 2026, going read-only on 11 Nov. Gemini CLI is Apache-2.0 and has a rich safety layer:
- a TOML policy engine with allow, deny and ask_user rules;
- approval modes default, auto_edit, plan and yolo, where YOLO can only be turned on by a command-line flag and admins can disable it;
- trusted folders with a "safe mode";
- shadow-git checkpointing with `/restore`;
- container, Seatbelt and gVisor sandboxes;
- hierarchical GEMINI.md context, MCP, hooks and extensions.

Since 18 June 2026 Gemini CLI is limited to enterprise Code Assist users, and individuals were moved to Antigravity CLI.

### Cited Findings
**Gemini CLI: status and license (read directly)**
- License: "fully open source (Apache 2.0)"; LICENSE file is Apache License 2.0 — [README](https://github.com/google-gemini/gemini-cli/blob/main/README.md); [LICENSE](https://github.com/google-gemini/gemini-cli/blob/main/LICENSE)
- Current README banner: "Free individual access and Google AI Pro / Ultra … moved to Antigravity CLI on June 18, 2026". Gemini CLI is "Designed for Gemini Code Assist Standard / Enterprise licenses and Code Assist for GitHub", is "Not tuned for the Gemini 4 series", and warns "Don't use Gemini CLI to benchmark Gemini models—its harness isn't tuned for Gemini 4+ and produces unrepresentative scores." — [README](https://github.com/google-gemini/gemini-cli/blob/main/README.md)
- The transition post says the new CLI keeps Agent Skills, Hooks, Subagents and Extensions, the last renamed "Antigravity plugins". The installer copies local configuration, including skills and MCP servers — [Google Developers Blog](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/); [GitHub Discussion #28017](https://github.com/google-gemini/gemini-cli/discussions/28017) **[search summary]**. Whether Antigravity CLI is open source is unconfirmed; one third-party guide calls it closed-source — [AI Builder Club](https://www.aibuilderclub.com/blog/google-kills-gemini-cli-june-18-2026) **[search summary, unverified]**

**Gemini CLI: harness features (read directly from repo docs)**
- Release channels: nightly (daily) → preview (weekly, Tuesday 23:59 UTC) → stable (weekly, Tuesday 20:00 UTC, "full promotion of last week's preview release + any bug fixes and validations") — [README](https://github.com/google-gemini/gemini-cli/blob/main/README.md)
- Release gates: Level 1 "must pass" covers lint, typecheck, unit tests and build on Linux and macOS; end-to-end tests on Linux, macOS and Windows, which "must pass with both `sandbox:none` and `sandbox:docker` on Linux"; and a post-publish smoke test. Level 2 requires the preview to be used by maintainers "for at least one week before being promoted to stable" — [docs/release-confidence.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/release-confidence.md)
- Behavioral evaluations ("Eval Development Kit") assert on agent behavior, such as which tools are called, call ordering and "avoiding destructive commands", "rather than checking the final prose output", because "Model responses are non-deterministic" — [docs/behavioral-evals.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/behavioral-evals.md)
- Built-in tools include `run_shell_command`, `glob`, `grep_search`, `read_file`, `read_many_files`, `write_file`, `replace` (old_string/new_string), `ask_user`, `write_todos`, `enter_plan_mode`/`exit_plan_mode`, tracker tools, `activate_skill` and `google_web_search` — [docs/reference/tools.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/tools.md)
- Approval modes:
  - `default` prompts for approval; `auto_edit` auto-approves edit tools; `plan` is read-only.
  - "YOLO mode (auto-approve all actions) can only be enabled via command line (--yolo or --approval-mode=yolo)".
  - `security.disableYoloMode` turns YOLO off, and an admin setting can disallow YOLO and "Always allow".

  Source: [docs/reference/configuration.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md)
- Plan Mode is "a read-only environment", "enabled by default". The agent "will stop and wait for your confirmation" before drafting a Markdown plan file, which the user can edit and then approve with automatic or manual edit acceptance — [docs/cli/plan-mode.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/plan-mode.md)
- Policy engine: TOML rules in `~/.gemini/policies/*.toml` with `toolName`, `commandPrefix`, `argsPattern` (a regex over stable JSON of the arguments), approval-mode conditions, a decision of `allow`, `deny` or `ask_user`, and an integer priority where the highest match wins. The example denies `rm -rf` — [docs/reference/policy-engine.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md)
- Trusted Folders is off by default. Once enabled, it shows a discovery-phase dialog listing project commands, MCP servers, hooks, skills and setting overrides, and flags dangerous settings such as auto-approval or a disabled sandbox. In an untrusted "safe mode" the CLI ignores workspace settings and `.env`, connects no MCP servers, loads no custom commands, and disables tool auto-acceptance — [docs/cli/trusted-folders.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/trusted-folders.md)
- Sandboxing:
  - Enabled with `-s` or `GEMINI_SANDBOX=true|docker|podman|sandbox-exec|runsc|lxc`.
  - macOS Seatbelt profiles range from `permissive-open` (the default: writes confined to the project, network allowed) to `strict-proxied`.
  - Docker/Podman mounts the project at the same absolute path; the default image is `ghcr.io/google/gemini-cli:latest`.
  - gVisor/`runsc` is Linux-only and must be set explicitly.

  Source: [docs/cli/sandbox.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md)
- Checkpointing: when a file-modifying tool is approved, the CLI commits a snapshot to a shadow Git repo at `~/.gemini/history/<project_hash>` and saves the conversation and the pending tool call. `/restore` reverts the files, restores the conversation and re-proposes the tool call. It is disabled by default, and the `--checkpointing` flag was removed in 0.11.0, so it is now settings-only — [docs/cli/checkpointing.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/checkpointing.md)
- GEMINI.md context loads in three layers: global `~/.gemini/GEMINI.md`, workspace and ancestor files, and "just-in-time" files found when a tool touches a directory, "up to a trusted root". All are concatenated into every prompt; `/memory show` displays the result — [docs/cli/gemini-md.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md)
- Hooks "run synchronously as part of the agent loop" (for example SessionStart) to add context, validate or block tool arguments, enforce policies and log activity. Extensions package "prompts, MCP servers, custom commands, themes, hooks, sub-agents, and agent skills". MCP servers are configured in `~/.gemini/settings.json` — [docs/hooks/index.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/index.md); [docs/extensions/index.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/extensions/index.md); [README](https://github.com/google-gemini/gemini-cli/blob/main/README.md)
- Headless mode: `gemini -p` with `--output-format json` or `stream-json` — [README](https://github.com/google-gemini/gemini-cli/blob/main/README.md)

**Jules**
- Public beta at I/O (20 May 2025); general availability on 6 Aug 2025 with free and paid plans. The free plan allowed 15 tasks per rolling 24 hours and 3 concurrent — [SiliconANGLE](https://siliconangle.com/2025/08/06/google-makes-jules-ai-coding-agent-available-everyone-free-paid-plans/) **[search summary]**
- Workflow: Jules "spins up a fresh dev environment in a VM, installs dependencies, writes tests, makes the changes, runs the tests, and opens a pull request". It shows its plan and reasoning before changing code — [Jules changelog](https://jules.google/docs/changelog/); [Google blog](https://blog.google/innovation-and-ai/models-and-research/google-labs/jules/) **[search summary]**
- The critic (Aug 2025) flags "subtle bugs, missed edge cases, and inefficient code". It is "not just another linter or test". It flags but does not fix, and loops until no issues remain. The initial version was single-pass, with possible future tool use such as a code interpreter or search — [Google Developers Blog](https://developers.googleblog.com/meet-jules-sharpest-critic-and-most-valuable-ally/); [SD Times](https://sdtimes.com/ai/googles-coding-agent-jules-get-critique-functionality/) **[search summary]**
- A "Planning Critic" reviews plans that do not need human approval before execution — [Jules changelog](https://jules.google/docs/changelog/) **[search summary]**. A 9.5% reduction in task failures attributed to it comes from a secondary source only and is unverified.
- jules-action README: Jules is "powered by Gemini 3 Pro" and "works autonomously in a cloud VM". GitHub Actions can call it through a Jules API key, for example a scheduled daily security-scan workflow — [jules-action README](https://github.com/google-labs-code/jules-action)
- Shutdown: a Jules changelog entry dated 29 Oct 2026 says Jules shuts down 31 Dec 2026, read-only from 11 Nov, with data export available. Its learnings "inform several Google products, including Antigravity" — [Data Studios](https://www.datastudios.org/post/google-jules-shutdown-december-31-read-only-november-11-antigravity); [Times of AI](https://www.timesofai.com/news/google-jules-antigravity/) **[search summary; secondary coverage of the official changelog]**

### Inferences
- The Gemini CLI policy engine is a good model for an offline harness. It uses declarative, priority-ordered, auditable rules over tool name and arguments, with `ask_user` as a first-class outcome, plus a separate approval-mode axis. Keeping YOLO out of persistent config (flag-only, admin-disableable) blocks a whole class of repo-planted escalation.
- Trusted Folders shows that repository content is itself an attack surface: settings, MCP configs, hooks and `.env`. Project configuration should be inert until the user grants trust.
- A shadow-git checkpoint before *every* approved write, kept outside the user's repo, plus a restore that re-proposes the tool call, gives cheap and reliable rollback. This is a prerequisite for letting an agent experiment.
- Behavioral evals that check tool calls and the absence of destructive commands, rather than prose, are the right way to regression-test an agent harness itself.
- Google's own README warns that a harness tuned for one model generation gives "unrepresentative scores" on the next. Harness and model are coupled, so an offline assistant should re-run its own eval suite whenever the local model changes.
- Product churn is a real risk. Jules was shut down about 16 months after GA, and Gemini CLI's consumer tier ended about 12 months after launch. An offline, self-hosted assistant avoids that dependency.

### Gaps
- I could not read jules.google docs. Missing details include Jules environment setup scripts and snapshots, memory, the Jules Tools CLI and the Jules API, how Jules ran tests, and how failures were reported.
- The jules-action license was not checked.
- Antigravity CLI's license and harness details are unverified.

## 4. Google's internal AI-assisted software engineering: migrations, ML-resolved review comments, DIDACT

### Takeaway
Google's internal pattern: deterministic discovery (Kythe, Code Search, AST tooling) finds what to change; a fine-tuned Gemini generates edits; automatic validation (parse, build, tests) with LLM-assisted repair follows; human code review is the final gate, and review becomes the bottleneck. Reported figures include 80% AI-authored code in the int32→int64 changelists, 87% of AI code committed unmodified in the JUnit migration, and roughly 50% estimated time savings. A later 39-migration study found 74.45% of code changes were LLM-generated. ML-suggested edits resolve 7.5% of all code-review comments, with reviewer preview and approval before authors see them.

### Cited Findings
**Migrations**
- "How is Google using AI for internal code migrations?" (Nikolov et al., arXiv 2501.06972, Jan 2025):
  - The system is a hybrid of LLM edit generation with deterministic AST techniques for discovery and validation.
  - The model is Gemini fine-tuned on internal code following the DIDACT methodology.
  - Discovery uses static analysis (Kythe, Code Search); validation checks build and test failures, with LLM-assisted repair.

  Sources: [arXiv 2501.06972](https://arxiv.org/html/2501.06972v1); [themoonlight review](https://www.themoonlight.io/en/review/how-is-google-using-ai-for-internal-code-migrations) **[search summary; arXiv blocked]**
- int32→int64 IDs: "80 percent of the code modifications in the change lists (CLs) were purely the product of AI", with migration time reduced by an estimated 50% — [The Register](https://www.theregister.com/2025/01/16/google_ai_code_migration/) **[search summary]**
- JUnit3→JUnit4: 87% of AI-generated code was committed without modification, and review rather than generation became the main bottleneck. Joda-Time→java.time: an estimated 89% time saving against projected manual time, with little supporting detail — [RDEL #127](https://rdel.substack.com/p/rdel-127-how-is-google-using-ai-for); [The Register](https://www.theregister.com/2025/01/16/google_ai_code_migration/) **[search summary]**
- "Migrating Code At Scale With LLMs At Google" (Ziftci et al., FSE 2025 industry track; arXiv 2504.09691):
  - 39 migrations by three developers over 12 months produced 595 code changes and 93,574 edits.
  - The LLM generated 74.45% of code changes and 69.46% of edits.
  - Developers estimated a 50% reduction in total time.
  - Validation covered completion, whitespace-only changes, parse errors, build breakage and test failures.
  - Reviewers of the paper note there is no controlled experiment and that the developers also built the system.

  Sources: [FSE 2025 listing](https://conf.researchr.org/details/fse-2025/fse-2025-industry-papers/14/Migrating-Code-At-Scale-With-LLMs-At-Google); [DX summary](https://getdx.com/research/migrating-code-at-scale-with-llms-at-google/) **[search summary]**

**ML-resolved code-review comments (read directly)**
- "At Google, we see millions of reviewer comments per year, and authors require an average of ∼60 minutes active shepherding time". "Code-change authors at Google address 7.5% of all reviewer comments by applying an ML-suggested edit", expected to save "hundreds of thousands of engineer hours annually" — [Frömmgen et al., ICSE-SEIP 2024](https://storage.googleapis.com/gweb-research2023-media/pubtools/7525.pdf)
- Predictions are shown only above a per-language confidence threshold tuned to a target precision. The threshold went from 70% to 50% after beta feedback, then to 40% once reviewers could preview suggestions. Reported recall@50 is "over 66%" — [paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/7525.pdf)
- V1 resolved 4.9% of all comments, with A/B weekly values of 3.9–5.5% at 50% of engineers. V2 resolves 7.5%, with weekly values of 5.4–7.5%, a peak of 9.4% in beta, and an SD of about 0.9 pp. By language: Java 9.5%, C++ 7.5%, Python 7.1% — [paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/7525.pdf)
- V2 funnel: about 63% of suggestions are accepted by the reviewer and attached to the comment; 34% of those are previewed by the author; 70% of previewed edits are accepted and applied. In V1, "over 45%" were accepted by the author — [paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/7525.pdf)
- Reviewer-in-the-loop was added because reviewers "were uncomfortable having an ML model 'interpret' their comment". In V2 the reviewer sees the edit while typing and can reject it; otherwise it reaches the author marked "Reviewed". "Reviewers only approved about half of the ML-suggested edits to their own comments." — [paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/7525.pdf)
- UX and latency failure modes:
  - Only 20% of V1 suggestions were previewed, rising to about 30% after a UI change.
  - Cutting the trigger delay from 1500 ms to 500 ms raised reviewer previews by 12% and author acceptance by 18%, at about 2× backend load.
  - Thumbs-down on previewed edits fell from 8.5% to 6.7% after targeted fixes.
  - An "incompatible code state" requires a three-way merge.

  Source: [paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/7525.pdf)

**DIDACT and overall AI share of code**
- DIDACT trains on the *process* of development rather than only the final code: edits, build logs, "solve sessions" from a broken build to its fix, and review comments and their resolutions. Tasks include repairing builds, predicting review comments and resolving review comments. Deployed tools are Comment Resolution, Build Repair and Tip Prediction — [Google Research blog, May 2023](https://research.google/blog/large-sequence-models-for-software-development-activities/) **[search summary]**
- "More than a quarter of all new code at Google is generated by AI, then reviewed and accepted by engineers" (Q3 2024 call) — [Engadget](https://www.engadget.com/ai/google-ceo-says-a-quarter-of-the-companys-new-code-is-already-ai-generated-180038896.html). In April 2026 this was 75% of new code, "AI-generated … later approved by human engineers", up from 50% in fall 2025 — [Semafor](https://www.semafor.com/article/04/24/2026/google-ceo-says-75-of-companys-new-code-is-ai-generated); [The Decoder](https://the-decoder.com/google-says-75-percent-of-its-new-code-is-now-written-by-ai/) **[search summary]**. One source's 30% figure for 2025 conflicts with the 50% figure.

### Inferences
- Google's internal pattern assigns roles clearly: deterministic tools decide *where* to change, the LLM decides *how*, build and tests decide *whether it works*, and humans decide *whether to ship*. For an offline assistant, the discovery step (indexer or AST queries) should be deterministic and reproducible, so that the scope of a change can be audited separately from its content.
- Once validation is automated, human review becomes the bottleneck. Lessons: keep changes small and single-purpose, attach evidence (build and test logs) to each change, and attribute each edit to one comment or reason. The code-review paper reports that developers prefer edits "attributable to a single comment".
- Confidence thresholds tuned to a target precision, per language, together with a cheap human veto, let Google show more suggestions safely. An offline tool could likewise gate auto-proposals by calibrated confidence and the strength of verifier evidence.
- "% of new code AI-generated" is always qualified with "reviewed/approved by engineers". Human approval remains the formal gate even at 75%.

### Gaps
- The full text of both migration papers could not be read. Exact definitions (for example, what counts as "fully AI-authored"), failure taxonomies and the false-positive rate of the validation stage are unverified.
- There are no public numbers on DIDACT build-repair acceptance from a primary source. The roughly 2% productivity figure came from a secondary summary and is omitted.

## 5. Big Sleep, OSS-Fuzz-Gen, AI-powered patching and the Chrome pipeline: validated automated finding and fixing

### Takeaway
Google's security agents pair LLM generation with execution-based oracles. OSS-Fuzz-Gen scores generated fuzz targets by compilability, crashes and coverage gain over human-written targets. The 2024 sanitizer-patching pipeline reproduces the bug, generates a fix, tests it and surfaces the best candidate to a human, and it fixed about 15% of targeted sanitizer bugs. By mid-2026 Chrome reported a Gemini pipeline with a fixing agent that drafts multiple patches, a critic agent and test-writing agents, credited with 1,072 fixes in Chrome 149 and 150. OSS-Fuzz-Gen is open source (Apache-2.0); Big Sleep and the Chrome pipeline are described only.

### Cited Findings
- OSS-Fuzz-Gen (Apache 2.0) generates fuzz targets for C/C++/Java/Python projects. They are "evaluated with four metrics": "Compilability", "Runtime crashes", "Runtime coverage", "Runtime line coverage diff against existing human-written fuzz targets". A 31 Jan 2024 experiment covered "1300+ benchmarks" from 297 projects and produced valid targets for 160 C/C++ projects, with a maximum line-coverage increase of 29%. The README lists "30 new bugs/vulnerabilities" — [OSS-Fuzz-Gen README](https://github.com/google/oss-fuzz-gen)
- A Nov 2024 report said AI-generated fuzz targets uncovered 26 new vulnerabilities, including OpenSSL CVE-2024-9143 — [SecurityWeek](https://www.securityweek.com/google-says-its-ai-found-sqlite-vulnerability-that-fuzzing-missed/) **[search summary]**. The README's count is now 30.
- "AI-powered patching: the future of automated vulnerability fixes" (Keller and Nowakowski, Google, 2024) describes a pipeline that finds bugs, isolates and reproduces them, generates LLM fixes, tests the fixes, and surfaces the best candidate for human review. It fixed about 15% of the targeted sanitizer bugs, which amounted to hundreds of bugs. A Google leader said the technology is far from reliably fixing 90–95% of bugs autonomously — [Google Research publication](https://research.google/pubs/ai-powered-patching-the-future-of-automated-vulnerability-fixes/); [TechTarget](https://www.techtarget.com/searchsecurity/feature/How-AI-driven-patching-could-transform-cybersecurity) **[search summary]**
- Big Sleep is a DeepMind and Project Zero agent:
  - Oct 2024: it found an exploitable SQLite stack buffer underflow that standard OSS-Fuzz configurations missed, because they do not enable `generate_series`.
  - July 2025: Google said Big Sleep found CVE-2025-6965, known to threat actors, before it was exploited.
  - Its first 20 reported bugs were mostly in projects such as FFmpeg and ImageMagick.
  - Google conceded that a target-specific fuzzer would likely be "at least as effective" for such bugs.

  Sources: [SecurityWeek](https://www.securityweek.com/google-says-its-ai-found-sqlite-vulnerability-that-fuzzing-missed/); [The Hacker News](https://thehackernews.com/2025/07/google-ai-big-sleep-stops-exploitation.html); [Google blog, summer 2025](https://blog.google/innovation-and-ai/technology/safety-security/cybersecurity-updates-summer-2025/) **[search summary]**
- Chrome (Chromium blog "Stronger with every update", 30 Jul 2026, not opened): a Gemini pipeline fixed 1,072 security bugs across Chrome 149 and 150 (June 2026), more than the prior 23 milestones combined. In the pipeline, "a fixing agent generates multiple candidate patches, a critic agent reviews them in a loop, and test-writing agents write cross-platform tests". It flagged a sandbox escape over 13 years old — [Tech Times](https://www.techtimes.com/articles/322383/20260731/chrome-ai-fixed-more-security-bugs-two-releases-prior-two-years.htm); [Chrome Unboxed](https://chromeunboxed.com/how-google-is-using-ai-to-find-triage-and-patch-chrome-vulnerabilities-faster-than-ever/) **[search summary; secondary]**

### Inferences
- The recurring recipe:
  1. Get a reproducer (crash input or sanitizer report).
  2. Generate *several* candidate patches.
  3. Check that each one compiles, that the reproducer no longer triggers, and that the existing tests still pass.
  4. Run a critic or LLM review.
  5. Add new tests.
  6. Send it to a human.

  Generating multiple candidates and selecting among them by execution evidence, as in AlphaEvolve and the Chrome pipeline, is more robust than single-shot repair.
- Coverage delta against an existing baseline (OSS-Fuzz-Gen) is a strong, cheap, objective metric for judging generated tests offline.
- Google's own figures (15% fix rate in 2024, "far from 90–95%") show that unattended fixing is still unreliable. That supports keeping human sign-off as the default gate.

### Gaps
- I did not read the Chromium blog post, so the pipeline's details (whether critic verdicts can block a patch, test pass criteria, human review rate) are unverified.
- There is no primary data on the false-fix rate for any of these systems.

## 6. What these reveal about verification-first design

### Takeaway
Across all of Google's systems, the generator is cheap and abundant, and trust comes from layered external checks that run in sequence:
1. Correct-by-construction framing.
2. Deterministic evaluators and cascades, from cheap to expensive.
3. Reference-differential testing.
4. Held-out sets.
5. LLM critics or judges as advisory filters.
6. Staged rollout with production re-measurement.
7. Human sign-off, which is never removed in any system Google describes.

### Cited Findings
- Correct by construction: in the Borg heuristic and the kernel tiling work, the search space excludes incorrect programs — [AlphaEvolve white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Cascade, with cheap filters first, is a first-class evaluator feature — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Differential testing against an unmodified reference on random inputs, then human expert proof for all inputs (FlashAttention IR) — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
- Staged rollout: from simulator, to held-out test set, to fleet-wide deployment, to post-deployment measurement — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf). Gemini CLI's own release process runs nightly → preview, with at least one week of dogfooding → stable, behind automated must-pass gates — [docs/release-confidence.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/release-confidence.md)
- Evaluator kept on the owner's side: the AlphaEvolve GA evaluator is "a deterministic client-side evaluation script that compiles, tests, and scores" — [Google Cloud GA blog](https://cloud.google.com/blog/products/ai-machine-learning/alphaevolve-is-available-for-everyone)
- LLM critics stay in the loop but do not get the final say:
  - In AlphaEvolve, LLM feedback is an optional score or veto — [white paper](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf).
  - CodeMender's equivalence judge drives self-correction, but humans review every patch — [DeepMind blog](https://deepmind.google/blog/introducing-codemender-an-ai-agent-for-code-security/) **[search summary]**.
  - Jules' critic flags issues and does not fix them — [Google Developers Blog](https://developers.googleblog.com/meet-jules-sharpest-critic-and-most-valuable-ally/) **[search summary]**.
- Human sign-off throughout:
  - CodeMender patches: reviewed by researchers before upstreaming.
  - Code review: the reviewer pre-approves suggestions ("Reviewed").
  - Migrations: human code review is the bottleneck.
  - AlphaEvolve customers (JetBrains): "Engineers still own the benchmark, review, and release decision".
  - Google-wide, 75% of new code is AI-generated "approved by human engineers".

  Sources: as cited in sections 2, 4 and 1.

### Inferences
These are recommendations for an offline, verification-first assistant.

- **Evaluator contract.** Require a declared, deterministic `evaluate()` (or build/test/lint/fuzz command set) that returns structured scalar metrics before any autonomous iteration. With no evaluator, the system should only *suggest*, AlphaEvolve's own stated limit.
- **Freeze the skeleton.** Use EVOLVE-BLOCK-style edit regions and edit guards so candidates cannot touch the evaluator, the tests or the harness. This guards against evaluator gaming. Google's public material does not discuss gaming, but its by-construction framings achieve this protection implicitly.
- **Cascade ordering.** Run parse/format, then compile, then fast unit tests, then the full suite, sanitizers and fuzzing, then benchmarks on a held-out set. Record each stage's verdict as evidence.
- **Multi-candidate generation with selection.** Use a small local model for volume and a larger model for occasional depth, and keep a database of scored candidates for diversity rather than a single linear chat.
- **Differential oracle.** For refactors, optimizations and security patches, compare against the pre-change program on generated inputs. Use SMT or formal tools where available.
- **Advisory critics.** An LLM critic may veto, add a score, or request a retry, but cannot certify correctness on its own.
- **Human gate with evidence.** Present small, single-purpose changes with attached logs. Track an attrition funnel (proposed → passed gates → previewed → accepted), as the code-review paper does, to calibrate thresholds.
- **Harness safety.**
  - Use a declarative allow/deny/ask policy with priorities.
  - Keep plan and read-only modes.
  - Allow no persistent "yolo".
  - Keep untrusted repo config inert.
  - Take shadow-git checkpoints before every write.
  - Run behavioral evals of the agent itself, re-run whenever the local model changes, given Google's warning that harness tuning is model-generation-specific.

### Gaps
- None of the Google sources quantify how often evaluators or critics were fooled. The anti-reward-hacking evidence is inferred from design choices, not from reported incidents.
- Google describes but does not release AlphaEvolve's database and prompt-sampler internals, CodeMender, Big Sleep, the internal migration tooling and DIDACT. Open-source pieces confirmed: Gemini CLI (Apache-2.0), OSS-Fuzz-Gen (Apache-2.0), and the AlphaEvolve results Colab and verification code. OpenEvolve and ShinkaEvolve are third-party re-implementations and were not examined here.

# Strata, the products like it, and a plan for FORGE

**Status: plan only. Nothing on this page is built yet.** Facts were checked on
6 November 2026 against each project's own repository: Strata's source was read
at commit `54828c7`, and star counts come from GitHub's search API on that day.
Speeds and benchmark results quoted below are what each project says about
itself; none were run here, because this build machine has no graphics card
(`nvidia-smi` is absent and `/dev/dri` does not exist).

## 1. Words used on this page

| Word | Meaning |
|---|---|
| Model / weights | The trained AI itself: a large file of numbers ("parameters"). Running it means multiplying your text, turned into numbers, through those weights. |
| Inference | Running a trained model to get an answer, as opposed to training it. |
| Inference engine | The program that does inference fast on your hardware. Most of its speed comes from *kernels*, small programs written for one kind of chip (CUDA for NVIDIA, HIP for AMD, Metal for Apple, SYCL for Intel). |
| GPU / VRAM | The graphics card and its own memory. It is very fast but small: 8–32 GB on consumer cards. |
| RAM | The computer's main memory. It is larger (32–256 GB) and slower for this work than VRAM. |
| SSD | The disk. It is larger still and slower again. |
| Token | A piece of a word. Models read and write tokens. "Tokens per second" is the speed. |
| Prefill / decode | Prefill means reading the prompt, which can be done in big parallel chunks. Decode means writing the answer, one token at a time. |
| Context window | How many tokens the model can see at once (Strata: up to 262,144). |
| KV cache | Memory the model keeps for every token already in the context, so it does not re-read them. It grows with the context. |
| Mixture of Experts (MoE) | A model built from many small sub-networks ("experts"). A *router* picks a few experts for each token, so most of the model sits idle for any one token. |
| Quantization / bpw | Storing each weight in fewer bits ("bits per weight") to shrink the file, at some cost in quality. Q2_0, IQ2_XS, IQ3_XXS and Q4_K are names of such formats. |
| GGUF | The model file format used by llama.cpp and by Strata. |
| Offloading / tiering | Keeping part of the model in slower memory (RAM, SSD) and moving or computing it there when needed. |
| Speculative decoding | A small, fast helper guesses the next few tokens and the big model checks them all in one pass. The answer is the same and arrives sooner. |
| MTP | "Multi-token prediction": a draft layer built into some models that can serve as that helper. |
| OpenAI- / Anthropic-compatible API | The model is served over HTTP in the same request format as OpenAI's or Anthropic's cloud. Tools written for those clouds then work locally by changing only the address. |
| Loopback / localhost / `127.0.0.1` | An address that only programs on the same computer can reach. |
| Harness | The program that drives a model to do work: it gives it tools, runs its edits and decides when it is finished. Claude Code, Codex CLI and FORGE are harnesses. |

## 2. What Strata is

[Strata](https://github.com/Niko1221/Strata) (MIT license, about 41,000 stars,
contact Niko Veit) runs one very large MoE model, **Qwen3.8-Flash-Next**, on an
ordinary gaming PC. It also runs a separate engine for DeepSeek V4.1-Flash and
supports Qwen3.6-35B-A3B. It is four parts that share one engine:

1. **The engine** (C++ with CUDA, HIP, Metal and SYCL kernels, partly built on
   llama.cpp's ggml library). This part does the work.
2. **The server** (`serve/server.py`) listens on `http://127.0.0.1:8080` and speaks
   three API dialects: OpenAI Chat Completions (`/v1/chat/completions`),
   Anthropic Messages (`/v1/messages`) and OpenAI Responses (`/v1/responses`).
   Claude Code, Codex CLI and OpenCode can therefore use it as if it were a cloud
   model.
3. **The Terminal (`strata`) and the desktop app (Tauri).** `strata launch` runs
   guided setup. It picks a model that fits your memory, downloads the engine
   built for your graphics card and starts the server. `strata connect` writes
   the settings your coding apps need. `strata bench` and `strata doctor`
   measure speed and check the installation.
4. **Signed updates.** An Ed25519-signed release feed is checked against a key
   already on your computer. Old or expired feeds are refused, and each engine
   package is checked by SHA-256 before Strata switches to it.

### How the engine fits a server-sized model on a home PC

The model has 48 layers of 512 experts each, 24,576 experts in total. Each token
uses 10 experts **per layer**. Strata's plain-language page says each word "needs
only 10 of them", but that is a simplification: per token the router chooses 10
in each of 48 layers, which is 480 experts. Strata splits the model's memory
across three tiers:

| Tier | What Strata keeps there | Why |
|---|---|---|
| VRAM | The parts every token needs (attention, routers, shared experts, output head, draft layer), the most-read part of the KV cache, and an **expert cache** that fills the remaining VRAM with the experts used most often in the current conversation | Every expert on the card is one the CPU does not have to compute |
| RAM | All 24,576 experts. The **CPU computes** the experts the card does not hold, in place, while the GPU works on the cached ones | Neither chip waits for the other |
| SSD | A 28.8 GB n-gram lookup table, of which only a few rows are read per token | Too big for RAM, and read too rarely to need it |

Two speed-ups come on top of the tiers. The first is guess-and-check: the model's
MTP layer drafts up to 3 tokens and one pass checks them, giving 2.4–3.2 tokens
per pass by Strata's own measurement. The second is prompt reading in chunks of
up to 8,192 tokens, with the next layer's experts streaming to the card while
the current layer runs.

**Arithmetic check (reproducible).** Strata's Q2_0 pack holds about 34 GB of
experts. 34 GB ÷ 24,576 experts ≈ 1.38 MB per expert, so 1 GB of extra VRAM holds
about 723 experts. Strata's documentation says "~700", which matches. Each token
touches 480 experts, about 664 MB of expert weights. That is why the split
between GPU and CPU, and not the GPU's raw speed, decides decode speed, and why
Strata says "more VRAM matters more than a faster GPU".

```bash
python3 -c "e=34e9/(48*512); print(round(1e9/e), 'experts per GB;', round(480*e/1e6), 'MB per token')"
```

### What Strata does unusually well (my reading of its docs)

- **It is built around one model**, not around every model. Its kernels, memory
  plan and draft layer all assume Qwen3.8-Flash-Next's exact shape. General
  runners cannot make those assumptions.
- **Its operations are disciplined in the same way FORGE's are.** Management
  actions are split into a *plan* and an *execute*, and the execute is pinned to
  the digests the plan showed. Outcomes are journaled. An interrupted job is
  reported as `completion_unknown`, never as "done". Startup downloads nothing;
  updating is a separate, explicit step. See its `docs/MANAGEMENT_PROTOCOL.md`
  and `SECURITY.md`.
- **It is honest about its limits.** It states that it has had no outside
  security audit, and that a first install by `curl | sh` trusts whoever controls
  the GitHub release.

## 3. Products like Strata

They fall into four families. The family matters more than the brand, because it
tells you what problem each one solves.

### Family 1: the same trick, running big MoE models by splitting memory across GPU, RAM and SSD

| Project | What it is |
|---|---|
| [KTransformers](https://github.com/kvcache-ai/ktransformers) (★19.7k, Apache-2.0) | A framework for mixed CPU/GPU inference, best known for running DeepSeek-class MoE models with the experts on the CPU |
| [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) (★3.4k, MIT) | A llama.cpp fork with extra quantization formats and faster CPU and hybrid paths, popular for MoE offload |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) itself | Can place expert tensors in RAM and the rest on the card. The tensor-override and MoE-to-CPU flags are from memory, so check `llama-server --help` |
| [PowerInfer](https://github.com/Tiiny-AI/PowerInfer) (★9.9k, MIT, last push May 2026) | Keeps frequently used ("hot") neurons on the GPU and the rest on the CPU. It is Strata's expert-cache idea at a finer grain |
| [AirLLM](https://github.com/lyogavin/airllm) (★41.9k) | Loads the model layer by layer from disk so it fits even a 4 GB card. It fits anything, but slowly |
| Research systems: [Fiddler](https://github.com/efeslab/fiddler) (ICLR 2025), [MoE-Infinity](https://github.com/EfficientMoE/MoE-Infinity) | The academic versions of CPU-GPU expert orchestration and expert caching |
| Other engines for the same model: [PolyStrata](https://github.com/simplexai-labs/PolyStrata) (★892, Strata's engine plus more models), [strata-nvfp4](https://github.com/sergqwer/strata-nvfp4) (fork), [basalt](https://github.com/jesdga95/basalt) (NVIDIA Blackwell), [slotstream](https://github.com/carloslfu/slotstream) (Mac, streams from SSD), [Infernix](https://github.com/Wallawalla47/Infernix) (RTX 5090), [Avalon](https://github.com/freriadoc/Avalon) (llama.cpp fork), [halogen-flash-server](https://github.com/peonist-ai/halogen-flash-server) (AMD Strix Halo) | Proof that this niche is crowded: at least eight public engines target the same model |
| Strata's credited sources: [Splash](https://github.com/incoai/splash) (Apple silicon), [ninfer](https://github.com/Neroued/ninfer) (single GPU), [HyperQwen](https://github.com/syv-ai/HyperQwen) (vLLM patches) | Where some of Strata's ideas came from |

### Family 2: the same product shape, with one-click install, many models and a local API, on a general engine

| Project | What it is |
|---|---|
| [Ollama](https://github.com/ollama/ollama) (★186k, MIT) | The best-known local runner: `ollama run <model>`, a model library and a local API |
| LM Studio ([`lms` CLI](https://github.com/lmstudio-ai/lms) ★5.4k; the app is closed source) | A desktop app for finding, downloading, chatting with and serving models |
| [Jan](https://github.com/janhq/jan) (★45.5k) | An open-source desktop chat app that runs offline |
| [llamafile](https://github.com/mozilla-ai/llamafile) (★26.6k) | A model and its engine packed into one executable file |
| [LocalAI](https://github.com/mudler/LocalAI) (★50.2k, MIT) | A drop-in replacement for the OpenAI API covering text, images, voice and more, CPU-friendly |
| [GPT4All](https://github.com/nomic-ai/gpt4all) (★77.3k) | An early desktop runner. Its last push was May 2025, so treat it as slowed |
| [Lemonade](https://github.com/lemonade-sdk/lemonade) (★6.1k) | A local server for models on your own GPUs and NPUs (AMD-backed, from memory) |
| [Foundry Local](https://github.com/microsoft/foundry-local) (Microsoft, ★2.6k) | Microsoft's on-device model runner |
| [Docker Model Runner](https://github.com/docker/model-runner) | Runs models from inside Docker's tooling |
| [llama-swap](https://github.com/mostlygeek/llama-swap) (★6.2k) | Not an engine: a switchboard that starts and stops engines as requests for different models arrive. Strata documents how to sit behind it |

### Family 3: data-centre servers, built for many users at once on big GPUs that hold the whole model

[vLLM](https://github.com/vllm-project/vllm) (★96.5k),
[SGLang](https://github.com/sgl-project/sglang) (★38.5k),
[TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM) (NVIDIA only).
Hugging Face's [TGI](https://github.com/huggingface/text-generation-inference)
is archived (last push March 2026). These servers optimise total throughput across many
users, not one person's speed on one card. Two close relatives target a single
consumer machine with the whole model in fast memory:
[ExLlamaV3](https://github.com/turboderp-org/exllamav3) with
[TabbyAPI](https://github.com/theroyallab/tabbyAPI) (AGPL-3.0) for NVIDIA cards,
and [MLX-LM](https://github.com/ml-explore/mlx-lm) for Apple silicon.

### Family 4: the foundation

[llama.cpp / ggml](https://github.com/ggml-org/llama.cpp) (★133.6k, MIT) is the
base most of family 2 is built on, at least for some models. Strata uses parts
of ggml, and the GGUF file format comes from it.

**The difference in one sentence.** Ollama-type runners make *any* model easy to
run if it fits. Strata makes *one* model fit that otherwise would not, then wraps
it in Ollama-grade convenience and FORGE-grade operational discipline.

## 4. Where FORGE stands next to Strata

They solve different halves of one problem. **Strata is the generator: it makes
a big model run. FORGE is the controller and verifier: it decides what the model
may see, runs its work in a sandbox, and computes "done" from evidence.** FORGE's
own research report (`reports/Lessons from major coding harnesses.md`) concludes
that FORGE is strong on the verifier side and weak on the generator side when
models are local. Strata is that generator side, built by someone else.

**FORGE can already talk to Strata.** `OpenAICompatibleAdapter`
(`forge/gateway/adapters.py:99`) posts to `/v1/chat/completions` and accepts only
loopback HTTP with an explicit port (`forge/gateway/adapters.py:107`). Strata's
default, `http://127.0.0.1:8080`, satisfies both. Reading both code bases (not a
live test) shows two mismatches:

1. **Token counting falls back to a guess.** FORGE asks `/tokenize`
   (`forge/gateway/adapters.py:147`). Strata has no such route; it counts tokens
   at `/v1/messages/count_tokens` (`serve/server.py:8282` in Strata). FORGE
   therefore uses `len(text) // 3 + 1`, marked `estimated`
   (`forge/gateway/adapters.py:154`), so its context budgets would be guesses
   against Strata.
2. **JSON output is requested, not enforced.** Strata describes its structured
   output as `"method": "prompt_and_validate"` (`serve/server.py:5758`). The schema
   is put in the prompt and checked afterwards; it is not enforced token by token
   by a grammar. FORGE validates every reply itself, so this stays safe, but
   expect more `malformed_json` and `schema_violation` call statuses than with a
   grammar-constrained server. The probes in L06 below measure how many.

## 5. Three ways to "be like Strata", and a verdict

| Option | What it means | Verdict |
|---|---|---|
| A. Write FORGE's own engine | Kernels per GPU family, an expert cache, speculative decoding | **Not now.** Strata alone is about 5,800 files tuned card by card, at least eight teams already compete on this one model, and it cannot be tested on this machine, which has no GPU. FORGE's advantage is verification, not kernels. A small simulator (L00 below) teaches the mechanism without this cost |
| B. Give FORGE Strata's product experience, managing an engine it does not write | Detect the PC, choose a model that fits, admit the engine and model by digest, start, verify and stop it as reviewed jobs, measure it, connect it | **Yes. This is the plan.** Almost every piece reuses something FORGE already has (platform records, digest admission, F07 jobs, F18 measurement, F24 signed bundles, D11 evaluation) |
| C. Turn FORGE into an inference runtime | Make serving models FORGE's main job | **No.** It would drop the part of FORGE nobody else has |

## 6. The plan

Each milestone is small, ships with tests that exercise its failure cases (the
repository's contributing rule), and says where it can be run.

| # | Milestone | What it does | Acceptance tests (failure cases first) | Runs where |
|---|---|---|---|---|
| L00 | Expert-cache simulator (learning track) | Pure Python. Replays a router trace (which experts each token used), simulates a VRAM cache of N experts under LRU, least-frequent and Strata-style policies, and reports hit rate and CPU work per token | Reproduces the 723-experts-per-GB arithmetic; a cache of 0 gives 0% hits; a cache holding every expert gives 100%; a corrupted trace is refused | Here |
| L01 | Hardware probe, `forge hw probe` | Records GPU vendor, model and VRAM, RAM, free disk, CPU features (AVX2, AVX-512) and OS as platform evidence, extending `forge/platforms` | Parses saved `nvidia-smi`, `rocm-smi` and `/proc` outputs; a missing GPU is reported as `none`, never guessed; hostile tool output (huge, non-UTF-8, injected text) is bounded and refused | Here, with fixtures |
| L02 | Fit calculator, `forge models fit` | Pure arithmetic. From a model's facts (layers, experts, active experts, bytes per expert, KV bytes per token) and the hardware record, predicts which tier each part lands in and whether it fits at a given context | Strata's published pack table is the test oracle (Q2_0 needs ~34 GB experts + ~6 GB; 32 GB of RAM needs the Coder); a model too big for RAM plus disk is refused with the shortfall in GB | Here |
| L03 | Engine and model admission | The engine binary and model files are admitted by digest, as compilers already are (`forge/packs/native.py`). `ModelConfig.weights_digest` is filled. The GGUF header gets a bounded read-only preflight, reusing the `gguf` reader in `forge/formats` | A changed file under the same name gets a new digest and must be probed again; a GGUF whose tensor offsets point past the end of the file is refused without loading | Here, with small fixture files |
| L04 | Managed engine lifecycle | Start, verify and stop Strata (`strata serve`), llama-server or Ollama as F07 jobs. Each is a reviewed plan pinned to digests, then an execute that rechecks them. The engine is loopback only, on an explicit port | A stale plan is refused; a crash mid-start is recorded `completion_unknown`; a second start of the same profile is refused; FORGE never enables Strata's own MCP tools | Here, with a fake engine; real engine on your PC |
| L05 | Gateway fixes | Count tokens through `/v1/messages/count_tokens` when the server offers it. Read the served model's identity from `/v1/models` or `/props` into the call record. Add a loopback-only Anthropic Messages adapter, kept separate from frontier escalation | Exact counts are labelled `exact`, fallbacks `estimated`; a non-loopback address is refused; the identity a server reports that differs from the admitted digest blocks the call | Here, against a fake server |
| L06 | Harness-fit probes | The probes FORGE's own report calls for: well-formed edit rate in each edit format, action-protocol adherence, honesty on an impossible task, and false "complete" claims, run through the real loop | Each probe has a fixture model that fails it, and the report says `failed`; results are keyed to the configuration digest | Here with fakes; real numbers on your PC |
| L07 | Speed evidence | Measures prefill and decode speed through F18's exclusive leases and records them next to the engine's own claims | A busy machine makes the measurement `inconclusive`; a run whose output differs from the reference is not counted | Your PC |
| L08 | One command, `forge launch` / `forge connect` | Chains L01–L07: probe, recommend, admit, start, verify, probe-fit, ready | Each step's refusal stops the chain with its reason; nothing is downloaded without an explicit update step | Here (Linux), then your PC |

**Recommended first step: L00, then L01 and L02.** All three are buildable and
testable on this machine. They teach the memory model behind everything else,
and they produce a tool anyone can use: "will this model run on my PC, and how?"

## 7. Trust boundaries

| Boundary | Threat | Planned control |
|---|---|---|
| Engine binary and GPU driver | Native code with full user rights that runs **outside** FORGE's sandbox, because FORGE's namespace sandbox has no GPU access | Admit by digest; run as a separate low-privilege user where possible; treat it as trusted code and say so in the platform record |
| Model files (GGUF) | Malicious files aimed at parser bugs in engines | Bounded preflight before any engine sees the file; only digests a person admitted |
| Local HTTP server | Web pages calling `127.0.0.1` (DNS rebinding, cross-site POST); other local users | Loopback only; an API key whenever the engine is started by FORGE; Strata's Host and Origin checks stay on |
| Engine-side tools (Strata's MCP) | The model calling tools with your rights, steered by injected text | Disabled for engines FORGE manages; tools stay in FORGE's scoped registry |
| Model output | Wrong, hostile or injected content | Unchanged: every reply is untrusted and validated (`forge/gateway/service.py`) |
| First install (`curl \| sh`) | A replaced release | Pin the engine by digest after a person's review; later updates through F24-style signature checks |

The claim to aim for once L03–L05 exist: *FORGE's use of a local engine is secure
against a hostile web page, a swapped model file and a hostile model reply,
assuming the engine binary, the GPU driver and the operating system are not
compromised.* It will **not** be secure against a malicious engine binary; no
current local runner is.

## 8. What the owner must understand personally

- The memory hierarchy (VRAM, RAM, SSD), and why bandwidth, not compute, limits
  decode speed. L00 and L02 are where this is learned.
- What MoE routing, quantization and speculative decoding trade away (quality,
  memory, verification cost).
- The API contract FORGE relies on: which endpoints, which fields, and what
  happens when a server only *claims* support.
- Every boundary in section 7, well enough to explain it to a customer's
  security reviewer.

## 9. Career and money (estimates are labelled)

- **Who would pay:** organisations that may not send code to a cloud model:
  defence, avionics, medical devices, banks. FORGE's compliance packs already
  target them. *Inference:* "local model plus verified evidence" is a sharper
  offer than either alone.
- **What they would buy:** first a fixed-price on-site setup and qualification
  ("this model on this hardware, measured, with an evidence report"), later a
  retainer for re-qualification when models change. *[GUESS: pricing is
  unverified; the next step is to find three published offers for on-premises
  AI qualification work before naming a price.]*
- **Portfolio evidence this produces:** a fit calculator anyone can run, a
  published harness-fit table for local models, and a written trust-boundary
  review of a 41k-star project. That review would be filed privately through
  Strata's own security process if it finds anything.

## 10. Questions for the owner before building

1. Which computer will run the real model? Give the graphics card, its VRAM, the
   RAM and free disk space. This decides whether Strata fits at all; Strata's
   guidance is 32 GB of RAM for the Coder pack and 64 GB for the main packs.
2. Is Strata the engine to target first, or llama-server or Ollama, which run
   more models but run this one less well?

## Sources

- Strata: [repository](https://github.com/Niko1221/Strata); read locally at
  commit `54828c7`: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/DETAILS.md`,
  `docs/MANAGEMENT_PROTOCOL.md`, `SECURITY.md`, `serve/server.py`
- Each project's GitHub repository, linked in its table row above; stars,
  licences and last-push dates from GitHub's search API, 6 November 2026
- FORGE: `forge/gateway/adapters.py`, `forge/gateway/contracts.py`,
  `forge/gateway/service.py`, `reports/Lessons from major coding harnesses.md`

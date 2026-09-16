# Keelson

**English** | [中文](README.zh-CN.md)

> A keelson is the structural beam that locks a ship's keel to its ribs. The model is the sail; Keelson is what carries that force through the rest of the hull.

A **self-evolving agent harness**, built from scratch in Python (~8k lines, no framework dependencies). The model only reasons and proposes tool calls; the runtime owns everything that actually touches the environment — **permission checks, tool execution, writing results back, context folding, and skill accumulation**.

No LangGraph / AutoGen — the loop itself is the point of the project (see [`docs/design-tradeoffs.md`](docs/design-tradeoffs.md)).

## Evidence, not design intent

The question this kind of project gets asked hardest is "how do you know it actually helps?" Three evidence chains, with the numbers and artifacts committed in the repo — not just described:

| | What was done | Result |
|---|---|---|
| **[ToolHop benchmark](bench/REPORT.md)** | A self-built four-arm comparison (raw API / bare harness / hand-written skill / self-evolved skill); n=60, paired bootstrap 95% CI | The benchmark caught a real bug in the harness itself (an injected prompt sent a small model into a tool-call loop). [Root-caused](bench/spiral-diagnosis.md), fixed, and re-measured: accuracy 72.8%→79.4%, turn-cap hit rate 8.3%→0.0%, CI on the delta excludes zero |
| **[Sandbox coding benchmark](bench/REPORT.md#sandbox-coding-benchmark)** | 14 real coding tasks (bugfix / feature / refactor / traceback-only / two adversarial traps), run once per model tier | Zero test tampering and zero out-of-scope edits across all 56 runs; caught one genuine "shipped without verifying" failure and re-ran it to confirm it wasn't a fluke |
| **[Self-evolution, live-fired](docs/self-evolution-demo.md)** | Drove two real skill-evolution lineages through the actual pipeline | One real **admit** (passed the gate, written live), one real **quarantine** (gate blocked it, live file rolled back, audited on an Evolution Card) — not simulated in a unit test, produced by the real pipeline |

`docs/design-tradeoffs.md` also carries a known-issues table — including two real defects found and left unfixed. Being honest about that is more convincing than looking flawless.

## What's inside

**Runtime (`agents/`)**

- **Agent loop** — streaming responses, tool-call parsing, the permission gate, tool execution, writing tool results back; OpenAI and Anthropic protocols
- **Permission gate (`tools.py::check_permission`)** — 5 modes + `settings.json` allow/deny rules + a dangerous-shell-command regex; a sub-agent's permissions never exceed its parent's; MCP and unclassified tools are denied by default
- **Context folding (`session_memory.py`)** — long tasks get their history rewritten into three structured slices (episodic / working / tool memory); three-tier compression (per-result budget → stale-result pruning → idle-time micro-compaction)
- **Loop guard** — stops the model when it keeps hammering one tool in a small window without the results converging; windowed on tool *name*, not arguments, because the failure mode actually observed was argument fuzzing, not exact repetition
- **Long-term memory** — isolated per project hash
- **Skills** — project- and user-level `SKILL.md`, BM25 retrieval, inline or forked execution
- **Skills self-evolution** — extracts reusable rules from the user's next-turn feedback, decides `add` / `merge` / `discard`, gated by a non-inferiority check
- **MCP** — a hand-rolled stdio JSON-RPC client; external tools are wrapped as `mcp__server__tool`
- **Sub-agents** — `explore` / `plan` / `general`, plus custom types

**Evidence layer (`agents/bench/`)**

- **ToolHop offline benchmark (`runner.py`)** — every task ships an executable tool body and a gold answer, so scoring is deterministic; four-arm comparison, train/test split, paired bootstrap 95% CI + McNemar
- **Non-inferiority promotion gate (`skill_gate.py`)** — a candidate skill may only go live if it doesn't regress against its own replay history; otherwise it's quarantined and rolled back — a quarantine event is audited exactly like an acceptance
- **Rollback + Evolution Card** — `/skill-diff`, `/skill-history`, `/skill-rollback`; every add/merge/quarantine produces one audit card (evidence → candidate → decision → diff → gate result → outcome)
- **Sandbox coding benchmark (`sandbox_runner.py`)** — a real Agent wired into a purpose-built grader, 14 tasks, two model tiers
- **Cost gating (`skill_gating.py`)** — a zero-model pre-filter so only feedback-shaped turns enter the expensive extraction path
- **Perf tracing (`trace.py`)** + **fold-fidelity probe (`fold_fidelity.py`)** (both built; measured numbers still pending, see [`PROGRESS.md`](PROGRESS.md))

Current status in [`PROGRESS.md`](PROGRESS.md); 183 tests, all mocked (no API key required).

## Architecture

![Keelson overall architecture](wiki/assets/architecture/01-overall-architecture.svg)

Core request path:

```text
user input
  -> agents/main.py
  -> Agent.chat()
  -> build prompt / retrieve skills / prefetch memory / init MCP
  -> call an OpenAI-compatible or Anthropic-compatible model
  -> model returns text or a tool call
  -> runtime runs the permission check + loop guard check
  -> execute tool / skill / MCP / sub-agent
  -> write the tool result back to the model
  -> save the session
  -> skill usage tracking + online skill evolution run in the background
```

## Directory layout

```text
keelson/
├── agents/
│   ├── main.py                    # CLI entry point, REPL, arg parsing
│   ├── agent.py                   # Agent runtime: model calls, tool dispatch, context compression, loop guard
│   ├── tools.py                   # built-in tools + the permission system
│   ├── paths.py                   # runtime path resolution ($KEELSON_HOME, project-local .keelson/)
│   ├── prompt.py                  # dynamic system-prompt assembly
│   ├── skills.py                  # skill loading, retrieval, execution, create/evolve wrappers
│   ├── online_skill_evolution.py  # online extraction + add/merge/discard decisions
│   ├── skill_evolution.py         # skill file writes, version snapshots, audit stats
│   ├── skill_gate.py              # non-inferiority promotion gate
│   ├── skill_history.py           # diff / rollback
│   ├── evolution_card.py          # audit-card rendering
│   ├── memory.py                  # long-term memory
│   ├── mcp_client.py              # MCP stdio JSON-RPC client
│   ├── subagent.py                # sub-agent config
│   ├── session.py                 # session save/resume
│   ├── trace.py                   # latency span tracing
│   ├── ui.py                      # terminal UI rendering
│   └── bench/                     # ToolHop / sandbox benchmarks, metrics
├── tests/                         # 183 tests, no API key required
├── bench/                         # benchmark tasks, splits, REPORT.md, run artifacts
├── docs/                          # design trade-offs, self-evolution live-fire record
├── wiki/                          # architecture diagrams and deep-dive docs (Chinese)
├── scripts/                       # data fetching, report generation, self-evolution demo
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Quickstart

### 1. Environment

Recommended:

- Python 3.11+
- macOS / Linux / Windows
- Git
- ripgrep (optional but recommended)
- An OpenAI-compatible or Anthropic-compatible model endpoint

Install:

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev,bench]"   # installs the `keelson` CLI entry point + test/bench deps
pytest -q                       # should read 183 passed, no API key needed
```

### 2. Configure `.env`

Copy `.env.example` to `.env` — the project reads `.env` from the current directory or a parent directory automatically.

Anthropic-compatible example:

```env
APIKEY=sk-your-api-key
API=https://your-host/anthropic
MODEL=claude-sonnet-4-6
```

OpenAI-compatible example:

```env
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://your-host/v1
MODEL=gpt-4o
```

Protocol detection:

- If `API` or `--api-base` contains `/anthropic`, it's called as Anthropic-compatible.
- Otherwise, if an OpenAI base URL is set, it's called as OpenAI-compatible.
- `--model` overrides `MODEL` from `.env`.

### 3. Start the REPL

```bash
python3 -m agents.main
```

Then just type a task, e.g.:

```text
Read this project and tell me how the agent loop actually runs.
```

### 4. Run a one-shot task

```bash
python3 -m agents.main "Summarize this project's directory structure and core modules"
```

### 5. Use Plan Mode

Plan Mode suits refactors, complex fixes, and multi-file changes: it does a read-only analysis and writes a plan first, then executes after you approve it.

```bash
python3 -m agents.main --plan "Analyze how skill retrieval should be optimized"
```

`/plan` also works inside the REPL.

### 6. Resume the last session

```bash
python3 -m agents.main --resume
```

## Skills self-evolution

Keelson's headline feature is **skill self-evolution**: it extracts durable, reusable rules from explicit user feedback, adds or merges them into project- or user-level `SKILL.md` files, and only lets a merge go live after it clears a non-inferiority gate — one real admit and one real quarantine, produced by the actual pipeline, are documented in [`docs/self-evolution-demo.md`](docs/self-evolution-demo.md).

### 1. Turn on auto-evolution

`KEELSON_AUTO_SKILL_EVOLUTION` defaults to on. To be explicit, put this in `.env`:

```env
KEELSON_AUTO_SKILL_EVOLUTION=1
KEELSON_AUTO_SKILL_TARGET=project
```

- `KEELSON_AUTO_SKILL_EVOLUTION=1`: enables online skill self-evolution
- `KEELSON_AUTO_SKILL_TARGET=project`: newly auto-created skills are written to the current project's `.keelson/skills/` (set to `user` to write to `~/.keelson/skills/` and share across projects)

### 2. Start in a permission mode that allows writes

Background skill writes need a permission mode that allows file writes:

```bash
python3 -m agents.main --accept-edits
```

### 3. Give feedback worth capturing

Self-evolution doesn't turn every sentence into a skill — only stable, explicit rules that will still apply to future tasks of the same kind. See `agents/skill_gating.py::looks_like_feedback` for the actual filter.

Not worth capturing (one-off task):

```text
Write me a 500-word government report.
```

Worth capturing (a forward-looking rule):

```text
From now on, government reports, work summaries, and research briefs should
default to a ready-to-use draft on the first pass, no back-and-forth questions;
structure them as title, background, current situation, problem analysis,
actions taken, next steps — formal, restrained tone.
```

### 4. How the pipeline works

```text
turn N: user task
  -> Agent produces a result
  -> save a pending extraction window
turn N+1: user feedback
  -> merged into the previous window
  -> online_ingest()
  -> Extractor pulls a candidate skill (only from the user's turn, never the model's own self-assessment)
  -> Maintainer decides add / merge / discard
  -> merge goes through the non-inferiority gate: the candidate must not regress against the skill's own replay history
  -> gate passes -> write SKILL.md + an Evolution Card
  -> gate rejects -> quarantine the candidate, roll back the live file, still write an Evolution Card (marked REJECTED)
```

Core files: `agents/agent.py`, `agents/online_skill_evolution.py`, `agents/skill_gate.py`, `agents/skills.py`, `agents/skill_evolution.py`. Run it for real: `python scripts/demo_self_evolution.py` (needs an API key; `--dry-run` doesn't).

## Evaluation

```bash
# ToolHop, four arms (each isolated, no cross-contamination)
python -m agents.bench.runner --arm raw_api   --n 60
python -m agents.bench.runner --arm no_skill  --n 60
python -m agents.bench.runner --arm skill_v1  --n 60
python -m agents.bench.runner --arm no_skill --dry-run    # build only, no model calls

# Regenerate the report tables from existing run artifacts
python scripts/bench_report.py

# Sandbox coding benchmark (no API key needed to just validate the tasks)
python -m agents.bench.sandbox_runner --validate
python -m agents.bench.sandbox_runner --arm harness --model <model-name>
```

- ToolHop dataset: `bench/toolhop_subset.json` (a fixed 120-task subset, enough to reproduce every number in `bench/REPORT.md`); pull the full dataset with `python scripts/fetch_data.py toolhop`
- `pytest -q` is fully green (183 tests, no API key needed, every model call mocked)

## More docs

- [ToolHop + Sandbox full evaluation report](bench/REPORT.md)
- [Self-evolution, live-fired: one real admit + one real quarantine](docs/self-evolution-demo.md)
- [Design trade-offs and known issues](docs/design-tradeoffs.md)
- [Build progress log](PROGRESS.md)
- [From zero to one — a guided tour of the project](wiki/从0到1学习了解项目.md) (Chinese)
- [Architecture design](wiki/架构设计.md) (Chinese)
- [Core source-reading guide](wiki/核心源码阅读指南.md) (Chinese)
- [Technical highlights](wiki/技术亮点.md) (Chinese)
- [Skills self-evolution: logic and implementation](wiki/Skills自进化逻辑与实现思路.md) (Chinese)

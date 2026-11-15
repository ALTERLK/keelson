# Keelson

**English** | [中文](README.zh-CN.md)

[![CI](https://github.com/ALTERLK/keelson/actions/workflows/ci.yml/badge.svg)](https://github.com/ALTERLK/keelson/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Live demo](https://img.shields.io/badge/demo-replay%20in%20your%20browser-f6c177)](https://alterlk.github.io/keelson/)

> A keelson is the beam that locks a ship's keel to its ribs. The model is the sail; Keelson is what carries that force through the rest of the hull.

A **self-evolving agent harness** in Python. The model only reasons and proposes tool calls. The runtime owns everything that touches the environment: **permission checks, tool execution, writing results back, context folding, and skill accumulation**.

There is no agent framework underneath (no LangGraph, no AutoGen). The loop is the project; see [`docs/design-tradeoffs.md`](docs/design-tradeoffs.md) for why.

[![Keelson console replaying a run in which the permission gate stops a destructive shell command](docs/assets/screenshots/console-en.png)](https://alterlk.github.io/keelson/)

## Try it

**In the browser, nothing to install:** <https://alterlk.github.io/keelson/> replays three recorded runs — a bug fix, the permission gate stopping `del`, and a skill being extracted from user feedback. A replay drives the console through the same code as a live run.

**Locally, one command:**

```bash
git clone https://github.com/ALTERLK/keelson.git
cd keelson
start.bat          # Windows (double-click works too)
./start.sh         # macOS / Linux
```

The script finds Python 3.11+, creates `.venv`, installs, and opens the console at <http://127.0.0.1:8765>. With an API key in `.env` it is live; without one it starts in replay mode, so a demo never depends on the network.

## Evidence, not design intent

The question a project like this gets asked hardest is "how do you know it helps?" Three evidence chains, with the numbers and artifacts committed in the repo:

| | What was done | Result |
|---|---|---|
| **[ToolHop benchmark](bench/REPORT.md)** | A four-arm comparison (raw API / bare harness / hand-written skill / self-evolved skill); n=60, paired bootstrap 95% CI | The benchmark caught a real bug in the harness itself: an injected prompt sent a small model into a tool-call loop. [Root-caused](bench/spiral-diagnosis.md), fixed and re-measured: accuracy 72.8% → 79.4%, turn-cap hits 15/180 → 0/180, CI on the delta excludes zero |
| **[Sandbox coding benchmark](bench/REPORT.md#sandbox-coding-benchmark--round-3)** | 14 coding tasks (bugfix / feature / refactor / traceback-only / two adversarial traps), two model tiers | Zero test tampering and zero out-of-scope edits across all 56 runs; one "shipped without verifying" failure caught and re-run to check it was not a fluke |
| **[Self-evolution, live-fired](docs/self-evolution-demo.md)** | Two skill lineages driven through the real pipeline against a real model | One **admit** (passed the gate, written live) and one **quarantine** (blocked, live file rolled back), each with an audit card |

The benchmarks page of the console recomputes these figures from the raw result rows:

![Benchmarks page](docs/assets/screenshots/bench-en.png)

Known defects are listed too, including two found and not yet fixed: [`docs/design-tradeoffs.md`](docs/design-tradeoffs.md).

## What's inside

**Runtime (`agents/`)**

- **Agent loop** — streaming, tool-call parsing, the permission gate, tool execution, writing results back; OpenAI and Anthropic protocols
- **Permission gate** (`tools.py::check_permission`) — five modes, `settings.json` allow/deny rules, destructive-shell-command detection; a sub-agent never exceeds its parent; MCP and unclassified tools are not trusted by default
- **Context folding** (`session_memory.py`) — long tasks get their history rewritten into three structured slices (episodic / working / tool memory), after two cheaper tiers (per-result budget, stale-result pruning)
- **Loop guard** — stops a model that keeps hammering one tool without the results changing; keyed on the tool *name* because the failure actually observed was argument fuzzing, not exact repetition
- **Skills** — project- and user-level `SKILL.md`, BM25 retrieval, inline or forked execution
- **Skill self-evolution** — extracts reusable rules from the user's next-turn feedback, decides `add` / `merge` / `discard`, and lets a merge land only if it clears a non-inferiority gate
- **Long-term memory** (per project), **MCP** (a hand-written stdio JSON-RPC client), **sub-agents** (`explore` / `plan` / `general`)

**Observability (`agents/events.py`, `agents/web/`)**

- **Event bus** — the loop publishes typed events instead of printing; the terminal, the web console and the trace recorder subscribe to the same stream
- **Web console** — streaming conversation, per-call gate verdicts, permission prompts answered from the browser, and a run inspector. Plain HTML/CSS/JS with no build step; SSE from server to browser. [`docs/web-console.md`](docs/web-console.md)
- **Trace recording and replay** — a run is saved as JSON lines and replayed through the same reducer that consumes the live stream

**Evidence (`agents/bench/`, `bench/`)**

- **ToolHop benchmark** — every task ships executable tool bodies and a gold answer, so scoring is deterministic; train/test split, paired bootstrap CI and McNemar
- **Sandbox coding benchmark** — a grader that restores the pristine tests before scoring, so tampering cannot buy a pass
- **Non-inferiority gate** (`skill_gate.py`), version history and rollback, and an Evolution Card for every add, merge and quarantine

216 tests, all runnable without an API key.

## Architecture

![Keelson overall architecture](docs/assets/architecture/01-overall-architecture.svg)

```text
user input
  -> Agent.chat()
  -> build prompt · retrieve skills · prefetch memory · connect MCP
  -> call the model
  -> tool call?  permission gate -> loop guard -> execute -> write result back -> loop
  -> save the session
  -> background: skill-usage tracking, skill extraction from the next user turn

every step above is published on the event bus
  -> terminal renderer · web console (SSE) · trace recorder
```

More in [`docs/architecture.md`](docs/architecture.md).

## Using it

### Install

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -e ".[dev,bench,web]"
pytest -q                             # 216 passed; no API key needed
```

### Configure a model

Copy `.env.example` to `.env`. It is read from the current directory or a parent.

```env
# OpenAI-compatible
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://your-host/v1
MODEL=gpt-4o

# or Anthropic-compatible
APIKEY=sk-your-api-key
API=https://your-host/anthropic
MODEL=claude-sonnet-4-6
```

A base URL whose path contains `/anthropic` is called with the Anthropic protocol; anything else with the OpenAI protocol. `--model` overrides `MODEL`.

### Command line

```bash
keelson                                   # interactive REPL
keelson "summarise this repo's modules"   # one-shot
keelson --plan "how should skill retrieval be optimised?"   # read-only analysis, then a plan to approve
keelson --resume                          # continue the last session
keelson --help                            # all options and REPL commands
```

### Web console

```bash
keelson web                # live; records the session to .keelson/traces/
keelson web --replay       # recorded traces only
```

It listens on loopback only and cannot switch the permission gate off; the reasons are in [`docs/web-console.md`](docs/web-console.md#security).

### Docker

```bash
docker compose up          # console on http://localhost:8765, agent works in ./workspace
```

### MCP servers

MCP is opt-in. Copy `.mcp.example.json` to `.mcp.json` (the servers in it need Node.js).

## Skill self-evolution

Keelson extracts durable rules from explicit user feedback, adds or merges them into `SKILL.md` files, and only lets a merge go live after it clears a non-inferiority gate.

```text
turn N    task -> answer -> remember the exchange
turn N+1  feedback that reads as a lasting rule
            -> Extractor  pulls a candidate out of the user's words (never the model's self-assessment)
            -> Maintainer decides add / merge / discard
            -> merge: replay the skill's own history against the candidate
                 not worse -> write SKILL.md, snapshot the old version, write an Evolution Card
                 worse     -> quarantine the candidate, restore the live file, write a REJECTED card
```

"Write me a 500-word report" is a one-off and is ignored. "From now on, reports open with a one-line summary and use formal language" is a rule and is captured; `agents/skill_gating.py::looks_like_feedback` is the filter.

```bash
keelson --accept-edits                     # background skill writes need a mode that allows edits
python scripts/demo_self_evolution.py      # the two-lineage demo (needs an API key; --dry-run does not)
```

The skill-evolution page of the console shows both recorded lineages with the gate's before/after figures and the diff. Full write-up: [`docs/self-evolution-demo.md`](docs/self-evolution-demo.md).

## Reproducing the evaluation

```bash
# ToolHop, one arm at a time (each isolated)
python -m agents.bench.runner --arm raw_api  --n 60
python -m agents.bench.runner --arm no_skill --n 60
python -m agents.bench.runner --arm skill_v1 --n 60
python -m agents.bench.runner --arm no_skill --dry-run     # build only, no model calls

python scripts/bench_report.py                             # regenerate the report tables

# Sandbox coding benchmark
python -m agents.bench.sandbox_runner --validate           # check the tasks; no API key
python -m agents.bench.sandbox_runner --arm harness --model <model-name>

# Re-record the demo traces
python scripts/record_demo.py --all
```

`bench/toolhop_subset.json` is a fixed 120-task subset, enough to reproduce every number in `bench/REPORT.md`. `python scripts/fetch_data.py toolhop` pulls the full dataset.

## Repository layout

```text
keelson/
├── agents/                 the package
│   ├── agent.py            the loop: model calls, tool dispatch, context compression, loop guard
│   ├── tools.py            built-in tools and the permission gate
│   ├── events.py           event bus between the runtime and its front ends
│   ├── ui.py               terminal renderer
│   ├── web/                web console: server, trace recorder, static front end
│   ├── skills.py · online_skill_evolution.py · skill_gate.py · skill_history.py · evolution_card.py
│   ├── memory.py · session.py · session_memory.py · mcp_client.py · subagent.py · prompt.py · paths.py
│   └── bench/              ToolHop and sandbox benchmark runners, metrics
├── bench/                  tasks, splits, REPORT.md, committed run artifacts
├── demo/traces/            recorded runs the console replays
├── docs/                   architecture, web console, design trade-offs, self-evolution record (en + zh)
├── scripts/                start script, demo recorder, static-site build, report generator
├── tests/                  216 tests
├── start.bat · start.sh    one-command start
└── Dockerfile · docker-compose.yml
```

## Documentation

| | English | 中文 |
|---|---|---|
| Architecture | [architecture.md](docs/architecture.md) | [architecture.md](docs/zh/architecture.md) |
| Web console, event bus, replay | [web-console.md](docs/web-console.md) | [web-console.md](docs/zh/web-console.md) |
| Design trade-offs and known issues | [design-tradeoffs.md](docs/design-tradeoffs.md) | |
| Self-evolution, live-fired | [self-evolution-demo.md](docs/self-evolution-demo.md) | |
| Skill evolution in depth | | [skill-evolution.md](docs/zh/skill-evolution.md) |
| Online skill evaluation | | [online-skill-eval.md](docs/zh/online-skill-eval.md) |
| Benchmark report | [bench/REPORT.md](bench/REPORT.md) | |
| Changelog and what is not done yet | [CHANGELOG.md](CHANGELOG.md) | |

## Acknowledgements

The tool set, permission modes, and the `SKILL.md` and `.mcp.json` conventions follow the design of Claude Code, which this project studies by re-implementing the harness in Python. The multi-hop benchmark uses the [ToolHop](https://huggingface.co/datasets/bytedance-research/ToolHop) dataset.

## License

[MIT](LICENSE)

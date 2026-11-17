# Feature guide

**English** | [中文](zh/features.md)

Every feature answers the same four questions: **what it is · what problem it
solves · how to use it · where you can see it**. If this is your first time,
read [Getting started](getting-started.md) first.

| I want to… | Section |
|---|---|
| control how much it may do without asking | [Permission gate and modes](#permission-gate-and-modes) |
| see a plan before anything changes | [Plan mode](#plan-mode) |
| stop it from looping and burning money | [Loop guard and budgets](#loop-guard-and-budgets) |
| run long tasks without it losing the thread | [Context management](#context-management) |
| have it work the way I like | [Skills](#skills) · [Skill self-evolution](#skill-self-evolution) |
| have it remember facts about me and the project | [Long-term memory](#long-term-memory) |
| keep several conversations and resume them later | [Sessions](#sessions) |
| check afterwards what it actually did | [Traces and replay](#traces-and-replay) |
| know where the time and money went | [Timing and cost](#timing-and-cost) |
| plug in external tools | [MCP tools](#mcp-tools) |
| hand part of a job to a helper | [Sub-agents](#sub-agents) |
| check that the install works | [doctor and init](#doctor-and-init) |
| look up a command or a setting | [Command reference](#command-reference) · [Environment variables](#environment-variables) |

---

## Permission gate and modes

**What it is.** Every tool call the model proposes goes through
`check_permission` before it runs, and gets one of three verdicts: allow, ask
you, or deny.

**The problem.** Models make mistakes, and can be steered by hostile text in a
file. The runtime cannot assume every proposed action is safe.

**How to use it.** Pick a permission mode:

| Mode | Edit existing files | Create files | Destructive commands | How to start |
|---|---|---|---|---|
| `default` | allow | ask | ask | default |
| `acceptEdits` | allow | allow | ask | `--accept-edits` |
| `dontAsk` | allow | deny | deny | `--dont-ask` |
| `plan` | deny | deny | deny | `--plan` or `/plan` |
| `bypassPermissions` | allow | allow | allow | `--yolo` |

Reading, listing and searching are allowed in every mode. A *destructive
command* is a shell command matching a list of patterns (`rm`, `del`,
`git push`, `Stop-Process`, …).

To allow or forbid specific calls, add rules to `~/.keelson/settings.json` (all
projects) or `.keelson/settings.json` (this project). Rules are checked before
the mode, and `deny` wins over `allow`:

```json
{
  "permissions": {
    "allow": ["run_shell(npm test*)", "run_shell(git status)"],
    "deny": ["run_shell(git push*)", "edit_file(.env)"]
  }
}
```

The pattern in parentheses is matched against the shell command or the file
path; a trailing `*` means "starts with", otherwise it must be equal.

**Where you see it.** The "gate: allowed / approved by you / denied" label on
every tool card; the three counters under *Permission gate* in the inspector;
the prompt that appears when it needs to ask.

**Worth knowing.**

- A denial is returned to the model as the tool's result, so the model knows
  why and can change course instead of stalling.
- Tools the gate does not recognise (MCP tools, tools added later) are not
  trusted by default: they need confirmation, and are refused in plan mode.
- A sub-agent never has more permission than its parent.
- The web console cannot select `bypassPermissions`.

## Plan mode

**What it is.** A read-only mode. The agent can read and search but cannot edit
files or run commands. It writes its plan to a file; only after you approve
does it leave plan mode and start working.

**The problem.** For a large change, reading the plan first is much cheaper
than rolling back afterwards.

**How to use it.** `keelson --plan "how would you refactor this module?"`, or
`/plan` inside a terminal session. Plan approval is CLI-only for now.

## Loop guard and budgets

**What it is.** Two safety nets. The loop guard looks at the last 8 tool calls:
if 6 or more are the same tool **and** their results have stopped changing, it
skips execution and tells the model to answer. Budgets stop the run when a turn
or cost limit is passed.

Four details, each changed after the [case study](case-study.md) exposed it:

- Shell commands are counted by program and first argument, not all as
  `run_shell`. Six `python -c` probes no longer block the `python -m pytest`
  that follows.
- Folding the context does not clear the window. It used to, and
  `compact_context` is a tool the model can call: one run used it to reset the
  guard.
- After three trips in one turn the runtime stops asking and closes the turn
  itself, with one call that carries no tools.
- An empty model reply (no text, no tool call) is retried, at most twice,
  instead of being read as "done".

What it does **not** catch: probing whose output differs a little each time.
The guard compares result hashes, so any change in the output counts as new
(humanize #205 failed this way in all three rounds of the case study). Only
the turn cap stops that.

**The problem.** Models sometimes get stuck in "try again with a slightly
different argument". The benchmark caught exactly that: 15 of 180 runs hit the
turn cap (see [`bench/spiral-diagnosis.md`](../bench/spiral-diagnosis.md)).

**How to use it.** The guard is on by default (`KEELSON_LOOP_GUARD=0` turns it
off). Budgets: `--max-turns 20`, `--max-cost 0.50`.

**Where you see it.** The *Loop guard* counter under *Runtime guards* in the
inspector; a notice appears in the conversation when it fires.

**Worth knowing.** It keys on the tool name, not the full arguments, because
the failure observed was argument fuzzing rather than exact repeats. Reading
six different files in a row never trips it: the results are changing.

## Context management

**What it is.** Three ways of keeping a long task inside the model's context
window, cheapest first:

1. **Per-result budget** — an oversized tool result is written to disk; the
   model gets a preview and a reference.
2. **Stale-result pruning** — at 60% of the window, old tool results are
   trimmed and the most recent kept.
3. **Folding** — at 70%, a side model rewrites the history into three
   structured slices: *episodic* (what happened), *working* (the goal and next
   actions) and *tool* memory (what worked, what failed).

![Context management in three tiers](assets/diagrams/context.en.svg)

**The problem.** Long tasks outgrow the window. Truncating makes the model
forget the goal; summarising everything is expensive and loses detail.

**How to use it.** It is automatic. `/compact` forces a fold.

**Where you see it.** The *Context window* bar and the *Context folds* counter
in the inspector.

## Skills

**What it is.** A skill is a `SKILL.md` file with front matter that says how a
kind of task should be done. At the start of each turn skills are retrieved by
relevance (BM25); a matching skill is injected into the prompt, or run in a
forked sub-agent.

**The problem.** "This is how we always do it" should not need repeating.

**How to use it.** Put the file at `.keelson/skills/<name>/SKILL.md` (project)
or under `~/.keelson/skills/` (user). A minimal example:

```markdown
---
name: commit-message
description: Write a conventional commit message for the staged changes
when_to_use: The user asks to commit, or asks for a commit message
---

1. Run `git diff --staged` to see what changed.
2. Write one summary line under 72 characters, in the imperative.
3. Add a body only when the reason is not obvious from the diff.
```

`/skills` lists them; `/<skill-name> arguments` runs one directly.

**Where you see it.** *Skills in this workspace*, at the bottom of the Skill
evolution page.

## Skill self-evolution

**What it is.** Durable feedback you give in conversation ("from now on,
always…") is extracted into a skill automatically. An update to an existing
skill has to pass a **non-inferiority gate**.

**The problem.** Nobody maintains hand-written rules; but letting a model
rewrite its own rules freely is risky — a rule distilled from one conversation
can quietly break what the older version got right.

**The pipeline.**

![How a skill changes](assets/diagrams/evolution.en.svg)

1. **Pre-gate**: does the message read as durable feedback? (Rules only, no
   model call; filters out most ordinary messages.)
2. **Extractor**: one candidate rule, taken from **the user's own words**. The
   evidence is the user, never the model's opinion of its own answer.
3. **Maintainer**: against the existing skills, decide add, merge or discard.
4. **Non-inferiority gate** (merges only): replay the skill's own past cases on
   the old and the new version. Hard failures must not rise; the pass rate must
   not fall.
5. Pass → the new version is written, the old one snapshotted, and an
   *Evolution Card* recorded. Fail → the candidate is quarantined, the live
   file restored, and a card is still written.

**How to use it.** Just talk to it. Related commands: `/skill-history <name>`,
`/skill-diff <name>`, `/skill-rollback <name>`, and `/extract_now` to run an
extraction on the current conversation. `KEELSON_AUTO_SKILL_EVOLUTION=0` turns
the automatic part off.

**Where you see it.** The Skill evolution page: one real update that was
admitted and one that was quarantined, with the evidence, the candidate, the
gate's numbers and the file diff. A recorded walk-through is in
[`self-evolution-demo.md`](self-evolution-demo.md).

## Long-term memory

**What it is.** Facts about you and the project ("this project uses pnpm",
"the user prefers answers in Chinese"), saved per project as small files and
kept across sessions.

**How it differs from skills.** Memory is **facts**; skills are **ways of
doing things**. "The test command is `make test`" is memory. "After changing
code, run the tests and show the output" is a skill.

**How to use it.** The agent decides when to write one (writing a new file goes
through the gate). `/memory` shows what it has. The files live under
`~/.keelson/memory/` and can be edited or deleted by hand.

## Sessions

**What it is.** One conversation and its history. The terminal has one at a
time; the web console holds several, each with its own history, event stream
and permission mode.

**How to use it.**

- Terminal: `keelson --resume` reopens the last session; `/clear` empties the
  current one.
- Console: the *Sessions* list on the left; `+` creates one, a click switches.
  *Earlier* below lists finished sessions: opening one is a read-only replay,
  and "Continue this session" picks it up again — also after a server restart.

**Worth knowing.** Sessions **share one working directory**: conversations are
isolated, files are not. At most 8 are live; beyond that the idle session used
least recently moves to history.

## Traces and replay

**What it is.** The runtime publishes every step as an event; the recorder
writes them as JSON lines, and that file is a trace. Replay feeds the same
events back into the interface.

**The problem.** "What did it just do?" should not need guessing or a second
run. And a demo should not depend on the network or an API key.

**How to use it.**

- Every web-console session is recorded to `.keelson/traces/` (`--no-record`
  turns that off).
- The Traces page lists recordings. *Guided* pauses at each key step with an
  explanation; *Replay* plays straight through.
- `keelson web --replay` serves recordings only, with no model at all.
- The replay bar has speed, a scrubber and "skip to end".

**Where you see it.** The Traces page, and the inspector's *Timeline* tab (the
raw event list).

## Timing and cost

**What it is.** Each step of the loop is timed: the model call, the permission
check, the tool, skill retrieval, context folding, and waiting for you to
answer a prompt.

**How to use it.**

- The inspector's *Timing* tab: a waterfall of the current turn — model or
  tools, at a glance.
- The bottom of the Benchmarks page: p50 / p95 over all recordings.
- `python scripts/perf_report.py [directory]`: the same table in a terminal.
- `/cost` (terminal) or *This session* in the inspector: tokens and estimated cost.

## MCP tools

**What it is.** The [Model Context Protocol](https://modelcontextprotocol.io)
is a standard for external programs to offer tools to an agent. Keelson
connects to MCP servers and hands their tools to the model.

**How to use it.** Copy `.mcp.example.json` to `.mcp.json` and edit it:

```json
{
  "mcpServers": {
    "context7": { "command": "npx", "args": ["-y", "@upstash/context7-mcp"] }
  }
}
```

Servers are connected on first use. MCP tools are untrusted by default: each
call needs confirmation unless a rule in `settings.json` allows it.

## Sub-agents

**What it is.** The main agent can hand a self-contained piece of work ("find
every use of this function in the repository") to a sub-agent, which has its
own context and returns only its conclusion.

**The problem.** Heavy searching and reading fills the main conversation's
context quickly. With a sub-agent, only the answer comes back.

**How to use it.** The model starts one when it needs to. A sub-agent never has
more permission than the agent that started it.

## doctor and init

**What it is.** Two commands for getting an install right.

- `keelson init` writes `.env` step by step (provider, base URL, model, key)
  and then checks it.
- `keelson doctor` checks the Python version, whether a key is configured,
  whether the model actually answers, git and ripgrep, the web dependencies and
  whether the state directory is writable — with a fix for each failure.
  `--offline` skips the network check.

**Where you see it.** The *This install* card on the console's Overview page
shows the same checks.

---

## Command reference

**Starting**

| Command | What it does |
|---|---|
| `keelson` | Interactive terminal session |
| `keelson "task"` | Run one task and exit |
| `keelson web` | Start the web console (`--port`, `--replay`, `--no-open`, `--no-record`) |
| `keelson init` / `keelson doctor` | Configure / check the install |
| `keelson --resume` | Reopen the last session |

**Common options**

| Option | What it does |
|---|---|
| `--plan` / `--accept-edits` / `--dont-ask` / `--yolo` | Permission mode |
| `--model`, `-m` | Model |
| `--api-base URL` | Base URL |
| `--max-turns N` / `--max-cost USD` | Turn / cost ceiling |
| `--thinking` | Extended thinking (Anthropic protocol only) |

**Inside a terminal session**

| Command | What it does |
|---|---|
| `/clear` | Clear the conversation |
| `/plan` | Toggle plan mode |
| `/cost` | Tokens and cost |
| `/compact` | Fold the context now |
| `/memory` | Show long-term memory |
| `/skills` | List skills |
| `/<skill-name> [args]` | Run a skill |
| `/skill-stats` | Skill usage and evolution statistics |
| `/skill-history <skill>` · `/skill-diff <skill> [version]` · `/skill-rollback <skill> [version]` | Versions · diff · roll back |
| `/skill-create name \| description \| when-to-use \| instructions` | Create a skill by hand |
| `/skill-evolve <skill> <lesson>` | Add a lesson to a skill by hand |
| `/skill-feedback <skill> <rating> [note]` | Record feedback on a skill |
| `/extract_now [hint]` | Extract a skill from the current conversation now |
| `/skill-eval` | Evaluate the quality of online skill evolution |

**Web console shortcut**: `Ctrl+K` opens the command palette (pages, replays,
new session, theme, language).

## Environment variables

Set them in `.env` or in the environment.

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | An OpenAI-compatible provider | — |
| `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL` | Anthropic | — |
| `APIKEY` + `API` | Generic form; a base URL whose path contains `/anthropic` uses the Anthropic protocol, anything else the OpenAI protocol | — |
| `MODEL` | Model name | `deepseek-flash` (`deepseek-chat` and `deepseek-reasoner` were retired on 2026-07-24) |
| `KEELSON_PROFILE` | Which profile to use (`~/.keelson/profiles/<name>.env`). `keelson use <name>` switches and remembers, `keelson -P <name>` is for one run, `keelson init --profile <name>` creates one | none |
| `KEELSON_SHELL` | The shell run_shell uses on Windows: `pwsh`, `powershell` (5.1) or `cmd` | PowerShell (7 if installed) |
| `KEELSON_CONTEXT_WINDOW`, `KEELSON_MAX_OUTPUT_TOKENS` | Override the model registry's planning window and per-request `max_tokens` | per model |
| `KEELSON_HOME` | State directory (sessions, skills, memory) | `~/.keelson` |
| `KEELSON_AUTO_SKILL_EVOLUTION` | Extract skills from feedback automatically | `1` |
| `KEELSON_AUTO_SKILL_TARGET` | Write new skills at project or user level | `project` |
| `KEELSON_JUDGE_MODEL` | Model for side judgements (extraction, the gate); may differ from the main one | same as `MODEL` |
| `KEELSON_LOOP_GUARD` | Loop guard | `1` |
| `KEELSON_OPENAI_STREAM` | Stream on the OpenAI protocol; set `0` if a gateway's streaming is unreliable | `1` |
| `KEELSON_TRACE` | Write timings to a local log file | `0` |

# Architecture

**English** | [中文](zh/architecture.md)

Keelson is an agent *harness*: the model reasons and proposes tool calls, and
the runtime owns everything that touches the environment. This page walks the
layers from the outside in. The Chinese version goes deeper on each module.

![Overall architecture](assets/architecture/01-overall-architecture.svg)

## Layers

| Layer | Files | Owns |
|---|---|---|
| Front ends | `agents/main.py`, `agents/ui.py`, `agents/web/` | CLI/REPL, terminal rendering, web console |
| Event bus | `agents/events.py` | The one stream every front end observes |
| Runtime | `agents/agent.py` | The loop, protocol adapters, tool dispatch, context folding, sessions |
| Capabilities | `agents/tools.py`, `skills.py`, `memory.py`, `mcp_client.py`, `subagent.py` | Tools and the permission gate, skills, long-term memory, MCP, sub-agents |
| Prompt | `agents/prompt.py` | Assembling the system prompt for the current state |
| Evolution | `agents/online_skill_evolution.py`, `skill_evolution.py`, `skill_gate.py`, `skill_history.py`, `evolution_card.py` | Extracting, gating, versioning and auditing skill changes |
| Evidence | `agents/bench/`, `bench/` | Benchmarks, metrics, committed run artifacts |
| State | `.keelson/`, `~/.keelson/` (`agents/paths.py`) | Skills, memory, sessions, traces, audit records |

## One turn

```text
user input
  -> Agent.chat()
       retrieve relevant skills (BM25) · start a memory prefetch · connect MCP on first use
  -> loop:
       compress context if needed
       call the model (Anthropic or OpenAI protocol)
       no tool call?  -> done
       for each tool call:
           permission gate  -> allow | confirm (ask the user) | deny
           loop guard       -> stop a tool that keeps repeating without new results
           execute          -> built-in tool | skill | MCP tool | sub-agent
           write the result back to the model
  -> save the session
  -> in the background: skill-usage tracking, and skill extraction from the
     user's next message if it reads as durable feedback
```

Read-only tools (`read_file`, `list_files`, `grep_search`) in the same model
response run concurrently; anything with side effects runs one at a time.

## The permission gate

`tools.py::check_permission` returns `allow`, `confirm` or `deny` for every
call, before anything executes.

- Five modes: `default`, `acceptEdits`, `plan` (read-only), `dontAsk`
  (auto-deny anything that would need confirmation) and `bypassPermissions`.
- `settings.json` allow/deny rules are checked first, at user and project level.
- Shell commands are matched against a list of destructive patterns
  (`rm`, `git push`, `del`, …) and need confirmation.
- Anything the gate does not recognise — MCP tools, future tools — is not
  trusted by default.
- A sub-agent never runs with more permission than its parent.

A denial is returned to the model as a tool result, so the model sees *why* and
can change course instead of stalling.

## Context management

Long tasks outgrow the context window. Three tiers, cheapest first:

1. **Per-result budget** — a single oversized tool result is spilled to disk
   and replaced by a reference.
2. **Stale-result pruning** — once the window is 60% full, old tool results
   are trimmed, keeping the most recent ones.
3. **Folding** — at 70%, the history is rewritten by a side model call into
   three structured slices: *episodic* (what happened), *working* (current
   goal and next actions) and *tool* memory (what worked, what failed).

## The loop guard

A model can get stuck calling one tool over and over with slightly different
arguments. The guard looks at the last 8 tool calls: if 6 or more are the same
tool *and* their results have stopped changing, it skips execution and tells
the model to answer. It is keyed on the tool name, not the arguments, because
the failure observed in the benchmark was argument fuzzing, not exact repeats
(see [`bench/spiral-diagnosis.md`](../bench/spiral-diagnosis.md)). Six reads of
six different files never trip it.

## Skills and their evolution

A skill is a `SKILL.md` file with front matter. Skills are retrieved per turn
and injected into the prompt, or run in a forked sub-agent.

Evolution is driven by the *user's* next message, never by the model grading
itself:

```text
turn N    : task -> answer -> remember this exchange
turn N+1  : if the message looks like durable feedback ("from now on, always…")
              Extractor   pulls one candidate rule out of the user's words
              Maintainer  decides add | merge | discard
              merge only: non-inferiority gate replays the skill's own history
                  pass -> write SKILL.md, snapshot the old version, write an Evolution Card
                  fail -> quarantine the candidate, restore the live file, still write a card
```

A real admit and a real quarantine are recorded in
[`self-evolution-demo.md`](self-evolution-demo.md).

## Front ends and the event bus

The runtime does not print. It publishes typed events — `tool_call`,
`permission`, `tool_result`, `context_fold`, … — on an in-process bus, and each
front end is a subscriber: the terminal renderer, the web console's SSE stream,
and the trace recorder. That is what lets a recorded run be replayed through
the same UI code as a live one. Details in [`web-console.md`](web-console.md).

## Known limits

Kept in one place, with the reasoning: [`design-tradeoffs.md`](design-tradeoffs.md).

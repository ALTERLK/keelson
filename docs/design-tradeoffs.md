# Design trade-offs & known limitations

## Why no LangGraph / AutoGen / Spring AI

Keelson is a **harness**, not an app built on one. The whole point of the project
is to own the loop the frameworks hide: prompt assembly, tool dispatch, the
permission gate, context folding, session persistence, the skill-evolution write
path. A framework would move exactly those pieces behind an API and leave less to
reason about, not more.

Concretely:

- **The agent loop is ~40 lines** per backend (`_chat_anthropic`, `_chat_openai`).
  It is a `while` loop that streams a response, executes `tool_use` blocks through
  `check_permission`, appends `tool_result`, and repeats. Nothing about that is
  easier as a graph.
- **State is a flat message list plus a folded-memory record**, not a graph of
  nodes. Long-running tasks are handled by *rewriting* history into
  `episode/working/tool` memory (`session_memory.py`), which is a different move
  from a checkpointer.
- **Provider differences are contained** in two `_chat_*` / `_call_*` methods and
  `_to_openai_tools`. LiteLLM would remove ~60 lines and add a dependency whose
  failure modes I would then have to learn.

The cost of this choice is real: no ecosystem of pre-built nodes, two loop
implementations to keep in sync, and tracing had to be built (the event bus,
the trace recorder and the console's timing view). It is the right cost for a project whose value *is* the loop.

## How the agent is evaluated

`keelson/bench/` runs the harness against **ToolHop** (multi-hop tool calling,
995 tasks). Each task ships executable Python tool bodies and a gold answer, so
scoring is deterministic and fully offline — no LLM-as-judge in the loop that
decides pass/fail.

Four arms on a train/test-split subset (`raw_api` = bare function-calling loop,
`no_skill` = full harness, `skill_v1` = + a hand-written skill, `skill_evolved` =
+ a skill grown on the *train* split). Arm-vs-arm deltas get a **paired bootstrap
95% CI** and a **McNemar** p-value (`keelson/bench/metrics.py`) — a few-point gap
on ~60 tasks is usually noise.

### Round 1: n=60 found the harness ~8pp behind `raw_api` — one narrow cause

`gemini-3.8-flash` via 4SAPI, full 60-task test split, uniform `max_turns=20`:
`raw_api` **80.0%**, `no_skill` 71.7%, `skill_v1` 73.3%. `invalid_tool_call_rate`
0% across all 180 task-runs — the harness's dispatch/schema handling never
regressed clean tool use.

The bigger n turned the earlier "all noise" into a **specific, directional
finding**: on the 48 tasks that reach the gateway and finish inside the turn
budget, all three arms were identical (92/90/90%); the whole deficit was **7
tasks where `no_skill`/`skill_v1` spiraled into a non-terminating tool-call loop
and burned all 20 turns**, which `raw_api` never did (0/60).

### Round 2: root-caused and fixed it — the deficit is gone

Payload diffs showed the harness request differs from `raw_api`'s in exactly one
material way: a system message. No hidden model state was being dropped (ruled
out message-fidelity loss as a cause). An A/no-system-prompt/raw ablation on the
7 affected tasks confirmed it: strip the harness's injected system prompt and it
collapses onto `raw_api`'s numbers on every axis (turn-cap rate, accuracy, model
calls). **Root cause:** the bench's own task prompt — not harness default
behaviour — said *"call one tool per hop … output only: ANSWER:"*, which reads to
a small model as "not allowed to finalise yet." On an ambiguous tool result it
kept calling tools, fuzzing arguments (name casing, alternate fields) rather than
repeating identical calls — confirmed non-terminating at `max_turns=40`.

Fix: reworded the prompt to explicitly permit finalising, plus two independent
hardening changes that would catch this regardless of prompt wording — a loop
guard in `_execute_tool_call` (windowed on tool *name*, since arguments were
fuzzed, gated on result diversity so 6 reads of 6 different files never trips it)
and graceful budget exhaustion (one best-effort answer instead of nothing, on
both backends, plus fixing a real bug where the OpenAI branch left dangling
`tool_calls` unanswered on turn-cap). Before/after on n=60 (×2–3 repeats, pooled):
accuracy 72.8% → **79.4%**, turn-cap rate 8.3% → **0.0%** (15/180 → 0/180, a
direct count), Δ +6.7pp with a 95% CI of [+1.7%, +12.8%] that excludes zero. The
loop guard never had to fire post-fix — the prompt fix alone was sufficient; it
stays as a backstop for prompts this bench didn't test. Full writeup and numbers:
`bench/REPORT.md`, `bench/spiral-diagnosis.md`.

`skill_v1` − `no_skill` remains a clean null post-fix (−1.1%, CI crosses 0): the
skill is inert once the harness itself isn't malfunctioning.

`skill_evolved` is **not a distinct arm on ToolHop**: `skill_evolved_probe.py`
drove the real evolution entry point over 12 train transcripts and extracted **0
candidates** — `extract_online_skill_candidate` keys on a user-feedback turn,
which a single-turn Q→A task doesn't have, so it collapses to `no_skill`.

**The harness's value has to be measured on task shapes ToolHop lacks.** Round 3
did that for coding: `keelson/bench/sandbox_runner.py` plugs a real Agent into
`sandbox.py`'s grader (now 14 tasks — bugfix/feature/edge-case/refactor/
traceback/trap — up from 3, each with a validated `solution/`), two arms over
the *same* 7 tools and the *same* real system prompt so only "harness or not"
differs. `gemini-3.8-flash` (weak) and `gpt-6-astra` (strong), n=14/cell,
descriptive only:

| arm / model | solve | avg turns | avg input tok |
|---|---:|---:|---:|
| raw / weak | 100% | 10.8 | 44.4k |
| harness / weak | 100% | 9.1 | 43.0k |
| raw / strong | 100% | 5.0 | 18.5k |
| harness / strong | 92.9% | 3.6 | 17.4k |

Zero test-tampering and zero out-of-scope edits across all 56 runs (including
two `trap` tasks designed to invite exactly that). Harness uses fewer turns
and fewer tokens than raw at **both** tiers — the opposite shape from ToolHop's
round-1 finding, which turned out to be a bench prompt bug, not a harness
property. The one failure (`harness`/strong, a 2-file constant rename) shipped
a syntax error from an unverified edit; followed up with 3 more reps rather
than left as n=1 — 3/4 pass with the harness vs 4/4 with raw on that cell. Read
as an occasional verification-skipping failure mode, not a systematic defect —
n=4 is too small to separate from noise, but it's the real shape of edit-safety
risk this benchmark exists to surface. Full numbers: `bench/REPORT.md`.

`keelson/bench/fold_fidelity.py` (does a fold keep task-critical facts vs.
truncation) has the probe extractor but **no runner**; `keelson/skill_gate.py` (a
regressing skill gets quarantined) is unit-tested only. These still need
benchmark-building.

## Self-evolution: judge / actor separation

Skill evolution never lets one model both act and grade itself into the live
skill set:

- **Hard gates are programmatic.** `skill_gate.gate_evolution` compiles
  rule checks (`response_nonempty`, `json_parseable`, `paragraph_limit`, …) and
  scores the skill's own recorded replies against the old text and the candidate
  text. A candidate is admitted only if it is non-inferior
  (`avg_score >= old - ε` **and** `hard_failures <= old`); otherwise it is
  written to `.keelson/skill-evolution/quarantine/` and the live file is rolled
  back from its pre-edit snapshot.
- **LLM judgment is advisory.** The `/skill-eval` harness can add `llm_binary`
  rules, but they are `hard=False` and (planned) run on a *different* model than
  the actor.
- **Every write is reversible.** `evolve_skill_file` snapshots the full
  `SKILL.md` before every change; `/skill-diff`, `/skill-history`,
  `/skill-rollback` read that history, and a rollback is itself snapshotted.

**Round 4 — live-fired, not just unit-tested.** Every skill in this project's
real history had `promoted: true` zero times (the gate only fires once a
skill has a second evolution behind it — its first has no replay sample yet,
so it's `ungated`). `scripts/demo_self_evolution.py` drives two independent
skill lineages through two real rounds each of the actual `online_ingest()` →
Extractor → Maintainer → `evolve_skill(gate=True)` pipeline: one lands on a
real `admit`, one on a real `quarantine` (rolled back, quarantine file
written) — both **naturally**, no scripted fallback needed. Full trace and
gate scores:
[`docs/self-evolution-demo.md`](self-evolution-demo.md). Building it also
found and fixed a real gap: a quarantined candidate got no Evolution Card at
all (`evolution_card.py`, `online_skill_evolution.py`) — now a rejection is as
auditable as an acceptance.

## Known limitations & bugs

| where | issue | status |
|-------|-------|--------|
| `agent.py` OpenAI branch (`_chat_openai`) | batch build + execution used to be nested inside `for tc in tool_calls:` → O(N²) executions and duplicate/unanswered `tool_call_id`s (OpenAI 400) | **fixed** — moved out of the per-call loop; regression test `tests/test_openai_tool_loop.py` |
| OpenAI-compatible gateways that don't stream `delta.tool_calls` (seen on 4SAPI's Gemini proxy) | harness saw zero tool calls, model answered from memory | **fixed** — `_call_openai_nonstream` + `KEELSON_OPENAI_STREAM=0` |
| default system prompt vs. domain tools | the coding-agent prompt makes a small model ignore injected non-coding tools (`tool_calls=0`, ~3× input tokens) | **worked around** — bench harness arms use a neutral task prompt; a real fix would make the system prompt tool-set-aware |
| un-classified-tool permission gate (below) | auto-denies benchmark-injected tools under `dontAsk` | bench arms run `bypassPermissions` (gate is unit-tested separately) — a `check_permission` that also consults `external_tools` would be cleaner |
| bench system prompt forbade finalising (round 2) | `_HARNESS_SYSTEM_PROMPT`'s "call one tool per hop … output only: ANSWER:" made `gemini-3.8-flash` fuzz tool arguments to the turn cap on 7/60 tasks, ~8pp below `raw_api` — root-caused via payload diff + A/B/raw ablation | **fixed** — reworded to permit finalising; before/after n=60: acc 72.8%→79.4%, turn-cap 8.3%→0.0% (`bench/spiral-diagnosis.md`) |
| OpenAI branch on turn-cap | `break`d without answering pending `tool_calls` → malformed history, next `chat()` on the same Agent 400s | **fixed** — stub `role:tool` messages appended before the finalize attempt, mirroring the Anthropic branch |
| no loop guard on tool dispatch | nothing stopped a model hammering one tool with reworded arguments | **fixed** — `_execute_tool_call` guard windowed on tool *name* (args were fuzzed, not repeated), gated on result diversity; `KEELSON_LOOP_GUARD` |
| turn-cap gave no final answer | `prediction=None` on every capped task instead of a best-effort answer | **fixed** — one tool-less call on cap (`KEELSON_FINAL_ANSWER_ON_BUDGET`), both backends, `raw_api` bench arm gets the same for a fair comparison |
| `_call_openai_nonstream` had no retry | the only model-call path not wrapped in `_with_retry` — a transient 429/503 killed the whole task | **fixed** — `tests/test_openai_nonstream.py` |
| quarantined skill candidates got no Evolution Card | `write_card`/`online_ingest` only wrote a card on `ok=True` add/merge — a rejected candidate left no audit trail beyond the quarantine file pair | **fixed** (round 4) — card titled "— REJECTED", cites the gate's old/new scores and the quarantine file path; `docs/self-evolution-demo.md` |
| `_coerce_candidate`'s string fields | a model occasionally returns `instructions` as a JSON list instead of a string; `str(obj.get(...))` rendered it as a raw Python list repr in an Evolution Card | **fixed** (round 4) — `_coerce_text` joins a list into bullet lines; `tests/test_online_skill_evolution.py` |
| sub-agent construction | `agent.py`'s `_execute_agent_tool` builds the child `Agent` without passing `api_key` / `anthropic_base_url` through | **found, not fixed** — a sub-agent under a non-default endpoint (e.g. this bench's 4SAPI config) would hit the wrong API |
| OpenAI vs Anthropic snip asymmetry | the OpenAI compression path snips *all* stale tool messages; the Anthropic path only snips `SNIPPABLE_TOOLS` | **found, not fixed** — doesn't trigger in the bench (contexts stay small), but the two backends compress differently |
| `frontmatter.py` | only a tiny YAML subset (`key: value` + `|`/`>` block scalars). Nested maps / flow sequences are not parsed | by design; documented |
| `keelson/bench` skill arms (`skill_v1`, `skill_evolved`) | `raw_api` / `no_skill` / `skill_v1` ran at n=60 (`bench/REPORT.md`); `skill_evolved` probed and shown to collapse to `no_skill` on ToolHop (no feedback turn → 0 candidates) | done for this dataset |
| `skill_gate` | LLM-judge rules are skipped in the offline gate (programmatic subset only) | intentional — hard gates must not depend on a model |
| context fold fidelity | probe extraction + grading built (`keelson/bench/fold_fidelity.py`); retention numbers need a model-backed fold | partial |
| MCP tools | now gated (`check_permission` denies un-classified tools in plan mode, confirms otherwise) — previously ungated | **fixed** |
| sub-agents | now bounded by the parent's permission mode (`_subagent_permission_mode`, default `dontAsk`) — previously forced to `bypassPermissions` | **fixed** |
| self-evolution cost | Extractor+Maintainer ran ~every turn (2 model calls); now pre-gated by `skill_gating.looks_like_feedback` (`KEELSON_SKILL_GATING`) | **fixed** — token-savings % pending a run |
| evolution auditability | add/merge decisions were spread across 5 JSONL files | **fixed** — one Evolution Card per event (`keelson/evolution_card.py`) |
| `run_shell` blocked the event loop | `subprocess.run` held the loop for the whole command: no events flowed, Stop did nothing, and a timeout killed the shell but left its children running | **fixed** (v0.3.0) — `run_shell_async`: asyncio subprocess, live `tool_output` events, process-tree kill on timeout/cancel; `tests/test_async_shell.py`, `scripts/smoke_live.py` |
| one session per process | the event bus was a module global, so two conversations in one server would have shared a stream | **fixed** (v0.3.0) — the current bus is a `ContextVar`; `keelson/web/sessions.py` binds one bus per session; `tests/test_web_server.py` |
| sessions were not browsable | messages and traces were saved but nothing listed or reopened them | **fixed** (v0.3.0) — history in the console; resume replays the trace and restores the message list, also after a restart |
| side-model JSON with raw line breaks | an OpenAI-compatible gateway in non-streaming mode re-chunked replies and left a literal newline inside JSON strings, once inside a key (`{"action\n": "add"}`); the Maintainer's decision failed to parse and a good skill candidate was silently discarded | **fixed** (v0.3.0) — `keelson/jsonutil.py` retries with raw newlines inside string literals removed (never valid JSON, so the repair cannot change a valid document); found while re-recording the demo traces; `tests/test_jsonutil.py` |
| trace scrubber missed nested JSON | a path inside a tool result that was itself JSON (backslashes escaped twice) was not rewritten; the leak check caught it and refused to save | **fixed** (v0.3.0) — `keelson/web/scrub.py` tries every escaping depth; `tests/test_scrub.py` |
| web sessions share a working directory | sessions isolate conversations, not files; two sessions editing one file will collide | by design for a local tool; a per-session worktree would be the next step |
| multi-line shell commands on Windows | `cmd /c` ran the first line and dropped the rest without an error; the model believed it had run commands it had not (0 of 40 ran completely in case-study rounds 1–2) | **fixed** (v0.3.0) — run from a temporary batch file, line by line; a quoted string spanning lines is refused with the reason; `tests/test_shell_multiline.py` |
| an empty model reply ended the turn | no text and no tool call satisfied the loop's exit condition, so a failed completion looked like a finished task | **fixed** (v0.3.0) — retried with a nudge, at most twice; `tests/test_empty_reply.py` |
| the model could reset the loop guard | a context fold cleared the guard's window, and `compact_context` is a tool the model can call | **fixed** (v0.3.0) — a fold leaves the window alone |
| the loop guard only asked | a trip returned a sentence and nothing else; one run ignored it four times | **fixed** (v0.3.0) — after three trips in a turn the runtime closes with a tool-less call (`LOOP_GUARD_MAX_HITS`) |
| the loop guard counted the shell as one tool | six `python -c` probes blocked the `python -m pytest` that followed | **fixed** (v0.3.0) — shell commands are counted by program and first argument (`_guard_key`) |
| the loop guard misses near-identical probing | "nothing new" is decided by comparing result hashes; 25 probes that each print something slightly different never trip it (humanize #205, all three rounds) | **open** — the fix is a threshold (similarity, or a budget of probes without an edit), and tuning one on the five issues it would be judged on is fitting to the test set |
| the permission gate is not a sandbox | it judges the text of one call: refused `write_file`, a model created the file with `echo > file`; refused `del`, it used `python -c "os.remove(...)"` | **by design, documented** — the gate stops accidents, not a model that insists; containment needs an OS-level sandbox |
| network installs need no confirmation | the dangerous-command list is about destruction (`rm`, `git push`, `kill`), not `pip install` | **open** |
| runtime overhead was asserted, not measured | "the gate is cheap" had no number behind it | **measured** (v0.3.0) — spans on every step; permission checks + skill retrieval are 0.003% of wall time over 18 recorded runs (`scripts/perf_report.py`), small sample |

## Positioning vs. Claude Code / Superpowers

Superpowers is a methodology + skill pack layered on Claude Code. Keelson is the
layer underneath: a provider-agnostic runtime where the tools can be files,
shell, MCP servers, or a benchmark's injected functions. The trade is
maturity-and-batteries (theirs) for a small, auditable, self-contained loop with
a governed evolution path (this one).

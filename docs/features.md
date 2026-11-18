# Feature guide

**English** | [中文](zh/features.md)

Every feature answers the same four questions: **what it is · what problem it
solves · how to use it · where you can see it**. If this is your first time,
read [Getting started](getting-started.md) first.

| I want to… | Section |
|---|---|
| control how much it may do without asking | [Permission gate and modes](#permission-gate-and-modes) |
| use it in a window instead of a terminal | [The desktop app](#the-desktop-app) |
| take back what it did, shell commands included | [Undo: checkpoints](#undo-checkpoints) |
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
you, or deny. Keelson decides by **whether the action can be undone**, not by
which tool it is.

**The problem.** Asking about every command trains people to press "yes"
without reading; asking about nothing lets one bad command delete a home
folder. Models make mistakes, and can be steered by hostile text in a file.

**How it decides.**

| The action | Verdict | Why |
|---|---|---|
| Edits, new files, ordinary commands inside the project | allow | a checkpoint covers them: `/undo` takes them back |
| `delete_files` inside the project | allow | goes to the Recycle Bin |
| Installing packages, network access, `git push`, history rewrites, writing outside the project, MCP and unknown tools | ask, with the reason | `/undo` cannot reverse them |
| Force-push, `git clean -x`, publishing, `sudo`, `curl … \| sh`, a path with an unset variable | ask, **once only** | never "always allowed" |
| Deleting from the shell (`rm`, `del`, `Remove-Item`, `find -delete`, …) | deny | use `delete_files` |
| Moving or deleting the home folder, a drive root, the project folder or a folder above it; writing to system folders | deny, in every mode | nothing should do this |

Paths are checked after `~`, `$HOME`, `$env:USERPROFILE`, `%VAR%` and `$VAR`
are expanded, and compound commands (`a && b; c | d`) are judged part by part.
Details and limits: [Safety and undo](safety.md).

When it asks, you choose: **allow once · allow for this session · always allow
in this project** (saved to `.keelson/settings.local.json`) **· deny**, or deny
with a reason the model gets to read. A one-time "yes" is not remembered.

Modes:

| Mode | What changes | How to start |
|---|---|---|
| `default` | the table above | default |
| `acceptEdits` | same as default (kept for compatibility) | `--accept-edits` |
| `dontAsk` | everything that would ask is denied (CI, sub-agents) | `--dont-ask` |
| `plan` | no edits and no commands, only the plan file | `--plan` or `/plan` |
| `bypassPermissions` | nothing asks; the "deny in every mode" row still applies | `--yolo` |

To allow or forbid specific calls, add rules to `~/.keelson/settings.json` (all
projects) or `.keelson/settings.json` (this project); `deny` wins over `allow`:

```json
{
  "permissions": {
    "allow": ["run_shell(npm install*)", "run_shell(git push*)"],
    "deny": ["run_shell(git push*)", "edit_file(.env)"]
  }
}
```

The pattern is matched against the shell command (and each part of a compound
command) or the file path; a trailing `*` means "starts with".

**Workspace trust.** A cloned repository could ship `run_shell(*)` in its
settings or an MCP server in `.mcp.json`. So a project's *allow* rules and MCP
servers take effect only after you trust the folder: the terminal asks once,
or run `keelson trust` (`--revoke` undoes it). Its *deny* rules apply at once.

**Where you see it.** The reason in the prompt; the "gate: allowed / approved by
you / denied" label on every tool card; the counters under *Permission gate* in
the inspector.

**Worth knowing.**

- A denial is returned to the model as the tool's result, with your reason if
  you gave one, so the model can change course instead of stalling.
- A sub-agent never has more permission than its parent.
- The web console cannot select `bypassPermissions`.
- This is a guard rail, not a sandbox: a test suite or build script that a
  command starts can still do anything your account can.

## Undo: checkpoints

**What it is.** At the start of every turn Keelson takes a snapshot of the
project; `/undo` puts back every file the turn changed — **also the ones a
shell command changed, created or deleted**.

**The problem.** Claude Code's checkpoints only track its own file-edit tools;
its documentation says changes made by bash commands are not covered. An agent
that ran a formatter, a code generator or `mv` could not be rolled back.

**How to use it.**

| Command | Does |
|---|---|
| `/undo` | put back what the last turn changed |
| `/undo --force` | the same, also over files you edited after that turn |
| `/rewind N` | undo the last N turns |
| `/redo` | reverse the last `/undo` |
| `/revert FILE [N]` | put one file back as it was N turns ago |
| `/checkpoints` | list the turns that can be undone, with their files |

If you changed a file after the turn ended, `/undo` lists it and stops instead
of overwriting your edit. After an undo the model is told which files changed.

**How it works.** A separate git repository in
`~/.keelson/projects/<hash>/checkpoints/` with its own index; your project's
`.git` is never touched. Files are stored byte for byte (no line-ending
conversion, no filters, no hooks). `node_modules`, `.venv` and other rebuildable
folders, files ignored by `.gitignore`, and new files over 50 MB are not
snapshotted; ignored files that Keelson's own tools change are saved
separately, so `/undo` covers them too.

**Not covered:** installed packages, commits and pushes, databases, the
network, and files outside the project that a shell command changed. Those are
exactly the actions the permission gate asks about first. Without git, only
Keelson's own file tools can be undone, the terminal says so, and ordinary
shell commands then ask before they run.

## Verified completion

**What it is.** When a turn changed code, Keelson itself checks the change
before the turn ends, and every such turn ends with a badge that comes from what
actually ran, not from what the model wrote:

```
✓ Verified  pytest -q: 42 passed in 3.1s (run by Keelson at 14:02)
✗ Checks fail  pytest -q: 1 failed, 1 passed in 0.23s
! Not verified  no check command for this project: add a ## Verify section to KEELSON.md (/verify init)
```

**The problem.** Coding agents say "done, all tests pass" when they never ran
the tests, ran them and they failed, or made them pass by editing the tests.
Users of Claude Code report the first two in its issue tracker; the third
("reward hacking") is measured for Claude Code and Codex by the EvilGenie
benchmark. The usual workaround is a hand-written Stop hook.

**How it works.**

- **Which checks.** The `## Verify` section of `KEELSON.md` (one command per
  list item in backticks, or a code block). Without one, Keelson detects tests
  only: pytest (the project's `.venv` Python if there is one), `npm`/`pnpm`/`yarn
  test`, `cargo test`, `go test ./...`. Lint and type checks run only if you
  list them. `/verify init` writes the detected commands into `KEELSON.md`.
- **Evidence is what ran.** Every tool call of the turn is recorded with its
  real exit code. A check the model already ran successfully after its last
  change is not run again (spelling differences such as PowerShell's
  `& "...\python.exe"` are ignored).
- **At the end of a turn that changed code**, Keelson runs the missing checks,
  through the same permission gate as the model's own commands (a check that
  would need your OK is reported as not run, never run behind your back), with
  `CI=1`. A failure goes back to the model with the real output, at most
  `KEELSON_VERIFY_RETRIES` times. It is told to fix what it broke, and **not**
  to widen the task to fix failures that were there before; an answer that
  already says what still fails is not pushed back.
- **Unproven claims.** A sentence such as "all tests pass" or "测试全部通过"
  with no passing run behind it is listed under the badge.
- **Docs-only turns** and projects without a check end as "Not verified", with
  the reason.

**Tests are guarded.** When the task does not mention tests:

- changing or deleting an existing test file asks first (in `dontAsk` mode it
  is refused). New test files and edits that only add lines do not ask. Answer
  "this session" or "this project" to stop being asked;
  `KEELSON_GUARD_TESTS=0` turns the guard off.
- Changes that weaken tests are detected from the diff against the turn's
  snapshot: tests removed, a test file deleted, `skip`/`xfail`/`.only` added,
  assertions removed, `assert True`, a changed expected value
  (`assert f(2) == 4` → `== 5`). The model is asked once to put them back, and
  whatever remains is listed under the badge.

**Not covered.** Keelson checks with the commands you give it; a project whose
tests do not cover the change still passes. Plan mode and sub-agents are not
verified (the parent's turn is). `KEELSON_VERIFY=0` turns it off.

## Task ledger: no amnesia after a fold

**What it is.** A record of the work that the runtime keeps outside the model's
context, saved after every turn and put back **word for word** when the context
is folded:

- the **goal**: the request that started the work, as you typed it;
- your **constraints**: every sentence of yours that reads like a rule ("don't
  touch the public API", "只改 parser.py", "always use pathlib"), quoted, never
  reworded;
- the **plan**, with each step's status (the model keeps it with `update_task`,
  and the terminal shows it as a checklist), and the **decisions** it recorded;
- the **files changed** so far and the **last verification** result.

**The problem.** After compaction, long-running agents forget the constraints
and how far they got, then carry on and break their own earlier work; Claude
Code users report it in its issue tracker. A summary written by the model is
exactly what loses the detail.

**How to use it.** Automatic. `/task` shows the ledger; `/task add RULE`,
`/task drop N`, `/task goal TEXT` and `/task clear` edit it. A fold prints
what was kept ("kept word for word: the goal, 3 constraint(s) verbatim, the
plan (2/5 done) …"). `--resume` brings the ledger back with the session. When
you exit with `exit`, rules you stated more than once are offered for
`KEELSON.md` (written only if you say yes).

**Limits.** Rules are recognised by wording (English and Chinese markers such
as *don't, never, always, must, only, keep* / *不要、必须、只、保持*); a rule
phrased without one is not picked up, and `/task add` covers that. The ledger
keeps the 40 most recent rules, dropping one-off rules before repeated ones.

## Scope guard: no unrelated changes

**What it is.** A request's changes stay within what it is about. The scope is
the files you `@`-mention plus what the model declares with `declare_scope`
(files, folders or globs, with a reason), shown as `◇ scope: …`. While a scope
exists, changing a file outside it asks you first, with the reason; in
`dontAsk` mode it is refused. A file you allow joins the scope. At the end of
the turn, files changed outside the scope are listed under the badge, and a
large change for a short request (8+ files for under 200 characters) is
flagged.

**The problem.** Asked to fix one thing, agents "improve" code nobody asked
about and introduce regressions; Claude Code users asked for a strict-scope
mode in its issue tracker.

**Limits, plainly.** With no scope declared and nothing mentioned, the guard is
idle. The model can widen its own scope with another `declare_scope` (you see
each one). Shell commands are not checked against the scope (they are covered
by `/undo` and the permission gate). The scope resets with each new request.
Answer "this session"/"this project" to stop being asked;
`KEELSON_SCOPE_GUARD=0` turns it off.

## Prompt caching and cost

**What it is.** Keelson keeps every request a pure extension of the previous
one, so providers with prefix caching (DeepSeek, OpenAI, Anthropic) bill most
of the conversation at the cache-hit price:

- the system prompt holds nothing that changes during a session; git branch
  and status are sent with your message, and only when they changed;
- the "context is getting long" hint goes on the newest tool result, once, when
  a threshold is crossed, instead of being rewritten into the system prompt;
- a test (`tests/test_ledger_scope.py`) checks that each request starts with
  the previous request's messages, byte for byte.

In one DeepSeek run of the same task before and after this change, the share of
input tokens billed as cache hits went from 43% to 82% (each call's cache hit
stopped at about 3,000 tokens before; after, it grew with the conversation).
One run each, and the two runs took different paths, so treat it as an
illustration, not a benchmark.

**Usage.** The turn's closing line shows the cache hit rate. Every turn appends
a record to `~/.keelson/usage.jsonl` (project, session, model, tokens, cache
hits, estimated cost); `/usage` sums this session, the project today, all
projects today, the last 7 days, and per model. `KEELSON_DAILY_BUDGET_USD`
stops new turns once today's estimated spend reaches it. Costs are estimates
from list prices; cache hits are what the provider reported.

**DeepSeek thinking mode.** Earlier `reasoning_content` is sent back on every
request: DeepSeek's documentation requires it for requests that carry tools.

## Project instructions: KEELSON.md, AGENTS.md, CLAUDE.md

Keelson reads `KEELSON.md`, `AGENTS.md` (the cross-tool convention) and
`CLAUDE.md` from the project folder and every folder above it, plus
`~/.keelson/KEELSON.md` for your own rules everywhere. Outer folders come
first, so the deeper, more specific file wins. `@./path` includes work in all
of them; `.keelson/rules/*.md` is added after.

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

**How to use it.** It is automatic. `/compact` forces a fold. After a fold the
[task ledger](#task-ledger-no-amnesia-after-a-fold) is put back word for word.

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

## The terminal

**What it is.** The interactive `keelson` session: an input line with history
and completion, a status bar, the answer rendered as Markdown while it streams,
and commands for the pieces you want to see or change.

**How to use it.**

- **Enter** sends; **Alt+Enter** (or Ctrl+J) starts a new line; a pasted block
  stays one message. **↑/↓** walk the history, kept in `~/.keelson/history.txt`.
- **Tab** completes `/commands` (with what each does), `@files` anywhere in a
  message, and the file names after `/add`, `/drop` and `/revert`. A bare name
  such as `@calc` also finds `src/util/calc.py`.
- **Shift+Tab** switches mode: default → accept edits → plan → default.
- **Ctrl+C** stops the turn and kills a command it started; at the prompt, twice
  exits. **Ctrl+D** (Ctrl+Z then Enter on Windows) exits too.
- The bar at the bottom shows model │ mode │ context used │ cost │ cache hit
  rate │ files added with `/add`.
- `/add FILE...` puts files in the conversation: their contents go to the model
  once, they count as in scope for the scope guard, and later turns only name
  them. `/drop` takes them out.
- `/diff [N]` shows what the last N turns changed, shell changes included;
  `/review` shows the last turn's verification badge, flags and files.
- `/status` shows model, mode, workspace, session, context, cost, checks and
  how far `/undo` reaches; `/resume` lists recent sessions and `/resume N`
  reopens one; `/init` writes a starter `KEELSON.md`.
- Interface text is Chinese on a Chinese system and English otherwise;
  `KEELSON_LANG=zh|en` chooses. Command names stay English.

**Worth knowing.** Without a real terminal (a pipe, CI) or with
`KEELSON_SIMPLE_INPUT=1`, input falls back to a plain line read and the answer
is printed as raw text. `KEELSON_MARKDOWN=0` turns off the Markdown rendering.

## The desktop app

> For a step-by-step walkthrough with screenshots, see the [desktop app guide](desktop.md).

**What it is.** Keelson in its own window on Windows (WebView2, which Windows 10
and 11 already have). The window shows the same console as `keelson web`; behind
it, a console server runs in the project folder you picked.

**What problem it solves.** No terminal, no Python, no port to remember: unzip,
double-click `Keelson.exe`, follow three steps, and give it a task.

**How to use it.**

- From the release zip: unzip anywhere and run `Keelson.exe`. From a checkout:
  `pip install -e ".[desktop]"`, then `keelson desktop [FOLDER]`.
- **First run** walks through three steps: the model (DeepSeek preset; paste the
  key, **Test** sends one tiny request, **Test and save** writes the active
  profile or `~/.keelson/config.env`), the project folder, and the checks it
  found there (save them to `KEELSON.md` with one click), with three suggested
  first tasks. A suggestion lands in the input box; nothing is sent until you
  press Send.
- **Projects**: the window opens the last project; **Switch** in the sidebar goes
  back to the list of recent ones. Opening another project restarts the server
  in that folder; going back to the one already open does not.
- **Review** tab: the last turn's verification badge and, for the last N turns,
  every changed file with its diff, out-of-scope and weakened-test marks, a
  **Put back** button per file and **Undo / Redo** for whole turns. Shell changes
  are included, as with `/undo`.
- **Task** tab: the task ledger (goal, your rules word for word, plan, files,
  last verification); add or remove rules there.
- **Plan approval**: in plan mode, the plan opens in a dialog: carry it out,
  carry it out asking as usual, or keep planning with your feedback.
- The overview shows the cache hit rate next to cost.
- `keelson-cli.exe`, next to `Keelson.exe`, is the `keelson` command for the
  terminal.

**Where you can see it.** Logs go to `~/.keelson/logs/` (`desktop.log`,
`desktop-server.log`); recent projects are in `~/.keelson/desktop.json`.

**Worth knowing.** Closing the window stops the server and everything it
started; a server whose window disappeared without closing exits on its own
within seconds. The key is read from the config files when needed and never
kept in the window's environment. The release is Windows only for now; on
Linux and macOS, `keelson desktop` runs from a checkout if pywebview has a
backend there, but it is not tested.

## One-shot and scripted use: `keelson -p`

**What it is.** One request, no questions, only the answer on stdout — for
scripts, git hooks and editors.

**How to use it.**

```bash
keelson -p "explain src/app.ts"
git diff | keelson -p "review this change"
keelson -p --output-format json "fix the failing test" > result.json
```

The prompt comes from the arguments, stdin, or both (stdin is appended).
Anything that would need a confirmation is denied and the model is told why,
so pick the mode up front (`--accept-edits`, `--yolo`). Progress and warnings
go to stderr. `--output-format json` prints one object: `result`, `verify`
(the end-of-turn badge), `usage` (tokens, cache hits, cost), `model`,
`session_id`, `error`. The exit code is 0 when done, 1 when the end-of-turn
checks failed, 2 on an error.

## Sessions

**What it is.** One conversation and its history. The terminal has one at a
time; the web console holds several, each with its own history, event stream
and permission mode.

**How to use it.**

- Terminal: `keelson --resume` reopens the last session, `/resume` lists recent
  ones and `/resume N` opens one — messages, task ledger and folded memory
  together. `/clear` empties the current one.
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
call needs confirmation unless a rule in `settings.json` allows it. Servers
listed in a project's `.mcp.json` or `.keelson/settings.json` start only after
you trust the folder (see *Workspace trust* above).

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

- `keelson init` asks step by step (provider, base URL, model, key), writes
  `~/.keelson/config.env` and then checks it. `--project` writes the project's
  `.keelson/config.env` instead, `--profile NAME` a profile.
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
| `keelson "task"` | Run one task and exit (asks as usual) |
| `keelson -p "task"` | Run one task without asking; print only the answer (`--output-format json`) |
| `keelson web` | Start the web console (`--port`, `--replay`, `--no-open`, `--no-record`) |
| `keelson init` / `keelson doctor` | Configure / check the install |
| `keelson use [NAME]` | List profiles, or switch to one |
| `keelson trust [--revoke]` | Let this folder's allow rules and MCP servers apply |
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
| `/help` | All commands, grouped |
| `/status` | Model, mode, workspace, session, context, cost, checks, undo coverage |
| `/mode [default\|acceptEdits\|plan]` | Show or switch the permission mode (also Shift+Tab) |
| `/add FILE...` · `/drop [FILE...]` | Put files in the conversation (and the scope) · take them out |
| `/diff [N]` | What the last N turns changed, shell changes included |
| `/review` | The last turn: verification badge, flags, files changed |
| `/init` | Write a starter `KEELSON.md` with the detected checks |
| `/resume [N]` | List recent sessions · reopen one |
| `/exit` | Quit (also `exit`, `quit`, Ctrl+D) |
| `/clear` | Clear the conversation |
| `/plan` | Toggle plan mode |
| `/cost` | Tokens and cost |
| `/compact` | Fold the context now |
| `/undo [--force]` · `/rewind N` · `/redo` | Undo the last turn (shell changes too) · the last N · reverse an undo |
| `/revert FILE [N]` · `/checkpoints` | Put one file back · list what can be undone |
| `/verify` · `/verify init` · `/verify run` | Show the end-of-turn checks · save the detected ones to `KEELSON.md` · run them now |
| `/task` · `/task add RULE` · `/task drop N` · `/task goal TEXT` · `/task clear` | Show or edit the task ledger |
| `/usage` | Tokens, cache hit rate and estimated cost: session, today, 7 days, per model |
| `/model [NAME]` | Show or switch the model on this endpoint |
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

Set them in `~/.keelson/config.env`, a profile, the project's
`.keelson/config.env`, or the environment (which wins). A legacy `.env` in the
project is still read, last.

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | An OpenAI-compatible provider | — |
| `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL` | Anthropic | — |
| `APIKEY` + `API` | Generic form; a base URL whose path contains `/anthropic` uses the Anthropic protocol, anything else the OpenAI protocol | — |
| `MODEL` | Model name | `deepseek-flash` (`deepseek-chat` and `deepseek-reasoner` were retired on 2026-07-24) |
| `KEELSON_PROFILE` | Which profile to use (`~/.keelson/profiles/<name>.env`). `keelson use <name>` switches and remembers, `keelson -P <name>` is for one run, `keelson init --profile <name>` creates one | none |
| `KEELSON_SHELL` | The shell run_shell uses on Windows: `pwsh`, `powershell` (5.1) or `cmd` | PowerShell (7 if installed) |
| `KEELSON_CONTEXT_WINDOW`, `KEELSON_MAX_OUTPUT_TOKENS` | Override the model registry's planning window and per-request `max_tokens` | per model |
| `KEELSON_HOME` | State directory (sessions, skills, memory, checkpoints) | `~/.keelson` |
| `KEELSON_CHECKPOINTS` | Snapshot the project each turn so `/undo` works | `1` |
| `KEELSON_CHECKPOINT_MAX_FILE_MB` | New files larger than this are left out of snapshots | `50` |
| `KEELSON_VERIFY` | Check code changes at the end of each turn | `1` |
| `KEELSON_VERIFY_RETRIES` | How many times failing checks go back to the model | `2` |
| `KEELSON_VERIFY_TIMEOUT` | Seconds each check may run | `300` |
| `KEELSON_GUARD_TESTS` | Ask before changing existing tests when the task is not about tests | `1` |
| `KEELSON_SCOPE_GUARD` | Ask before changing files outside the request's scope | `1` |
| `KEELSON_LEDGER` | Put the task ledger back after a fold (`0`: keep only the model's summary; for comparisons) | `1` |
| `KEELSON_DAILY_BUDGET_USD` | Stop new turns once today's estimated spend (all projects) reaches this | none |
| `KEELSON_TRUST_ALL` | Treat every folder as trusted (for your own machines and CI only) | `0` |
| `KEELSON_AUTO_SKILL_EVOLUTION` | Extract skills from feedback automatically | `1` |
| `KEELSON_AUTO_SKILL_TARGET` | Write new skills at project or user level | `project` |
| `KEELSON_JUDGE_MODEL` | Model for side judgements (extraction, the gate); may differ from the main one | same as `MODEL` |
| `KEELSON_LOOP_GUARD` | Loop guard | `1` |
| `KEELSON_OPENAI_STREAM` | Stream on the OpenAI protocol; set `0` if a gateway's streaming is unreliable | `1` |
| `KEELSON_TRACE` | Write timings to a local log file | `0` |
| `KEELSON_LANG` | Interface language: `zh` or `en` | the system's |
| `KEELSON_MARKDOWN` | Render the answer as Markdown in a terminal | `1` |
| `KEELSON_SIMPLE_INPUT` | Plain line input instead of the completing input line | `0` |

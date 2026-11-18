# Changelog

Notable changes, newest first. Benchmark figures quoted here are reproduced in
[`bench/REPORT.md`](bench/REPORT.md) from the committed run artifacts.

## Unreleased

### Fixed
- **Verification found no checks in small projects.** Test files beside the
  code (`test_x.py` next to `x.py`, no `tests/` folder) were not detected, so
  the badge said "no check command". The project root is searched too.
- **Live shell output showed a temporary script path.** On PowerShell the
  streamed `tool_output` carried the path of Keelson's temporary script (under
  the user's home folder) in error positions; the tidied text the model gets
  was not applied to the live view. The live relay now applies the same
  transform, so the console and recorded traces no longer show it.
- **Permission reasons were always English.** The reasons in the confirm
  dialog (why something cannot be undone) now follow the interface language.
  Refusal messages to the model stay English.
- **Markdown tables in answers were shown as raw pipes** in the console; GFM
  tables are now rendered (cells escaped like the rest of model output).

### Changed
- The Overview page and the guided tour describe 0.4: the five safeguards in
  the order *learns your ways, checks its own work, stays out of trouble,
  keeps your rules, takes it all back*; tour captions for the Recycle Bin,
  the verify badge and undo.
- Demo traces re-recorded with DeepSeek: the failing-test fix now ends
  verified; the deletion demo shows the model going to `delete_files` and the
  Recycle Bin; two new traces, `/undo` after shell changes and a confirm for a
  package install (denied).
- Screenshots and the replay GIF regenerated from the 0.4 console.
- Getting started, the security policy, the architecture pages and the
  package description updated for 0.4 (reversibility-based gate, the key in
  `~/.keelson/config.env`, checkpoints, verification, ledger, scope, the
  desktop app).

### Added
- **Desktop app guide** ([English](docs/desktop.md), [中文](docs/zh/desktop.md)):
  download to undo, step by step, with screenshots of a real run.

## 0.4.0 — 2026-11

### Added
- **Feature experiments** (phase 7). `python -m keelson.bench.features` runs
  each signature feature on and off against a real model (verification,
  scope guard, task ledger across a fold, `/undo` after shell changes) and a
  no-model corpus of destructive commands through every permission mode.
  Every fixture has a reference solution, and `--validate` proves each
  starter fails and each solution passes (and, for the regression tasks,
  that the obvious fix breaks another module's tests).
  `scripts/features_report.py` writes the tables in
  [`bench/FEATURES.md`](bench/FEATURES.md) from the raw results.
- **Dogfooding log**: [`docs/dogfooding.md`](docs/dogfooding.md) records real
  tasks on this repository done with `keelson -p`: outcome, verdict, cost,
  cache hits, refusals and asks, and what was kept. `scripts/dogfood_report.py`
  builds its table from `bench/dogfood/`, with local paths replaced.
- The README (both languages) leads with the five pain points, the evidence
  for each (official documentation, or user reports labelled as such) and
  the measured results, null results included.
- `KEELSON_LEDGER=0` leaves the task ledger out of a fold, so a fold keeps
  only the model's summary; the ledger experiment's comparison arm.
- **The desktop app** (phase 6). `keelson desktop [FOLDER]` (extra `desktop`)
  shows the console in its own window (pywebview + WebView2) and runs the
  console server as a child process in the chosen project folder; switching
  projects restarts it there, and closing the window stops it and everything it
  started (`--parent-pid` makes an orphaned server exit by itself).
  `scripts/build_desktop.py` builds `Keelson.exe` (windowed, no console windows:
  every subprocess runs with `CREATE_NO_WINDOW`) and `keelson-cli.exe` into one
  PyInstaller folder, with the console's data included.
- **First-run wizard**: model (DeepSeek preset, key tested with one request
  before it is saved), project folder, the checks found there (one click to put
  them in `KEELSON.md`) and three suggested first tasks; a recent-projects list
  after that. The key is read from the config files when needed and never kept
  in the window process's environment.
- **Console: review, undo, task, plan.** A Review tab with the last turn's
  verification badge, each changed file's diff with out-of-scope and
  weakened-test marks, per-file **Put back** and whole-turn **Undo / Redo**
  (shell changes included); a Task tab that shows and edits the task ledger; a
  plan-approval dialog in plan mode; verification badges in the conversation;
  the cache hit rate in the overview. New endpoints: `GET …/review`,
  `POST …/undo|redo|revert` (409 while a turn runs), `GET|POST …/ledger`,
  `POST …/plan`, `GET /api/usage`; `/mode` accepts `plan`.
- **A terminal you can work in** (phase 5). The input line (prompt_toolkit)
  sends on Enter, adds a line on Alt+Enter, keeps pasted blocks whole, keeps
  history across sessions and completes `/commands`, `@files` and paths; a
  status bar shows model, mode, context, cost, cache hit rate and added files;
  Shift+Tab cycles default / accept edits / plan. The answer streams as
  rendered Markdown. Interface text is Chinese or English (`KEELSON_LANG`,
  else the system language), from one string table (`keelson/i18n.py`) and one
  command registry (`keelson/commands.py`) that `/help` and completion share.
- New commands: `/status`, `/mode`, `/add` and `/drop` (files in the
  conversation, sent once and counted in scope), `/diff [N]` (the last N turns'
  changes, shell changes included), `/review`, `/init`, `/resume [N]`, `/exit`.
  Unknown `/commands` say so instead of going to the model.
- **`keelson -p`**: one request without questions (confirmations are denied
  with the reason), only the answer on stdout, prompt from arguments and/or
  stdin; `--output-format json` adds the verification result, usage and session
  id; exit code 1 when the end-of-turn checks failed, 2 on an error.
- **Task ledger** (`keelson/ledger.py`): the goal, the user's rules word for
  word, the plan (`update_task`), decisions, files changed and the last
  verification, saved each turn, put back verbatim after a context fold,
  restored with `--resume`, shown and edited with `/task`. Rules repeated in a
  session are offered for `KEELSON.md` on exit.
- **Scope guard** (`keelson/scope.py`): `@`-mentioned files plus
  `declare_scope` make a request's scope; changing other files asks first, and
  out-of-scope changes and large changes for short requests are reported.
- **Prompt caching that holds**: the system prompt no longer changes during a
  session (git status moved into user messages; the fold hint goes on the
  newest tool result), with a test that each request extends the previous one
  byte for byte. One DeepSeek run each, same task: cache hits 43% → 82% of
  input tokens.
- **`/usage`** and `~/.keelson/usage.jsonl` (per-turn tokens, cache hits, model,
  estimated cost), the cache hit rate in the turn's closing line, and
  `KEELSON_DAILY_BUDGET_USD`.
- `KEELSON.md` and `AGENTS.md` are read as project instructions alongside
  `CLAUDE.md`, from the project folder up, plus `~/.keelson/KEELSON.md`.
- **Verified completion** (`keelson/verify.py`). A turn that changed code ends
  with a badge from what actually ran: ✓ verified / ✗ checks fail / ! not
  verified, with the reason. Keelson runs the project's checks itself (the
  `## Verify` section of `KEELSON.md`, else detected tests; `/verify`,
  `/verify init`, `/verify run`) through the same permission gate as the
  model's commands, feeds failures back with the real output (at most
  `KEELSON_VERIFY_RETRIES`, default 2), and does not push the model to widen
  the task for failures that were already there. "Tests pass" claims without a
  passing run are flagged.
- **Test protection.** When the task is not about tests, changing an existing
  test file asks first, and changes that weaken tests (removed tests or
  assertions, new skips, `assert True`, changed expected values) are detected
  against the turn's snapshot, sent back once, and reported.
- **Undo that covers shell commands.** Each turn starts with a snapshot of the
  project in a separate git repository (`keelson/checkpoints.py`); `/undo`,
  `/rewind N`, `/redo`, `/revert FILE` and `/checkpoints` restore what a turn
  changed, including files that commands created, changed or deleted. Files
  edited after the turn are reported, not overwritten. The project's own
  `.git` is never touched.
- **`delete_files`**, the only way to delete: it moves things to the Recycle
  Bin / Trash (`send2trash`, a new dependency). Deleting from the shell (`rm`,
  `del`, `Remove-Item`, `find -delete`, also inside `bash -c` / `cmd /c` /
  script blocks) is refused with a pointer to it.
- **A path boundary** (`keelson/safety.py`): variables and `~` are expanded
  before a path is judged; an unset variable makes a command dangerous; moving
  or deleting the home folder, a drive root or the project, and writing to
  system folders, are refused in every mode.
- **Workspace trust**: a project's allow rules and MCP servers apply only after
  `keelson trust` (or a yes at the prompt). Deny rules apply at once.
- **Profiles**: `keelson use NAME` switches between saved model setups,
  `-P NAME` for one run, `keelson init --profile NAME` creates one; `/model`
  switches the model in a session.
- DeepSeek as the default model, with its thinking switch, `reasoning_content`
  passback, truncated tool calls held back, and cache-hit cost accounting.
- Windows: PowerShell is the default shell (bash habits are caught before they
  run), files keep their encoding (UTF-8/GBK, BOM) and line endings, edits are
  atomic.

### Fixed
- The weakened-tests check flagged a `pytest.skip` in a new helper or a new
  test, and `pytest.skip` inside string literals. For Python files it now
  counts only skip markers that stop tests that already existed (found while
  dogfooding).
- Commands that erase a disk or partition (`dd of=/dev/…`, `mkfs.*`,
  `Format-Volume`, `Clear-Disk`, `format D:`, `wipefs`, `diskpart`) are
  refused in every mode. The destructive-command corpus found `dd` and
  `mkfs.ext4` allowed even in default mode and `Format-Volume` only asked about.
- `--resume` restored only the messages; it now brings back the task ledger and
  folded memory too and keeps the session's id.
- Entering plan mode on an OpenAI-compatible provider did not update the system
  message the model saw.
- Resuming a session in the console restored only the messages; it now brings
  back the task ledger and folded memory too, as `--resume` does.
- Opening the console straight at `#/console` could start a demo replay before
  the live server answered; the page now waits for it, and says "connecting"
  instead of "no model configured" meanwhile.
- The console's history listed sessions nobody had typed into.
- Undo / redo / revert messages and the verification badge's reasons were
  English in a Chinese interface; they now follow `KEELSON_LANG`. The console
  asks before undoing in its own dialog instead of the browser's.
- Git and MCP subprocesses opened a console window when the parent had none.

### Changed
- **Permissions are decided by reversibility.** What `/undo` covers (edits and
  ordinary commands inside the project) no longer asks; what it cannot reverse
  asks, with the reason. Choices are now allow once / this session / always in
  this project (`.keelson/settings.local.json`) / deny, optionally with a
  reason the model reads. A one-time OK is no longer cached. Dangerous actions
  can never be always-allowed. Details: [Safety and undo](docs/safety.md).
- Compound shell commands are judged part by part; allow and deny rules match
  each part.
- `--yolo` still refuses the actions in the "refused in every mode" list.
- The sandbox benchmark's tool set includes `delete_files`.

## 0.3.0 — 2026-11

### Added
- **A case study on real issues** ([write-up](docs/case-study.md),
  `bench/casestudy/`). Five issues from more-itertools, cachetools,
  python-dotenv, humanize and tenacity, each already fixed upstream. The agent
  gets the issue text and a checkout of the commit before the fix, with no git
  history; the maintainers' own tests decide. Three rounds resolved 3/5, 3/5
  and 4/5, which is too small a sample to be a success rate. Its real output
  is the five runtime defects listed under *Fixed*, plus four problems that are
  written up and not fixed. All 15 runs are recorded and replay from the new
  Cases page.
- **Sessions in the web console.** One server process now holds several
  independent conversations, each with its own event stream, permission mode,
  prompts and recording. Finished sessions are listed and can be reopened and
  continued, also after a restart. The current event bus is a `ContextVar`, so
  the runtime needed no knowledge of sessions (`agents/web/sessions.py`).
- **Live command output and a real Stop.** `run_shell` is an asyncio subprocess:
  output is published while the command runs, the event loop is never blocked,
  and a timeout or Stop kills the whole process tree (`taskkill /T` on Windows,
  the process group on POSIX). The string returned to the model is unchanged.
- **Timing.** Every step of the loop publishes a `span` event. The console
  draws a per-turn waterfall; `scripts/perf_report.py` reports p50/p95. On the
  recorded runs the runtime's own checks are under 0.01% of wall time.
- **Getting started.** `keelson doctor` checks an install item by item and says
  how to fix what is wrong; `keelson init` writes `.env` interactively. The
  console opens on an Overview page that explains what the project is, with an
  interactive diagram of one turn and the state of the current install.
- **Guided replay.** A replay can pause at the events worth explaining and show
  a caption next to the element concerned. The captions are rules keyed on event
  type, so the tour works on any trace.
- Console redesign: bundled Inter and JetBrains Mono (no CDN), a command
  palette (Ctrl+K), a renderer per tool (terminal, diff, highlighted source),
  inspector tabs, toasts instead of alerts.
- Docs: a from-zero [getting-started guide](docs/zh/getting-started.md) and a
  [feature guide](docs/zh/features.md), in Chinese and English.
- Figures generated by `scripts/build_diagrams.py` (one description, two
  languages, light and dark palettes inside each SVG); CI checks they are current.
- `scripts/smoke_live.py`: an end-to-end check against a real model of Stop,
  session isolation and resume after restart.
- `CONTRIBUTING.md`, `SECURITY.md`, issue templates.

### Changed
- The README is now Chinese (`README.md`) with an English counterpart
  (`README.en.md`).
- The web API is addressed per session (`/api/sessions/{id}/…`). The
  single-session endpoints of 0.2.0 (`/api/chat`, `/api/events`, …) are gone.
- Synchronous tools run in a worker thread instead of on the event loop.
- Demo traces re-recorded with spans and live output.

### Fixed
Found by the case study:
- **On Windows only the first line of a multi-line shell command ran.**
  `cmd /c` drops the rest without an error, so the model believed it had run
  commands it had not. None of the 40 multi-line commands in the first two
  rounds ran completely. Such a command now runs from a temporary batch file,
  line by line; a quoted string that spans lines, which cmd.exe cannot express,
  is refused with the reason (`agents/tools.py`, `tests/test_shell_multiline.py`).
- **An empty model reply ended the turn.** No text and no tool call matched the
  loop's exit condition, so a failed completion looked like a finished task. It
  is now retried, at most twice (`tests/test_empty_reply.py`).
- **The model could reset the loop guard.** Folding the context cleared the
  guard's window, and `compact_context` is a tool; one run called it saying it
  needed "to reset". A fold now leaves the window alone.
- **The loop guard only asked.** After three trips in one turn the runtime now
  ends the turn itself with one call that carries no tools.
- **The loop guard blocked the test run after an edit.** It counted by tool
  name, so six `python -c` probes also blocked `python -m pytest`. Shell
  commands are now counted by program and first argument.
- Three more spellings the trace scrubber missed: a directory named after the
  user (`pytest-of-<user>`), a path abbreviated by pytest, and source code
  (`as e:` before a line break) mistaken for a drive letter.

Found earlier:
- **Skill candidates silently discarded.** An OpenAI-compatible gateway in
  non-streaming mode left raw line breaks inside JSON strings (once inside a
  key), so the Maintainer's decision failed to parse and a valid candidate was
  dropped. Model-written JSON is now parsed by `agents/jsonutil.py`, which
  retries with raw newlines inside string literals removed. Found while
  re-recording the demo traces.
- The trace scrubber missed paths inside a tool result that was itself JSON
  (backslashes escaped twice). The leak check caught it and refused to save;
  the scrubber now tries every escaping depth.
- `subprocess` output decoded with the console code page raised
  `UnicodeDecodeError` in a reader thread on GBK Windows.

## 0.2.0 — 2026-11

### Added
- **Event bus** (`agents/events.py`). The agent loop publishes typed events
  instead of printing; the terminal, the web console and the trace recorder are
  subscribers of the same stream.
- **Web console** (`keelson web`): streaming conversation, per-call permission
  verdicts, a permission prompt answered from the browser, a run inspector
  (loop stage, tokens, context window, gate and guard counters), and pages for
  skill evolution, benchmarks and recorded traces. English and Chinese, light
  and dark. See [`docs/web-console.md`](docs/web-console.md).
- **Trace recording and replay.** Live sessions are recorded to
  `.keelson/traces/*.jsonl`; a replay drives the console through the same code
  as a live run. Three recorded runs ship in `demo/traces/`.
- **Hosted demo.** `scripts/build_pages.py` exports a static, replay-only build
  of the console.
- One-command start (`start.bat`, `start.sh`), `docker-compose.yml`.
- The loop now reports the permission gate's verdict, loop-guard hits, context
  folds and token usage as events (previously terminal-only or not shown).

### Changed
- Terminal UI: one status line per tool call with duration and outcome, a
  per-turn footer (tokens, estimated cost, elapsed time, context-window usage),
  and a start-up banner showing model, permission mode and workspace.
- Docs reorganised under `docs/` with English and Chinese versions of the
  architecture and web-console pages.
- `Dockerfile` no longer installs a browser by default.

### Fixed
- A shell command that exits non-zero is now reported as a failed tool call in
  the UI rather than a successful one.

## 0.1.0 — 2026-09

### Runtime
- Agent loop with Anthropic and OpenAI protocol adapters, streaming, tool
  dispatch, plan mode, session save/resume, sub-agents and an MCP stdio client.
- Permission gate: five modes, `settings.json` allow/deny rules, destructive
  shell-command detection; sub-agents bounded by the parent's mode; MCP and
  unclassified tools gated by default.
- Context management: per-result budget, stale-result pruning, and folding of
  history into episodic / working / tool memory.
- Loop guard on tool dispatch, windowed on tool name and gated on result
  diversity; graceful budget exhaustion (one best-effort answer at the turn cap).
- Long-term memory isolated per project; runtime paths centralised in
  `agents/paths.py` (`$KEELSON_HOME`).

### Skill evolution
- Online extraction from user feedback (Extractor → Maintainer →
  add / merge / discard), with a zero-model pre-filter so only feedback-shaped
  turns reach the expensive path.
- Non-inferiority gate on merges: a candidate that regresses on the skill's own
  replay history is quarantined and the live file restored.
- Version snapshots, `/skill-history`, `/skill-diff`, `/skill-rollback`, and an
  Evolution Card for every add, merge and quarantine.
- A real admit and a real quarantine, produced by the pipeline against a real
  model: [`docs/self-evolution-demo.md`](docs/self-evolution-demo.md).

### Evidence
- ToolHop benchmark module: offline tool execution, hop-stratified split,
  paired cluster bootstrap and McNemar. The benchmark surfaced a prompt bug in
  its own harness arm (a tool-call spiral to the turn cap); after the fix,
  accuracy went from 72.8% to 79.4% and turn-cap hits from 15/180 to 0/180.
  Write-up: [`bench/spiral-diagnosis.md`](bench/spiral-diagnosis.md).
- Sandbox coding benchmark: 14 tasks including two adversarial traps, with a
  grader that restores the pristine tests before scoring. Zero test tampering
  and zero out-of-scope edits across 56 runs; one verification-skipping failure
  found and followed up.
- 183 tests, all runnable without an API key.

### Fixed during this release
- OpenAI branch executed tool-call batches O(N²) times with duplicate ids.
- OpenAI branch left `tool_calls` unanswered at the turn cap, so the next
  request failed with a 400.
- The non-streaming OpenAI path was the only model call without retry.
- Quarantined skill candidates produced no Evolution Card.
- File reads and writes without an explicit encoding broke on GBK consoles.

## Not done yet

- Fold-fidelity numbers: the probe (`agents/bench/fold_fidelity.py`) exists; it
  still needs a transcript corpus and a model-backed run.
- An A/B of speculative tool execution (Anthropic protocol only): spans now
  measure every step, but the gateway used for the recorded runs speaks the
  OpenAI protocol, where the feature does not apply. Also still unmeasured: the
  token saving of the feedback pre-filter.
- Web sessions share one working directory. Isolating files as well would need
  a worktree per session.
- From the case study, written up in [`docs/case-study.md`](docs/case-study.md):
  the loop guard misses probing whose output differs slightly each time (one
  issue failed all three rounds this way); the permission gate judges the text
  of a call, so a model refused `write_file` used `echo > file` instead — it is
  not a sandbox; `pip install` from the network needs no confirmation.
- Plan-mode approval in the web console.
- Agreement (κ) between the LLM judge and human labels on a 50-sample set.
- Two known defects, documented in
  [`docs/design-tradeoffs.md`](docs/design-tradeoffs.md): sub-agent construction
  drops `api_key` / `anthropic_base_url`, and the OpenAI and Anthropic pruning
  paths are not symmetric.

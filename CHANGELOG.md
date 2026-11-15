# Changelog

Notable changes, newest first. Benchmark figures quoted here are reproduced in
[`bench/REPORT.md`](bench/REPORT.md) from the committed run artifacts.

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
- Latency P50/P95 for speculative tool execution, and the token saving of the
  feedback pre-filter.
- Agreement (κ) between the LLM judge and human labels on a 50-sample set.
- Two known defects, documented in
  [`docs/design-tradeoffs.md`](docs/design-tradeoffs.md): sub-agent construction
  drops `api_key` / `anthropic_base_url`, and the OpenAI and Anthropic pruning
  paths are not symmetric.

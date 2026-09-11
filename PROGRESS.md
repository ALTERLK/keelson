# Keelson — build progress

Status of the "harness → evidence" work. ⏳ = needs a working model API key.

## Deploy / environment
- Project renamed to Keelson early on (folder rename done manually, VS Code
  had a lock on it); venv rebuilt at the new path for Python 3.11, pip/temp
  caches off C:
- `pip install -e ".[dev,bench]"` works; `keelson` console entry point works
- `.env` configured for 4SAPI (OpenAI-compatible, `https://4sapi.com/v1`,
  `MODEL=gemini-3.8-flash`, `KEELSON_OPENAI_STREAM=0`). See "Cost" below —
  budget is not a constraint (~¥0.03/task).
- All work is committed on branch `round2-loop-fix` (7 commits as of round 2,
  authored solely by the user — no AI co-author trailers).

## Stage 0 — stop the bleeding  ✅
- `--thinking` crash, plan-approval (`await` / path-vs-content / mode-not-restored) — fixed
- ~23 `read_text/write_text/open` missing `encoding=` — fixed (+ `newline="\n"`)
- spinner `UnicodeEncodeError` on GBK console — fixed (stdio forced to UTF-8)
- **OpenAI branch O(N²) tool-call loop** — batch build/exec moved out of the
  per-call loop; regression test `tests/test_openai_tool_loop.py`
  (matters: 4SAPI is OpenAI-compatible)
- duplicate `get_active_tool_definitions` removed

## Stage 1 — Keelson branding + path consolidation  ✅
- `agents/*.py`, `README.md`, `.env.example` rebranded
- `agents/paths.py` — one runtime-path source of truth; `$KEELSON_HOME`
  (default `~/.keelson`), replacing per-module copy-pasted path logic
- sessions now project-scoped (`--resume` can't leak across projects)
- an early prototype's legacy env-var aliases and fallback directory names
  were carried for a while, then dropped outright once nothing needed them —
  no deprecated-alias surface left to explain or maintain

## Stage 2 — ToolHop bench module  ✅ (run done)
- `agents/bench/{toolhop,splits,metrics,runner}.py`
- 940/940 safe tasks build + execute their tool bodies offline, zero stdout leak
- deterministic hop-stratified split; `bench/splits.json` + `bench/toolhop_subset.json` committed
- paired bootstrap CI + McNemar in `metrics.py`
- 4-arm runner with per-arm `KEELSON_HOME` + cwd isolation; `skill_v1` skill authored
- **Round 1** (`gemini-3.8-flash` / 4SAPI, n=60 test split, `max_turns=20`):
  raw_api 80.0%, no_skill 71.7%, skill_v1 73.3%; invalid_tool_call_rate 0%
  across all 180 task-runs. Found a specific, directional gap: harness arms
  ~8pp below the bare loop, *entirely* from 7 tasks spiraling to the 20-turn cap
  (raw_api never hit its cap; on the other 48 tasks all three arms were
  identical, 92/90/90%).
- `skill_evolved` probed (`agents/bench/skill_evolved_probe.py`, 12 train
  transcripts → 0 candidates): no user-feedback turn on ToolHop, so it collapses
  to `no_skill`. Not a separate arm on this dataset.
- Surfaced + fixed 4 harness bugs in round 1 (O(N²) OpenAI loop, Gemini
  streaming drops tool calls → `KEELSON_OPENAI_STREAM=0`, coding prompt vs
  domain tools, un-classified tool gate vs injected tools).

## Round 2 — root-caused and fixed the spiral  ✅
- **Diagnosis** (`bench/spiral-diagnosis.md`): payload diff on a spiraling task
  showed the harness request differs from `raw_api`'s in exactly one way — a
  system message (ruled out message-fidelity loss as a cause). A/no-sysprompt/raw
  ablation on the 7 affected tasks confirmed it: strip the harness's system
  prompt and it collapses onto `raw_api`'s numbers on every axis. Root cause:
  `_HARNESS_SYSTEM_PROMPT` ("call one tool per hop … output only: ANSWER:") read
  as "not allowed to finalise," so the model fuzzed tool arguments (not
  identical repeats) to the cap — confirmed non-terminating at `max_turns=40`.
- **Fix**: reworded the prompt to permit finalising, plus two independent
  hardening changes — a loop guard in `_execute_tool_call` (windowed on tool
  *name* since args were fuzzed; `KEELSON_LOOP_GUARD`) and graceful budget
  exhaustion (one best-effort answer on turn-cap instead of nothing, both
  backends; also fixed a real bug where the OpenAI branch left `tool_calls`
  unanswered on cap → next `chat()` 400s). `_call_openai_nonstream` also now
  retries transient errors like every other model call.
- **Re-measured, n=60 pooled ×2–3 repeats**: accuracy 72.8% → **79.4%**,
  turn-cap rate 8.3% → **0.0%** (15/180 → 0/180, a direct count), Δ +6.7pp,
  95% CI [+1.7%, +12.8%] excludes zero. Loop guard triggered **0 times**
  post-fix — the prompt fix alone was sufficient; the guard is now a backstop.
  Full numbers: `bench/REPORT.md`.
- Honest caveat kept in the report: `no_skill` now nominally beats a
  non-interleaved `raw_api` re-run by +3.6% (significant), but `raw_api`'s own
  accuracy has swung 4.2pp between sessions before — read as "reached parity,"
  not "now wins."
- Ground truth for round 1 is unchanged: ToolHop still can't exercise
  permissions/folding/recovery — sandbox + fold_fidelity still need a model
  driver / task authoring (see Pending).

## Stage 4 — gate + rollback + history + card  ✅
- `agents/skill_history.py` — `list_history` / `diff` / reversible `rollback`;
  `/skill-history`, `/skill-diff`, `/skill-rollback`
- `agents/skill_gate.py` — non-inferiority gate (programmatic rules, no model);
  wired into `skills.evolve_skill(gate=True)` + the online merge path → a regressing
  candidate is quarantined and the live file rolled back
- `agents/evolution_card.py` — every online add/merge (and, since round 4,
  quarantine) writes an audit card (evidence → candidate → decision → diff →
  gate → version)

## Round 4 — self-evolution live-fired: a real promotion, a real quarantine  ✅
- Every skill in this project's actual history had `promoted: true` **zero**
  times before this round — the gate only ever fires on a skill's *second*
  evolution (the first has no replay sample yet → `ungated`), and no skill had
  ever been evolved twice with real usage in between.
- `scripts/demo_self_evolution.py`: two isolated skill lineages, each driven
  through two real rounds of `online_ingest()` (Extractor → Maintainer →
  `evolve_skill(gate=True)`) with a real model. Both landed on their intended
  gate outcome **naturally**, no scripted fallback needed: lineage A —
  `admit` (merge added no new hard rule); lineage B — `quarantine` (merge
  added a "must cite sources" rule the round-1 replay reply never satisfied;
  live file rolled back, quarantine file written).
- Fixed two real gaps found while building this: (1) quarantined candidates
  got no Evolution Card at all — now they do, titled "— REJECTED"; (2)
  `_coerce_candidate` rendered a list-shaped `instructions` field (a model
  occasionally returns one instead of a string) as a raw Python list repr —
  now joined into bullet lines.
- New end-to-end tests (no API key) drive the real `skills.evolve_skill(gate=
  True)` path directly — not just `gate_evolution()` in isolation — proving
  the exact same mechanism deterministically: `tests/test_skill_gate.py`,
  `tests/test_evolution_card.py`, `tests/test_online_skill_evolution.py`.
- Write-up mapping both lineages to the hardest recurring interview questions
  (自进化算不算 / 评判闭环 / 全链路 / 回答稳定性):
  `docs/self-evolution-demo.md`. Artifacts: `docs/self-evolution-demo/
  lineage-a-admit/`, `lineage-b-quarantine/`.

## Stage 5 — judge / actor + permission holes  ✅
- `KEELSON_JUDGE_MODEL` — side-query / judge calls can use a different model
- `no_unfounded_claims` reclassified `hard=False` — LLM-judge rules never gate a write
- sub-agents bounded by parent mode (`_subagent_permission_mode`, default `dontAsk`)
- MCP / un-classified tools now gated in `check_permission`
- ⏳ 50-sample human-labelled judge agreement (κ) — needs a run + labels

## Stage 6 — engineering baseline  ✅
- `pyproject.toml`, `agents/__init__.py`, `.gitattributes`, GitHub Actions CI
- **183 passing tests**, up from 140 baseline — round 2 added loop-guard/
  budget-exhaustion/nonstream-retry/runner-diagnostics coverage, round 3 added
  sandbox-grader/sandbox-runner coverage, round 4 added skill-gate end-to-end/
  evolution-card/candidate-coercion coverage; `ruff check .` clean
- `frontmatter.py` block-scalar bug fixed
- `data/` (74 MB) + `.idea/` out of git index; `scripts/fetch_data.py` + `data/README.md`

## Stage 7 — cost gating  ✅ (measurement pending ⏳)
- `agents/skill_gating.py::looks_like_feedback` — zero-model pre-filter; the
  Extractor/Maintainer path is skipped unless the turn looks like durable feedback
  (`KEELSON_SKILL_GATING`, bypassed by `/extract_now`)
- ⏳ token-savings / recall ablation vs. gate-off

## Stage 8 — perf trace  ✅ (measurement pending ⏳)
- `agents/trace.py` — span sink + summary; `model_stream` spans + `speculative_start`
- `KEELSON_SPECULATIVE=0/1` gates the streaming early-tool-exec path for the A/B
- ⏳ P50/P95 latency delta

## Stage 9 — fold fidelity  ✅ (measurement pending ⏳)
- `agents/bench/fold_fidelity.py` — programmatic probe extraction from a transcript
  (episode / working / tool classes) + containment grading + truncation control
- ⏳ retention numbers (needs model-backed fold + answer)

## Stage 10 — coding sandbox  ✅ (round 3, run done)
- Grader holes closed: `grade_workdir` restores the pristine test surface
  (deleted or rewritten test files, injected pytest.ini/conftest.py) *before*
  grading, not after — tampering can no longer buy a passing score. Deleted
  files are now detected (previously only new/changed were).
- Task set 3 → **14**: added `refactor` (cross-file rename), `traceback`
  (instruction is only a stack trace), `trap` ×2 (tempting "make this test
  pass" wording; a duplicated buggy line that makes a naive `edit_file`
  `old_string` non-unique). Every task ships a `solution/`, validated
  fail-then-pass with `sandbox_runner.py --validate` (no API key).
- `agents/bench/sandbox_runner.py`: real Agent plugged in, two arms over the
  *same* 7 tools + *same* real system prompt (harness = bypassPermissions +
  read-before-edit gate + loop guard + budget exhaustion; raw = bare loop,
  gate off).
- **Ran gemini-3.8-flash (weak) + gpt-6-astra (strong), n=14/cell,
  descriptive**: solve rate raw/harness 100%/100% (weak), 100%/92.9% (strong);
  0% test-tampering and 0% out-of-scope edits across all 56 runs; harness uses
  fewer turns *and* fewer tokens than raw at both tiers. One failure
  (harness/strong, unverified edit → syntax error on a 2-file rename),
  followed up with 3 more reps: 3/4 pass vs raw's 4/4 on that cell — a real,
  reproducible-in-kind edit-safety miss, not (from n=4) a systematic one.
  Full numbers: `bench/REPORT.md`.

## Stage 11 — outward materials
- `docs/design-tradeoffs.md` ✅ (round 1 + round 2 read, the 8 bugs, 2 found-not-fixed,
  skill_evolved probe)
- `bench/REPORT.md` ✅ — before/after + per-arm, regen with `scripts/bench_report.py --set ...`
- README intro + eval section rewritten ✅; full rewrite still worthwhile ⏳
- demo GIF ⏳ (needs a running agent)

## Cost
The 4SAPI dashboard is authoritative (the runner's `est_cost_usd` uses a generic
$0.30/$2.50-per-1M rate and overstates actual `gemini-3.8-flash` pricing ~5×).
Round 1's entire first session was **¥2.56** (~$0.36), ≈¥0.03/task. Round 2's
diagnosis + fix + re-measure roughly tripled the task volume — order ¥8–12.
Round 3's sandbox run (56 + 6 follow-up model calls, real `build_system_prompt()`
so ~2-4x the tokens/call of ToolHop's neutral prompt, plus a strong model of
unknown per-token price) is the least certain — order ¥10–20. Round 4's
self-evolution demo was 2 dry-run + 2 real lineages × 2 rounds each ≈ 4 real
model calls total, well under ¥1. Budget has not been the constraint in any
round.

## Pending — needs benchmark-building, not budget
- ✅ ~~sandbox coding bench~~ — done, round 3 (see Stage 10).
- ⏳ **fold-fidelity**: `fold_fidelity.py` probe extractor is done; needs a CLI +
  a transcript corpus + a model-backed fold to produce retention numbers.
- ⏳ latency P50/P95, gating token-savings %, 50-sample judge-agreement κ.
- ✅ `skill_evolved` — resolved for ToolHop (probe: 0 candidates, collapses to
  `no_skill`); belongs on the sandbox bench where a correction signal exists.
- **found, not fixed** (see `docs/design-tradeoffs.md`): sub-agent construction
  drops `api_key`/`anthropic_base_url`; OpenAI vs Anthropic snip asymmetry.

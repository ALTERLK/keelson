# Benchmark report — ToolHop (multi-hop tool calling)

**Model:** `gemini-3.8-flash` via 4SAPI (`https://4sapi.com/v1`, OpenAI-compatible, non-streaming — the gateway drops streamed tool-call deltas).
**Dataset:** ToolHop (`data/ToolHop/ToolHop.json`); 120-task hop-stratified subset in `bench/splits.json` (salt `keelson-toolhop-v1`), 60 train / 60 test. Results below are the **test** split.
**Scoring:** exact gold-answer match, normalized per `answer_type` (`keelson/bench/toolhop.py::score`); no LLM judge in the pass/fail path.
**Arms:** `raw_api` = bare model function-calling loop. `no_skill` / `skill_v1` = full Runtime (loop, permission gate, tool dispatch, context fold, per-turn skill retrieval, loop guard) under `bypassPermissions`; `skill_v1` also gets the hand-written `multihop-tool-chaining` skill.

This is round 2. Round 1 (n=60, single run) found the harness ~8pp below `raw_api`, traced it to 7 tasks spiraling to the turn cap, and shipped a fix. Everything below is post-fix, run with repeats for a noise band. The investigation and the pre-fix numbers are in [`bench/spiral-diagnosis.md`](spiral-diagnosis.md) — kept as the record, not superseded.

## The bug, in one paragraph

`_HARNESS_SYSTEM_PROMPT` (the bench's injected task prompt, not harness default behaviour) said *"call one tool per hop … output only: ANSWER:"*. On an ambiguous tool result, `gemini-3.8-flash` read that as "not allowed to finalise yet" and kept calling tools — not repeating identical calls, but **fuzzing arguments** (name casing, alternate fields) on the same tool, up to the 40-turn cap in a follow-up test. `raw_api` (no system prompt) never did this. Payload diffs ruled out message-fidelity loss; an A/B/raw ablation on the 7 affected tasks nailed the system prompt as sole cause (harness-without-that-prompt collapsed onto `raw_api`'s numbers on every axis). Fix: reworded the prompt to explicitly permit finalising, plus two independent hardening changes that would have caught this regardless of prompt wording — a loop guard (execution-time, windowed on tool *name* since args were fuzzed) and graceful budget exhaustion (one best-effort answer instead of nothing, on both backends). Full writeup: [`bench/spiral-diagnosis.md`](spiral-diagnosis.md).

## Before / after (no_skill, n=60 test split, pooled over repeats)

| | before (pre-fix, commit `29bb79e`, ×3) | after (post-fix, ×3) |
|---|--:|--:|
| accuracy | 72.8% (131/180 rows, 60 tasks) | **79.4%** (143/180 rows) |
| turn-cap hit rate | 8.3% (15/180) | **0.0%** (0/180) |
| avg model calls/task | 7.9 | 5.7 |
| loop-guard triggers | n/a (didn't exist) | **0** (fix alone was sufficient; guard never had to fire) |

**Δ accuracy (after − before) = +6.7%, 95% CI [+1.7%, +12.8%], cluster bootstrap over 60 tasks — significant.** The turn-cap rate is a direct count, not an estimate: 15/180 → 0/180.

## Per-arm, post-fix (n=60 test split, pooled)

| arm | rows | accuracy | hit-cap rate | avg model calls | invalid-tool-call rate |
|-----|-----:|---------:|--------------:|-----------------:|-----------------------:|
| `raw_api` (×2) | 120 | 75.8% | 0.8% | 6.3 | 0% |
| `no_skill` (×3) | 180 | **79.4%** | 0.0% | 5.7 | 0% |
| `skill_v1` (×3, ×2 in this run) | 120 | 78.3% | 0.0% | 5.7 | 0% |

5 of 60 tasks (262, 416, 427, 552, 555) fail with a gateway `400` in every arm (duplicate function name, tool-schema value 4SAPI's Gemini bridge rejects) — symmetric, counted as wrong everywhere.

## Paired comparison (per-task mean hit rate, 10 000-iter cluster bootstrap)

| A − B | Δ accuracy | 95% CI | significant? |
|-------|-----------:|:------:|:------------:|
| `no_skill` − `raw_api` | +3.6% | [+0.8%, +7.2%] | yes (see caveat) |
| `skill_v1` − `no_skill` | −1.1% | [−3.6%, +0.6%] | no |
| `skill_v1` − `raw_api` | +2.5% | [+0.0%, +5.8%] | no |

**Caveat on `no_skill` − `raw_api`:** these two arms were run sequentially in the same session, not interleaved, and `raw_api`'s own accuracy has moved between sessions before (80.0% in round 1's single n=60 run vs 75.8% here — a 4.2pp swing, the same order as this "significant" gap). **Read this as "the harness reached parity with the bare loop," not "the harness now beats it"** — the fix's job was to stop the harness from being *worse*, and it did; a genuine harness-beats-raw claim would need an interleaved re-run, which is cheap but wasn't the point of this round.

## Accuracy by hop depth (n=60, post-fix, common tasks)

Shown as accuracy % (row counts differ by arm — `no_skill` pooled ×3 repeats, `raw_api`/`skill_v1` ×2 — task counts are identical across arms).

| hops | n tasks | `raw_api` | `no_skill` | `skill_v1` |
|-----:|--------:|----------:|-----------:|-----------:|
| 3 | 27 | 87.0% | 90.1% | 90.7% |
| 4 | 21 | 78.6% | 84.1% | 81.0% |
| 5 | 7 | 78.6% | 81.0% | 78.6% |
| 6 | 1 | 0% | 0% | 0% |
| 7 | 4 | 0% | 0% | 0% |

Every arm is 0 at 6–7 hops — the chain outgrows `gemini-3.8-flash` regardless of harness; that ceiling didn't move and was never the bug.

## `skill_v1` reading (unchanged from round 1)

`skill_v1` − `no_skill` is a clean null (−1.1%, CI crosses 0). A generic "chain your tools" skill neither helps nor hurts a model that already chains once the harness itself isn't malfunctioning.

## `skill_evolved` — not a distinct arm on this dataset

`keelson/bench/skill_evolved_probe.py` drove the real evolution entry point (`extract_online_skill_candidate`, model-backed `side_query`) over 12 real train-task transcripts: **0 candidates extracted**. `extract_online_skill_candidate`'s own rules key on a user-feedback turn ("USER turns are the primary evidence … extract only durable workflow, correction, or repeated constraint"). A ToolHop task is one question turn with no feedback, so `skill_evolved` collapses to `no_skill` here by construction. The evolved-skill path needs a bench with a correction signal — the sandbox coding tasks below, once built out.

## Sandbox coding benchmark — round 3

ToolHop is a clean function-calling task; it can't exercise `edit_file` safety, out-of-scope edits, test-tampering, or read-before-edit. `keelson/bench/sandbox.py` + `keelson/bench/sandbox_runner.py` measure that directly: 14 small coding tasks (bugfix/feature/edge-case/refactor/traceback/trap), each with a reference `solution/` validated to fail-then-pass before any model call. Two arms over the *same* 7 tools and the *same* real `build_system_prompt()` text (the ToolHop H2 lesson: mismatched prompts between arms confound the comparison) — `harness` = real Agent (`bypassPermissions`, read-before-edit gate, loop guard, graceful budget exhaustion); `raw` = bare tool-calling loop, gate off. **Descriptive statistics — n=14 per cell, no significance testing.**

Two models: `gemini-3.8-flash` (weak, same as ToolHop) and `gpt-6-astra` (strong).

| arm / model | solve rate | test-tamper | out-of-scope edits | avg turns | avg input tokens |
|---|---:|---:|---:|---:|---:|
| `raw` / weak | 100% (14/14) | 0% | 0% | 10.8 | 44.4k |
| `harness` / weak | 100% (14/14) | 0% | 0% | 9.1 | 43.0k |
| `raw` / strong | 100% (14/14) | 0% | 0% | 5.0 | 18.5k |
| `harness` / strong | 92.9% (13/14) | 0% | 0% | 3.6 | 17.4k |

Zero test-tampering and zero out-of-scope edits across all 56 runs, including both `trap` tasks (one phrased as "make this test pass," inviting the model to edit the test instead of the real bug — no model took the bait; the grader's pristine-test restore would have caught it if they had). `harness` uses fewer turns and fewer input tokens than `raw` at **both** model tiers — consistent with a more structured tool-use prompt paying for itself in efficiency, not just adding overhead (the opposite of what ToolHop's round-1 finding looked like, before that turned out to be a prompt bug rather than a harness property).

**The one failure, followed up rather than left as a single data point:** `harness`/strong on `rename-max-retries-constant` (a 2-file constant rename) produced `while attempts < MAX_RETRIES::` — a naive replace that collided with an existing colon, a syntax error, shipped without verification (5 turns, no `run_shell`/`grep_search` check before stopping). `raw`/strong solved the same task in its one sample by spending 2 extra turns to verify ("Verified there are no remaining MAX_RETRY references"). Re-running that one cell 3 more times each: `harness`/strong went 3/4 pass overall (3,4,5 turns on the successes), `raw`/strong went 4/4 (5,5,5,7 turns). **Read as an occasional verification-skipping failure mode on this model, not a systematic harness defect** — n=4 is far too small to separate from noise, but it's a genuine, reproducible-in-kind edit-safety miss of exactly the shape this benchmark exists to surface, and it appeared under the harness, not under raw, in every sample so far.

Full artifacts: `bench/runs/20260911T024427Z-sandbox-raw-gemini-3.8-flash`, `...024434Z-...harness-gemini-3.8-flash`, `...024441Z-...raw-gpt-6-astra`, `...024447Z-...harness-gpt-6-astra` (main 14-task batches); `...025527Z-...harness-gpt-6-astra` / `...025734Z-...raw-gpt-6-astra` (the 3-repeat follow-up on `rename-max-retries-constant` only).

## Where harness value would actually show — remaining gaps

| vehicle | what it tests | state |
|---------|---------------|-------|
| `keelson/bench/fold_fidelity.py` | does a context fold keep task-critical facts vs. plain truncation | fact-probe extractor is written; **no CLI, no transcript corpus, unrun.** |
| `keelson/skill_gate.py` | non-inferiority gate blocks a regressing skill | unit-tested only; no end-to-end bench. |

Not yet measured — needs benchmark-building, not more spend. Sandbox above closes the coding-task gap; these two are what's left.

## Bugs fixed while building this

Round 1:
1. **OpenAI-branch O(N²) tool-call loop** — batch build/exec nested in `for tc in tool_calls:`; N calls ran the set O(N²) times with duplicate `tool_call_id`s. `tests/test_openai_tool_loop.py`.
2. **4SAPI Gemini streaming drops `delta.tool_calls`** — added `_call_openai_nonstream` + `KEELSON_OPENAI_STREAM=0`.
3. **The default coding-agent system prompt** made `gemini-3.8-flash` ignore injected domain tools and answer from memory. Bench arms use a neutral task prompt.
4. **Un-classified-tool permission gate** auto-denied the benchmark's injected tools under `dontAsk`. Bench arms use `bypassPermissions`.

Round 2:
5. **Bench system prompt forbade finalising** (this report's headline bug) — root-caused via payload diff + A/B/raw ablation, fixed by rewording. `bench/spiral-diagnosis.md`.
6. **OpenAI turn-cap left `tool_calls` unanswered** — the branch `break`d without appending `role:tool` messages for pending calls, so a follow-up `chat()` on the same Agent would 400. Now satisfied before the finalize attempt, on both backends.
7. **`_call_openai_nonstream` didn't retry** — the only model-call path not wrapped in `_with_retry`; a transient 429/503 killed the whole task. `tests/test_openai_nonstream.py`.
8. **No loop guard on tool dispatch** — nothing stopped a model from hammering one tool with reworded arguments. Added, windowed on tool name (not args — the failure mode fuzzes them), gated on result diversity so 6 reads of 6 different files never trips it. `tests/test_loop_guard.py`.

## Cost

The runner's `est_cost_usd` uses a generic $0.30/$2.50-per-1M-token estimate and overstates 4SAPI's actual `gemini-3.8-flash` pricing by ~5×. The 4SAPI usage dashboard is authoritative: round 1's entire first session (~3 arms × n≈25–60 + smokes + debugging) was **¥2.56** (~$0.36), ≈¥0.03/task. Round 2's diagnosis + fix + re-measure (payload diffs, the A/B/raw ablation, before/after at n=60 ×2–3) roughly triples the task volume — order ¥8–12, still not a budget constraint.

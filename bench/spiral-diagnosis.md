# Diagnosis: why the harness arms spiral on 7 ToolHop tasks

The n=60 run showed `no_skill`/`skill_v1` ~8pp below `raw_api`, entirely from 7
tasks (314, 392, 453, 578, 636, 642, 787) that burn the 20-turn cap with no
answer. This is the investigation.

## 1. Payload diff (task 314, first request) — rules out message-fidelity loss

`--dump-payloads` on `no_skill` vs `raw_api`. The two requests are identical
except:

- `no_skill` carries a `system` message; `raw_api` sends none.
- assistant turns stored back to history: `no_skill` `content: null`,
  `raw_api` `content: ""`. `tool_calls` are the same shape in both.

There is **no** Gemini thought-signature / reasoning field anywhere on the
`tool_call`, the message, or the choice — `tool_call` keys are `function/id/type`
only. 4SAPI's OpenAI bridge does not expose one, so `_call_openai_nonstream`
rebuilding the tool_call drops nothing. **H1 (fidelity loss) is impossible here.**

## 2. Ablation A / C / D — confirms the system prompt is the cause

7 spiral tasks x3 repeats, gemini-3.8-flash / 4SAPI.

| arm | hit_cap | acc | median model_calls |
|-----|--------:|----:|-------------------:|
| A  `no_skill` as-is                    | 11/21 | 4/21 | 20 |
| C  `no_skill --variant no_sysprompt`   |  2/21 | 9/21 | 12 |
| D  `raw_api`                           |  1/21 | 9/21 | 12 |

Strip the harness system message and the harness collapses onto the bare loop on
every axis — cap rate, accuracy (9/21 == 9/21, so C did *not* "fix" it by letting
the model answer from memory), median calls, and per-task hit rates.

**Root cause:** `_HARNESS_SYSTEM_PROMPT` in `agents/bench/runner.py` —
> "Never answer from your own knowledge. Decompose ... call one tool per hop ...
> When every hop is done, output only: ANSWER: <final answer>"

On an ambiguous tool result `gemini-3.8-flash` reads this as *not permitted to
finalise yet* and keeps calling tools. This string is the **bench's** injected
prompt (passed via `custom_system_prompt`), not harness default behaviour — the
harness default was never implicated.

## 3. Loop shape (A at max_turns=40, tasks 453/787/578)

453 and 787 spiral to the full 40 turns — genuine non-termination, not slow
convergence. The pattern is **argument fuzzing**: one tool (`genealogy_query`)
called ~35x in a row with tweaked args (name casing, `data_source`,
`handle_discrepancies` flags). Longest identical-args streak = 2.

=> a consecutive-identical-args guard would never fire. The guard must be
**windowed on tool name**: N calls to the same tool within the last M turns
without a new distinct result -> nudge, then force-finalise.

## Fixes (Day 2)

1. Rewrite `_HARNESS_SYSTEM_PROMPT` so it permits finalising (keep "use the
   tools, don't answer from memory"; drop "one tool per hop" + "output only
   ANSWER:"). Re-measure to confirm the gap closes.
2. Defensive loop guard in `_execute_tool_call` (windowed on tool name) — catches
   this class regardless of prompt wording.
3. Graceful budget exhaustion: on turn cap, one tool-less call for a best-effort
   answer instead of `prediction=None`. Also fixes the OpenAI dangling-history
   bug (cap `break` leaves tool_calls unanswered).

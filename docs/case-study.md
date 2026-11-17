# Case study: real issues in real projects

[中文](zh/case-study.md)

**The conclusion first.** Keelson was given five real issues from open-source projects, each already fixed by its maintainers, and graded with the projects' own tests. Three rounds: 3/5, 3/5, 4/5 resolved. That count says very little. There are five issues, one run per issue per round, and the same issue can flip between rounds on model variance alone.

What the rounds are worth is something else: **they exposed five defects in Keelson itself** that neither the unit tests nor the ten-line sandbox tasks had shown. All five are fixed, each with a test. Four more problems are not fixed, and [the reasons are below](#what-is-not-fixed). The most serious of the five: on Windows, a shell command of several lines ran only its first line and dropped the rest in silence. Of 40 multi-line commands in the first two rounds, none ran completely.

Everything is in the repository. [`bench/casestudy/`](../bench/casestudy/) holds the case list, the runner and the results of all three rounds; the event streams of all 15 runs are in [`demo/traces/`](../demo/traces/) and can be replayed from the Cases page of the web console.

## How a case is judged

The protocol is the one SWE-bench uses.

1. The project is checked out at the commit **before** the maintainers' fix.
2. Keelson gets the issue text and nothing else. It does not see the tests added with the fix, and its checkout has no `.git`, so the fix cannot be looked up. (In 6 of the 15 runs it tried `git diff`, `git status` or `git log -p`. All failed.)
3. When it stops, the maintainers' version of the test files is put in place and run. A case counts as resolved only if **every test written for the fix passes** (fail-to-pass) and **nothing that passed before is broken**.

Keelson runs unattended in `dontAsk` mode: whatever the permission gate would have asked a person about is refused. Each case gets at most 30 steps and 15 minutes. The model is `gemini-3.8-flash` through an OpenAI-compatible gateway, non-streaming, on Windows 11.

Before any grading the cases themselves are checked (`run.py validate`, no model involved): at the old commit the target tests must fail, and with the upstream fix they must pass. All five passed. The tenacity test does not fail on the old code, it never returns, which is the bug.

## The issues

| Project | Issue | Problem | Upstream fix |
|---|---|---|---|
| more-itertools | [#1308](https://github.com/more-itertools/more-itertools/issues/1308) | `bucket()` picks its validator with `validator or default`, so a callable that happens to be falsey is silently replaced | +4 −1 |
| cachetools | [#440](https://github.com/tkem/cachetools/issues/440) | `TTLCache.__setitem__` stores the value before computing the expiry; if the addition overflows, a half-inserted entry is left behind | +2 −1 |
| python-dotenv | [#699](https://github.com/theskumar/python-dotenv/issues/699) | `dotenv get KEY` exits with code 1 for a key that is present but empty | +3 −4 |
| humanize | [#205](https://github.com/python-humanize/humanize/issues/205) | `naturalsize()` chooses the unit before rounding, so a value just under a boundary prints as `1000.0 ZB` | +6 −0 |
| tenacity | [#658](https://github.com/jd/tenacity/issues/658) | `retry_if_exception_cause_type` loops forever on a cyclic cause chain (`raise e from e`) | +7 −2 |

They were chosen for being pure Python, having tests that run offline in under a minute, and having a fix commit that came with tests. To be plain about it: **all five are single-file fixes of a few lines, the easy end of SWE-bench**, and I picked them; they are not a sample. Four of the five were filed between July and October 2026.

## Results

| | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| Runtime | as it was (`6e62a9e`) | loop guard and empty reply fixed | multi-line commands fixed too |
| **Issues resolved** | **3 / 5** | **3 / 5** | **4 / 5** |
| Model calls, total | 113 | 117 | 110 |
| Input tokens, total | 1.40 M | 1.21 M | 1.52 M |
| Runs that hit the 30-step limit | 3 | 1 | 2 |
| Loop-guard trips | 6 | 1 | 0 |
| Multi-line shell commands run completely / all | 0 / 30 | 0 / 10 | 3 / 6 (the other 3 refused with the reason) |
| Permission-gate denials | 5 | 7 | 8 |
| Runs that removed lines from existing tests | 0 | 0 | 0 |

Issue by issue (✓ resolved, ✗ not, then the number of model calls):

| Issue | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| more-itertools #1308 | ✗ 2 | ✓ 29 | ✓ 30 |
| cachetools #440 | ✓ 30 | ✗ 19 | ✓ 20 |
| python-dotenv #699 | ✓ 30 | ✓ 16 | ✓ 15 |
| humanize #205 | ✗ 30 | ✗ 30 | ✗ 30 |
| tenacity #658 | ✓ 21 | ✓ 23 | ✓ 15 |

How to read the two tables:

- **The resolved count and the total cost barely moved.** 3, 3, 4 is within noise, and neither calls nor tokens went down. I do not claim the fixes raised the success rate.
- **What changed is specific failure modes.** Loop-guard trips went from 6 to 0. Multi-line commands are no longer cut short in silence: none of 40 ran completely in the first two rounds, and in round 3 each one either ran completely or was refused with the reason. The "empty reply, turn over" of round 1 did not happen again.
- **cachetools passed, failed, then passed**, and nothing relevant in the runtime got worse in between: in round 2 the model simply wrote a wrong fix. That is how noisy a single run is.

## Five defects found and fixed

Each is written as symptom, evidence, cause, fix. The evidence is a trace that can be replayed.

### 1. An empty model reply was taken as "done"

- **Symptom**: more-itertools in round 1 ended after ten seconds with nothing changed.
- **Evidence**: trace `21-case-more-itertools-1308-round1`. The model listed files once, and the next completion had no text and no tool call (zero output tokens).
- **Cause**: the loop ends when a reply contains no tool call. An empty reply satisfies that.
- **Fix**: a reply with no text and no tool call is a failed call. It is retried with a one-line nudge, at most twice; only then does the turn end, with a warning (`agents/agent.py::_should_retry_empty_reply`, `tests/test_empty_reply.py`).
- **How far it is verified**: no empty reply occurred in the later rounds, so this fix has unit tests and no live confirmation.

### 2. The model could reset the loop guard

- **Symptom**: cachetools in round 1. The loop guard tripped four times and the model carried on to the 30-step limit.
- **Evidence**: trace `22-case-cachetools-440-round1`. The model called `compact_context` and gave this as its reason: "The previous tool calls were blocked by a loop guard, need to reset to confirm the fix."
- **Cause**: folding the context also cleared the guard's window of recent calls, and `compact_context` is a tool the model can call. The reset button was in the hands of the thing being limited.
- **Fix**: a fold leaves the window alone. Folding changes what the model can see, not what it has just been doing (`_record_fold_event`, `test_a_fold_does_not_reset_the_guard`).

### 3. The loop guard only asked

- **Symptom**: the same run. Four times the guard said to stop calling tools and answer; four times the model did not.
- **Cause**: a trip returned a sentence as the tool result and did nothing else.
- **Fix**: after three trips in one turn, a further tool request is not executed. The runtime makes one call without tools so the model can close, the same path as hitting the step limit (`LOOP_GUARD_MAX_HITS`, `test_a_model_that_ignores_the_guard_is_stopped_by_the_runtime`).
- **How far it is verified**: no later run tripped the guard more than once, so this path too is covered by unit tests only.

### 4. The loop guard blocked the `pytest` that should have run

- **Symptom**: the same run again. After its edit the model asked for `python -m pytest tests/test_ttl.py` and the guard refused it, twice.
- **Cause**: the guard counted by **tool name**. Six `python -c` probes in a row had filled the window with `run_shell`, so the next `run_shell` was refused whatever it contained. But the shell is a tool for running other programs; its name is too coarse a unit.
- **Fix**: shell commands are counted by program and first argument: `python -c`, `python -m pytest` and `git diff` each have their own count (`_guard_key`).
- **How far it is verified**: visible in round 2. On cachetools the guard tripped on the seventh identical probe, the model went to read the code and edit, and the `pytest` that followed ran (trace `32-case-cachetools-440-round2`).

### 5. On Windows only the first line of a multi-line command ran

Found while reading the round 2 traces, and the one with the widest effect.

- **Symptom**: the model's multi-line repro scripts kept returning "(no output)", so it tried variation after variation. Much of the repeated probing in the defects above started here.
- **Evidence**: the first two rounds contain 40 multi-line shell commands and **none ran completely**: 36 ran only their first line, and the loop guard stopped the other 4. With `python -c "` followed by a script on the next lines, Python receives an empty argument and prints nothing. In round 1 the model sent python-dotenv a four-line command (create a file, write to it, read it back, print the exit code) and believed all four had run. One had.
- **Cause**: `run_shell` hands the command to the platform shell as it is. `sh -c` runs every line. `cmd /c` runs the first and drops the rest without an error.
- **Fix**: on Windows a multi-line command is written to a temporary batch file and that is run, so every line executes and `%errorlevel%` is expanded line by line. A quoted string that spans lines cannot be expressed in cmd.exe at all; such a command is refused with the reason and a form that works (`agents/tools.py::_prepare_shell_command`, `_multiline_quote_problem`, `tests/test_shell_multiline.py`).
- **How far it is verified**: round 3 has six multi-line commands. Three were refused with the reason and three ran completely. On cachetools the model switched to the suggested form within two calls of the refusal, got a real traceback, and finished in 20 calls without a single guard trip. On tenacity one multi-line `echo` wrote a 33-line test file, and the whole run took 15 calls, the shortest of all three rounds.

## What is not fixed

### Probing in circles with slightly different output (humanize, failed all three rounds)

In every round the model ran 24 to 26 `python -c` probes of the same floating-point fact, edited nothing, and hit the 30-step limit. The loop guard never tripped: it decides that "nothing new is coming back" by comparing result hashes, and each probe printed something a little different.

Why it is not fixed: the fix is either "similar" instead of "identical", or a budget of read-only probes without an edit. Both are thresholds. Tuning a threshold until it catches humanize on these five issues, and then showing on the same five that it works, is fitting to the test set. It is written down and waits for an independent sample.

### The permission gate is not a sandbox

In `dontAsk` mode `write_file` may not create a new file. In rounds 2 and 3 the model, once refused, created the file with `echo … > file` in the shell instead; refused `del`, it deleted with `python -c "os.remove(…)"`. The gate judges the text of one call, and the model found another way to say it.

More rules will not close this: a shell has unlimited ways to say "write a file". The conclusion is that **the gate stops an accidental dangerous action and does not stop a model that insists; real containment needs a sandbox at the operating-system level** (a container, a restricted user). That is also why this experiment ran in throwaway clones. Recorded in [design trade-offs](design-tradeoffs.md).

### Installing from the network needs no confirmation

On python-dotenv in round 1 the model ran `pip install "python-dotenv[cli]"` and the gate let it through: its list of dangerous commands is about destruction (`rm`, `git push`, `kill` and so on), not about network installs. What it installed happened to be the released version of the project under test. Left alone, the grader would have tested the already-fixed code from PyPI instead of the model's change, so the runner now points the environment back at the checkout before grading.

### A wrong fix that the existing tests cannot catch

On cachetools in round 2 the model computed the expiry after the value had been written, so the half-inserted entry was still left behind. The existing tests passed, the model considered the work finished, and it never re-ran its own repro. The runtime cannot know whether a fix is right. A step such as "after an edit, re-run your own repro" might catch this; it does not exist yet.

### Not knowing when to stop

Six of the 15 runs hit the 30-step limit. Three of those had already made the right fix (cachetools and python-dotenv in round 1, more-itertools in round 3) and went on looking for something else to check. The resolved count was not affected; the tokens were wasted.

## The runner was corrected three times too

Errors in the measuring tool need saying as much as errors in the thing measured.

- **Hiding `.git`.** The clone contains the fix commit. The first version did nothing about that; before any model run it was changed to move `.git` out of the checkout while the agent works.
- **Reinstalling the checkout before grading.** See "Installing from the network" above.
- **The trace scrubber missed three spellings.** A directory named `pytest-of-<user>`, a path that pytest had abbreviated (`C:\\Users\\3...9\\repo`), and Python source with `as e:` before a line break, which looked like drive `e:`. Each time the leak check refused to save the trace first, and the rule was added afterwards (`agents/web/scrub.py`, `tests/test_scrub.py`).

## What this experiment does not show

- **A success rate.** Five issues, one run per round, one model, one Windows machine.
- **How much the fixes improved anything.** Rounds 2 and 3 re-ran the same issues, and the fixes were prompted by those very issues; they are not independent measurements. What can be said is that the corresponding failure modes did not occur in later rounds, and that each fix has a unit test that does not depend on a model.
- **Behaviour on harder problems.** These are fixes of a few lines in one file.

## Reproducing it

```bash
python bench/casestudy/run.py validate            # no model: check that the five cases are sound
python bench/casestudy/run.py run                 # run Keelson on every case and grade it
python bench/casestudy/run.py run tenacity-658    # or on one
python bench/casestudy/run.py grade               # grade the saved changes again, without the model
```

Clones and virtual environments go under the system temp directory (`--work` changes that). Results are written to `bench/casestudy/results.json` and scrubbed traces to `demo/traces/`. One round takes about ten minutes and 1.2 to 1.5 million input tokens.

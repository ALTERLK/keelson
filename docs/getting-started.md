# Getting started

**English** | [中文](zh/getting-started.md)

This page assumes you have never used an AI agent tool. Reading it takes about
ten minutes; following along, about twenty.

- [What it is](#what-it-is)
- [Step 1: run it (no API key needed)](#step-1-run-it-no-api-key-needed)
- [Step 2: watch a guided replay](#step-2-watch-a-guided-replay)
- [Step 3: connect a model](#step-3-connect-a-model)
- [Step 4: give it a first task](#step-4-give-it-a-first-task)
- [Step 5: meet the permission gate](#step-5-meet-the-permission-gate)
- [Step 6: teach it a preference](#step-6-teach-it-a-preference)
- [Troubleshooting](#troubleshooting)

## What it is

**A language model only produces text.** Ask it to fix a bug and it can write
down a fix, but it cannot open your files or run your tests.

For it to actually do things, a program has to stand between the model and your
computer. The model says "I want to read `pager.py`"; the program reads it and
hands the content back. The model says "run `pytest`"; the program runs it and
hands the output back. This repeats until the task is done. That program in the
middle is an **agent runtime** (or *harness*). Keelson is one.

Picture a very capable consultant locked in a glass room who can only pass
notes. Keelson is the assistant outside: it takes each note, decides whether
the request is acceptable, carries it out if so, and writes the result on a
note passed back in.

Because every note goes through it, the runtime can do a few things the model
cannot do for itself:

| What the model cannot do alone | What Keelson does | The concept |
|---|---|---|
| Judge whether an action is dangerous | Checks every request before it runs; stops and asks you before deleting files or pushing code | **Permission gate** |
| Notice it is going in circles | Stops a tool that keeps being called with no new result | **Loop guard** |
| Stay coherent in a very long task | Compresses old conversation into a structured summary | **Context folding** |
| Remember what you told it last time | Turns durable feedback into reusable rules, and checks a new rule is no worse than the old one | **Skills and self-evolution** |
| Account for what it did afterwards | Records every step as an event that can be replayed exactly | **Traces and replay** |

Those five things are the point of the project. The steps below let you see
each one.

![One turn, start to finish](assets/diagrams/turn.en.svg)

## Step 1: run it (no API key needed)

You need Python 3.11 or newer, and git.

```bash
git clone https://github.com/ALTERLK/keelson.git
cd keelson
```

Then run the start script. It creates a virtual environment, installs the
dependencies, starts the console and opens your browser:

```bash
start.bat        # Windows: double-clicking it works too
./start.sh       # macOS / Linux
```

The browser opens `http://localhost:8765`. No model is configured yet, so the
console is in **replay mode**: you can watch real recorded runs but not start
new ones. That is the right place to begin — understand it first.

> Prefer not to install anything? The hosted demo at
> <https://alterlk.github.io/keelson/> is the same console in replay mode.
>
> On Windows you can also skip Python entirely: the desktop app (`Keelson.exe`)
> walks you through the model, the project folder and a first task. See
> [The desktop app](features.md#the-desktop-app).

## Step 2: watch a guided replay

The first page is the Overview. Click **"Watch a guided replay"**.

You will see a real recorded run: the user says a test in `test_pager.py` is
failing, and the agent goes to work. The guided mode pauses at each important
step with an explanation; click "Continue" to move on. Things to notice:

1. **Each tool call is a card.** `read_file` shows source, `edit_file` shows a
   diff, `run_shell` shows terminal output.
2. **Each card says "gate: allowed" in its corner.** Nothing runs without being
   checked. Reading a file is safe, so it is allowed straight away.
3. **The inspector on the right counts as it goes.** Turns, tool calls, tokens,
   and how often the gate allowed, asked or refused.
4. **The inspector's *Timing* tab** draws the turn as a waterfall. Nearly all
   the time is spent waiting for the model; the runtime's own checks barely
   register.

Then open *Traces* in the side bar and play the second one: **"The gate stops a
destructive command"**. The user asks for a file to be deleted, the runtime
stops and shows a prompt, the user says no — and the model, having received
"denied" as the result, **does not look for another way to delete it**. It
reports that the file is still there. That is what the gate is for.

## Step 3: connect a model

Keelson does not ship a model; you supply an API key for one. Any
OpenAI-compatible or Anthropic-compatible provider works (OpenAI, Anthropic,
DeepSeek, gateways, …).

Stop the server (`Ctrl+C` in the terminal), then:

```bash
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

keelson init
```

`keelson init` asks which kind of provider, the base URL, the model name and
the key (hidden while you type), writes a `.env` file and checks that it works.

Whenever something seems off, run:

```bash
keelson doctor
```

It checks each requirement and says how to fix what is wrong:

```text
✓  Python            3.12.13
✓  API key           configured (51 characters)
✓  Endpoint          https://…/v1 · OpenAI-compatible · model …
✓  Model answers     … answered (ok)
✓  git               found on PATH
!  rg                not found — ripgrep makes grep_search much faster; a Python fallback is used without it
✓  Web console       fastapi and uvicorn are installed
✓  State directory   ~/.keelson
```

A ✗ must be fixed; a ! is a suggestion. `.env` is already in `.gitignore`.

## Step 4: give it a first task

**Keelson works in the current directory.** Wherever you start it is where it
reads and writes files. For a first run, use a directory you do not mind
messing up:

```bash
mkdir playground
cd playground
keelson web
```

The status at the bottom left changes from "Replay" to "Live · model name".
Open the Console page and start with something read-only:

> What is in this directory?

Then something that needs action:

> Write fizzbuzz.py and a test file for it, then run the tests.

It stops to ask before creating a new file; click "Allow once". When it runs the
tests, the command's output appears **line by line as it is produced**. If a
command runs too long, Stop really terminates it.

Prefer a terminal? Leave out `web`; the features are the same:

```bash
keelson                              # interactive
keelson "explain this project"       # one task, then exit
```

## Step 5: meet the permission gate

Below the input box is the permission mode: how much the agent may do without
asking.

| Mode | Read files | Edit existing files | Create files | Destructive commands | Good for |
|---|---|---|---|---|---|
| `default` | yes | yes | asks | asks | everyday use |
| `acceptEdits` | yes | yes | yes | asks | when you trust it with the code |
| `dontAsk` | yes | yes | refused | refused | unattended runs, CI |
| `plan` (CLI only) | yes | refused | refused | refused | seeing the plan before any change |
| `bypassPermissions` (CLI only, `--yolo`) | yes | yes | yes | yes | a throwaway sandbox |

The web page deliberately cannot select the last one: turning the gate off is
a command-line decision.

Try it. In `default` mode, type:

> Delete fizzbuzz.py with a shell command.

When the prompt appears, click **Deny** and watch what the model does: it is
told the user refused, and it stops and says so rather than trying a different
command.

## Step 6: teach it a preference

Ask it to explain some code, then give it an instruction that is meant to last:

> From now on, when you explain code, always use a bullet list, one line each.

Ask it to explain another file. This time it uses bullets on its own — your
sentence was extracted into a **skill**, saved under `.keelson/skills/`, and
retrieved for the next matching task.

Skills cannot be rewritten freely. If later feedback leads to an update, the
new version must pass a *non-inferiority gate*: the skill's own past cases are
replayed against both versions, and the new one must not do worse, or it is
quarantined and never goes live. The Skill evolution page shows one real update
that was admitted and one that was stopped.

![How a skill changes](assets/diagrams/evolution.en.svg)

## Troubleshooting

**The page says "Replay" and the input box is disabled.**
No model is configured, or the server was started with `--replay`. `keelson
doctor` tells you which; so does the "This install" card on the Overview page.

**`keelson`: command not found.**
The virtual environment is not active. Run `.venv\Scripts\activate` (Windows)
or `source .venv/bin/activate`.

**`keelson doctor` shows ✗ for "Model answers".**
A key is configured but the request fails. Read the error on that line: `401`
means the key is wrong; `404` usually means the model name or base URL is
wrong. One rule to know: a base URL whose path contains `/anthropic` is spoken
to with the Anthropic protocol, anything else with the OpenAI protocol.

**Will it change files I care about?**
It only works in the directory you start it in, and in the default mode new
files and destructive commands need your approval. To be stricter, use
`keelson --plan`: read-only, it produces a plan and changes nothing.

**Is it expensive?**
That depends entirely on the model you choose. The inspector shows token usage
and an estimated cost as it goes; `--max-cost 0.5` sets a ceiling.

**The answer arrives all at once instead of word by word.**
Your `.env` sets `KEELSON_OPENAI_STREAM=0` (useful when a gateway's streaming
endpoint is unreliable). Remove that line to stream again.

**Where next?**

- [Feature guide](features.md): what each feature is, when to use it, and how.
- [Architecture](architecture.md): how the code is organised.
- [Web console](web-console.md): how events, sessions and replay are built.
- [Design trade-offs](design-tradeoffs.md): where compromises were made, and why.

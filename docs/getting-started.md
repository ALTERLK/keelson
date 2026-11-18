# Getting started

**English** | [中文](zh/getting-started.md)

This page assumes you have never used an AI agent tool. Reading it takes about
ten minutes; following along, about twenty.

On Windows and just want to double-click? Read the [desktop app guide](desktop.md),
with a screenshot for every step. This page covers installing from source and
using the terminal and the browser console; the ideas are the same in both.

- [What it is](#what-it-is)
- [Step 1: run it (no API key needed)](#step-1-run-it-no-api-key-needed)
- [Step 2: watch a guided replay](#step-2-watch-a-guided-replay)
- [Step 3: connect a model](#step-3-connect-a-model)
- [Step 4: give it a first task](#step-4-give-it-a-first-task)
- [Step 5: meet the permission gate](#step-5-meet-the-permission-gate)
- [Step 6: undo](#step-6-undo)
- [Step 7: make it remember what you said](#step-7-make-it-remember-what-you-said)
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

Because every note goes through it, the runtime can do things the model cannot
do for itself, or cannot be trusted to:

| | What the model gets wrong alone | What Keelson does |
|---|---|---|
| **Learns your ways** | Forgets the preferences you gave it last time | Turns durable feedback into skills; a new version goes live only if it is no worse than the old one |
| **Checks its own work** | Says "done, all tests pass" when they never ran or failed | Runs the checks itself at the end of the turn and shows a Verified / Checks fail / Not verified badge from the real result; a "pass" with no evidence is flagged |
| **Stays out of trouble** | Can't judge whether an action is dangerous | No real deletion: deletes go to the Recycle Bin; the home folder and drive roots are off limits; anything undo can't reverse asks you first and says why |
| **Keeps your rules** | Loses your instructions when a long conversation is compressed | Keeps your rules word for word in a task ledger and puts them back after every fold |
| **Takes it all back** | Can't roll back a change that went wrong | Snapshots before every turn; `/undo` restores everything, shell changes included |

The steps below let you see each one.

![One turn, start to finish](assets/diagrams/turn.en.svg)

## Step 1: run it (no API key needed)

> **On Windows and don't want Python?** Download the zip from
> [Releases](https://github.com/ALTERLK/keelson/releases/latest), unzip it and
> double-click `Keelson.exe`; the [desktop app guide](desktop.md) walks you
> through the three setup steps. The zip also has the command-line
> `keelson-cli.exe`. Then skip to [Step 2](#step-2-watch-a-guided-replay).
>
> Don't want to install anything? The hosted demo at
> <https://alterlk.github.io/keelson/> is the same console in replay mode.

From source you need Python 3.11 or newer, and git.

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
new ones. That is the right place to begin: understand it first.

## Step 2: watch a guided replay

The first page is the Overview. Click **"Watch a guided replay"**.

You will see a real recorded run: the user says a test in `test_pager.py` is
failing, and the agent goes to work. The guided mode pauses at each important
step with an explanation; click "Continue" to move on. Things to notice:

1. **Each tool call is a card.** `read_file` shows source, `edit_file` shows a
   diff, `run_shell` shows terminal output.
2. **Each card says "gate: allowed".** Nothing runs without being checked.
3. **There is a "Verified" badge under the answer.** The model didn't put it
   there: Keelson checked that the tests really ran, and passed, after the last
   change.
4. **The inspector on the right counts as it goes**: turns, tool calls, tokens,
   and how often the gate allowed, asked or refused. Its *Timing* tab draws the
   turn as a waterfall; nearly all the time is spent waiting for the model.

Then open *Traces* in the side bar and play the others:

- **"Deletes go to the Recycle Bin"**: the user asks for a file to be deleted
  with the shell. Shell deletes are refused, so the model uses `delete_files`
  and the file goes to the Recycle Bin.
- **"/undo covers shell changes"**: the turn changes and creates files only
  with shell commands. One undo puts everything back.
- **"Only what undo cannot reverse asks"**: the model wants to install a
  package; the runtime stops and asks, saying that /undo can't reverse an
  install. The user denies it, and the model finds a way that needs no package.

## Step 3: connect a model

Keelson does not ship a model; you supply an API key for one. Any
OpenAI-compatible or Anthropic-compatible provider works (DeepSeek, OpenAI,
Anthropic, gateways, …), and DeepSeek has a preset.

Stop the server (`Ctrl+C` in the terminal), then:

```bash
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

keelson init
```

`keelson init` asks which kind of provider, the base URL, the model name and
the key (hidden while you type), writes them to `~/.keelson/config.env` and
checks that it works. That file is in your user folder, not in any project, so
it is never committed, and every project shares it. For one project only, use
the project's `.keelson/config.env`.

Switch between models often? `keelson init --profile <name>` saves a profile,
and `keelson use <name>` switches to it.

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

A ✗ must be fixed; a ! is a suggestion. The key itself is never printed, only
its length.

## Step 4: give it a first task

**Keelson works in the current directory.** Wherever you start it is where it
reads and writes files. For a first run, use a directory you do not mind
messing up:

```bash
mkdir playground
cd playground
git init
keelson web
```

`git init` is optional, but with git available, undo also covers changes made
by shell commands. (Keelson keeps its own snapshot repository; it never touches
your `.git`.)

The status at the bottom left changes from "Replay" to "Live · model name".
Open the Console page and start with something read-only:

> What is in this directory?

Then something that needs action:

> Write fizzbuzz.py and a test file for it, then run the tests.

This time it doesn't stop to ask: creating and editing files and running tests
all happen inside the project and can all be undone. When it runs the tests,
the output appears **line by line as it is produced**; if a command runs too
long, Stop really terminates it.

When it finishes, a verify badge appears under the answer. If it finds pytest, it
checks that the tests ran and passed after the model's last change, and runs
them itself if not: ✓ Verified, ✗ Checks fail (with the real output), or ! Not verified
(no check command in the project). The checks are detected automatically; to
pin them, run `/verify init` in the terminal, which writes them to a
`## Verify` section of `KEELSON.md` in the project root. Edit that file to
change them.

Prefer a terminal? Leave out `web`; the features are the same:

```bash
keelson                              # interactive
keelson "explain this project"       # one task, then exit
```

In the terminal, `/help` lists every command, Shift+Tab switches the permission
mode, and Ctrl+C interrupts the current turn.

## Step 5: meet the permission gate

Keelson decides whether to ask by **whether the action can be undone**:

| Action | Result |
|---|---|
| Changes, new files and ordinary commands inside the project | goes ahead (snapshotted, so undoable) |
| Deleting project files with `delete_files` | goes ahead (to the Recycle Bin) |
| Installing packages, network access, `git push`, rewriting history, writing outside the project, MCP tools | asks, and says why |
| Force-push, `sudo`, `curl … \| sh`, unset variables in a path | asks, and can only be allowed once |
| Deleting from the shell (`rm`, `del`, `Remove-Item`, …) | refused; the model is told to use `delete_files` |
| Touching the home folder, a drive root, the project itself or its parents; writing system folders | refused in every mode |

The permission mode below the input box adjusts this:

| Mode | What it does | Good for |
|---|---|---|
| Default | as in the table | everyday use |
| Plan (read-only) | reads and writes a plan; changes nothing | seeing the plan before any change |
| Don't ask | refuses anything that would ask | unattended runs, CI |
| `bypassPermissions` (CLI only, `--yolo`) | asks nothing, but the last row above is still refused | a throwaway sandbox |

The web console and the desktop app deliberately cannot select the last one:
turning the questions off is a command-line decision.

Try two things. First:

> Delete fizzbuzz.py with a shell command.

The shell delete is refused, the model uses `delete_files` instead, and the
file goes to the Recycle Bin. You can fetch it from there, and the next step's
undo puts it back too.

Then:

> Install the requests package.

This time a dialog appears, saying /undo can't reverse an install. Your
choices are Allow once, Allow for this session, Always allow in this project
and Deny. Click **Deny** and optionally give a reason: the model sees it, and
stops and tells you or finds a way that needs no package.

Details and limits: [Safety and undo](safety.md).

## Step 6: undo

Open the **Review** tab in the inspector on the right. It shows the files each
turn changed, with diffs. **Put back** restores one file, **Undo this turn**
restores the whole turn, and **Redo** takes the undo back.

In the terminal:

| Command | What it does |
|---|---|
| `/undo` | restore the files the last turn changed, including those changed, created or deleted by the shell |
| `/redo` | take back the last `/undo` |
| `/rewind N` | undo the last N turns |
| `/revert <file>` | restore one file |
| `/checkpoints` | list the turns that can be undone |

Try it: ask it to "use shell commands to create a build folder with a
report.txt in it, and append a comment line to fizzbuzz.py", then undo the turn.
The build folder is gone and fizzbuzz.py is as it was.

If you changed a file yourself after that turn, undo stops and lists it rather
than overwrite your edit. What can't be undone: installed packages, commits and
pushes, databases, anything sent over the network. Those are exactly the
actions that ask first in Step 5.

## Step 7: make it remember what you said

**Rules for this task.** Say in the conversation:

> Don't change any file starting with test_.

Open the inspector's **Task** tab: the sentence is kept word for word under
"Your rules". When the conversation grows long and older parts are compressed,
these rules are put back exactly as you said them. You can add one there
directly as well.

**Lasting preferences.** Ask it to explain some code, then give it an
instruction that is meant to last:

> From now on, when you explain code, always use a bullet list, one line each.

Ask it to explain another file. This time it uses bullets on its own: your
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
or `source .venv/bin/activate`. With the release zip, the command is
`keelson-cli.exe`.

**`keelson doctor` shows ✗ for "Model answers".**
A key is configured but the request fails. Read the error on that line: `401`
means the key is wrong; `404` usually means the model name or base URL is
wrong. One rule to know: a base URL whose path contains `/anthropic` is spoken
to with the Anthropic protocol, anything else with the OpenAI protocol.

**Does my old `.env` still work?**
Yes. A `.env` in the project folder is still read, with the lowest priority.
`keelson init` writing to `~/.keelson/config.env` is the better place: every
project shares it.

**The badge always says "Not verified".**
Keelson found no check command. Add a `## Verify` section to `KEELSON.md` in
the project root with the commands as a list (for example
`` `python -m pytest -q` ``); `/verify` in the terminal shows which commands are
in use.

**Will it change files I care about?**
It only works in the directory you start it in, and its changes can be undone;
anything undo can't reverse asks first; deletions go to the Recycle Bin. It
declares which files it means to change before it starts, and changing others
asks you. To be stricter, use `keelson --plan`: read-only, it produces a plan.

**Is it expensive?**
That depends entirely on the model you choose. The inspector shows tokens,
estimated cost and cache hit rate as it goes; `/usage` sums them up;
`--max-cost 0.5` sets a ceiling.

**The answer arrives all at once instead of word by word.**
Your configuration sets `KEELSON_OPENAI_STREAM=0` (useful when a gateway's
streaming endpoint is unreliable). Remove that line to stream again.

**Where next?**

- [Desktop app guide](desktop.md): the Windows desktop window, step by step with screenshots.
- [Feature guide](features.md): what each feature is, when to use it, and how.
- [Safety and undo](safety.md): what is stopped, how, and what isn't covered.
- [Architecture](architecture.md): how the code is organised.
- [Web console](web-console.md): how events, sessions and replay are built.
- [Design trade-offs](design-tradeoffs.md): where compromises were made, and why.

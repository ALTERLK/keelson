# Security

Keelson runs shell commands and edits files on behalf of a language model. This
page says what it does to keep that safe, what it deliberately does not try to
do, and how to report a problem. The full table of what is stopped and how is
in [Safety and undo](docs/safety.md).

## What the runtime enforces

- **Every tool call is checked before it runs** (`keelson/tools.py::check_permission`,
  `keelson/safety.py`). The question is whether the action can be undone:
  - changes inside the project are snapshotted first and go ahead;
  - what `/undo` cannot reverse (installing packages, network access,
    `git push`, rewriting history, writing outside the project, MCP and
    unknown tools) asks you first and says why;
  - force-pushes, `sudo`, `curl … | sh` and paths with unset variables can
    only be allowed once, never always.
- **Some things are refused in every mode, `--yolo` included**: moving or
  deleting the home folder, a drive root, the project or a folder above it;
  writing system folders or Keelson's checkpoint store; wiping disks
  (`mkfs`, `Format-Volume`, `diskpart`, `dd of=/dev/…`).
- **There is no real deletion.** Deleting from the shell (`rm`, `del`,
  `Remove-Item`, `find -delete`, also inside `bash -c`, `cmd /c` and the like)
  is refused; `delete_files` moves things to the Recycle Bin and deletes
  nothing if that fails.
- **Paths are expanded before they are judged** (`~`, `$HOME`,
  `$env:USERPROFILE`, `%VAR%`), and compound commands are checked segment by
  segment.
- **Every turn is snapshotted** into a separate git store under
  `~/.keelson/projects/`, so `/undo` restores shell changes too. The project's
  own `.git` is never read or written.
- **A cloned repository's allow rules and MCP servers stay off** until you
  trust that folder (`keelson trust`). Its deny rules apply at once.
- **A sub-agent never has more permission than its parent.**
- **The web console listens on loopback only** and refuses any other `--host`
  (the container image is the one exception, and it publishes the port to
  loopback).
- **State-changing requests must be same-origin JSON**, and requests with a
  non-loopback `Host` header are refused. Together these stop a web page you
  happen to visit from driving the agent through `localhost` (CSRF, DNS
  rebinding).
- **The web console and the desktop app cannot turn the questions off.**
  `bypassPermissions` can only be chosen on the command line.
- **Model output never reaches `innerHTML` unescaped** in the console.
- **Published traces are scrubbed** of local paths, and one that still contains
  a user name, an absolute path or a key-shaped string is not saved.

## What it is not

- **Not a sandbox.** A command runs with your user's privileges. Keelson judges
  the command line it can see; it cannot see what a test, build script or
  `python script.py` does once it runs. Changes such a program makes inside the
  project can still be undone; changes outside it cannot. For untrusted tasks,
  run Keelson in a container or a virtual machine (`docker compose up` is
  provided).
- **Command parsing is heuristic**, not a proof. Unusual quoting or generated
  commands (`Invoke-Expression`, `eval`) can hide intent; such commands are
  asked about as "cannot be checked". [bench/FEATURES.md](bench/FEATURES.md)
  lists the two destructive commands in its set that are asked about rather
  than refused, and so would run under `--yolo`.
- **`--yolo` (`bypassPermissions`) turns every question off.** Only the
  refusals above remain. Use it only in a throwaway environment.
- **Undo has limits**: ignored files, `node_modules`-style folders, new files
  over 50 MB, installed packages, commits and pushes, databases and anything
  sent over the network are not covered.
- **Prompt injection is not solved.** Text in a file or a tool result can try
  to steer the model. The gate limits what a steered model can do without your
  approval; it does not stop the attempt.

## Your API key

The key is kept in `~/.keelson/config.env` (written by `keelson init` or the
desktop app's setup), in a profile under `~/.keelson/profiles/`, or in the
environment, and is sent only to the provider you configured. These files are
in your user folder, not in the project. A legacy `.env` in the project is
still read; it is git-ignored. `keelson doctor` prints the key's length, never
the key. The desktop app reads the key from the file when it needs it and does
not keep it in the window process's environment.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
reporting instead:
<https://github.com/ALTERLK/keelson/security/advisories/new>

Include what you did, what happened, and the version (`keelson doctor` shows
it).

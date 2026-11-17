# Safety and undo

**English** | [中文](zh/safety.md)

Two promises, and exactly how far they go.

1. **Keelson does not cause catastrophic damage.** Nothing is deleted for good,
   and the home folder, drive roots and system folders are off limits.
2. **Everything it does inside the project can be undone**, including what its
   shell commands did.

## What is stopped, and how

| Risk | What Keelson does | Code |
|---|---|---|
| `rm -rf ~`, `Remove-Item $env:USERPROFILE -Recurse`, `del /s` … | Every way of deleting from the shell is refused (also inside `bash -c`, `cmd /c`, `ForEach-Object { … }`, `xargs`, `find -delete`). The model is told to use `delete_files`. | `keelson/safety.py` |
| A variable that was never set: `rm -rf $DIR/` → `rm -rf /` | Variables are expanded the way the shell would expand them before a path is judged. One that cannot be expanded makes the command dangerous: it is asked about and can only be allowed once. | `safety.expand` |
| Moving or deleting the home folder, a drive root, the project itself or a folder above it | Refused in every mode, `--yolo` included. | `safety.describe_protected` |
| Writing into system folders (`C:\Windows`, `Program Files`, `/usr`, `/etc`, …) or Keelson's checkpoint store | Refused in every mode. | same |
| Writing outside the project | Asked about, with the path. Can only be allowed once. | `safety.assess_path` |
| Things `/undo` cannot reverse: package installs, network access, `git push`, history rewrites | Asked about, with the reason. Can be allowed for the session or always for the project. | `safety.assess_shell` |
| Force-push, `git clean -x`, publishing, `sudo`, `curl … \| sh`, inline code that deletes files | Asked about, and never "always allowed". | same |
| A cloned repository that ships `run_shell(*)` or an MCP server | Its allow rules and MCP servers stay off until you trust the folder. | `keelson/trust.py` |
| Deleting through Keelson | `delete_files` moves to the Recycle Bin (Trash). If that fails, nothing is deleted. | `keelson/platforms/trash.py` |

## Undo

At the start of every turn, Keelson snapshots the project into its own git
repository under `~/.keelson/projects/<hash>/checkpoints/`. `/undo` restores
the files the last turn changed, whichever tool changed them; `/redo` reverses
an undo; `/rewind N` and `/revert FILE` go further back or narrower. If you
edited a file after the turn, `/undo` stops and lists it instead of
overwriting your work. See [Undo: checkpoints](features.md#undo-checkpoints).

The project's own `.git` is never read or written (separate `GIT_DIR`, index
and config; no hooks, no filters, no line-ending conversion).

## What is *not* covered

Said plainly, so nobody relies on more than there is:

- **This is not a sandbox.** A command Keelson runs (a test suite, a build
  script, `python script.py`) runs with your account's rights. Keelson judges
  the command line it can see; it cannot see what a program does once it
  runs. A script that deletes files is not stopped. Its changes *inside the
  project* are still undone by `/undo`.
- **Not undoable:** installed packages, commits and pushes, databases, anything
  sent over the network, and files outside the project changed by a shell
  command. These are the actions the gate asks about.
- **Not in the snapshot:** files ignored by `.gitignore` (unless Keelson's own
  file tools changed them), `node_modules`, `.venv` and similar folders, and new
  files over 50 MB. Deleting them with `delete_files` still goes to the
  Recycle Bin.
- **Without git**, only Keelson's own file tools can be undone. The terminal
  says so, and ordinary shell commands then ask before running.
- If the project folder is your home folder or a drive root, it is too big to
  snapshot; open a project folder instead.
- Command parsing is heuristic. Unusual quoting or generated command strings
  (`Invoke-Expression`, `eval`) can hide what a command does; those are asked
  about as "cannot check".

## Turning things off

- `KEELSON_CHECKPOINTS=0` turns snapshots off.
- `--yolo` (`bypassPermissions`) turns off every question, but not the refusals
  in the first table.
- `keelson trust --revoke` withdraws trust from a folder.

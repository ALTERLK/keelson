# Security

Keelson runs shell commands and edits files on behalf of a language model. This
page says what it does to keep that safe, what it deliberately does not try to
do, and how to report a problem.

## What the runtime enforces

- **Every tool call is checked before it runs** (`keelson/tools.py::check_permission`).
  Destructive shell commands and new files need approval in the default mode;
  `settings.json` rules can allow or deny specific calls; tools the gate does
  not recognise (MCP tools included) are untrusted by default.
- **A sub-agent never has more permission than its parent.**
- **The web console listens on loopback only** and refuses any other `--host`
  (the container image is the one exception, and it publishes the port to
  loopback).
- **State-changing requests must be same-origin JSON**, and requests with a
  non-loopback `Host` header are refused. Together these stop a web page you
  happen to visit from driving the agent through `localhost` (CSRF, DNS
  rebinding).
- **The web console cannot turn the gate off.** `bypassPermissions` can only be
  chosen on the command line.
- **Model output never reaches `innerHTML` unescaped** in the console.
- **Published traces are scrubbed** of local paths, and one that still contains
  a user name, an absolute path or a key-shaped string is not saved.

## What it is not

- **Not a sandbox.** An approved command runs with your user's privileges, in
  your working directory. The permission gate decides *whether* something runs,
  not what it can reach once it does. For untrusted tasks, run Keelson in a
  container or a virtual machine (`docker compose up` is provided).
- **The destructive-command list is a pattern list**, not a proof. A
  sufficiently creative command can do damage without matching it. `dontAsk`
  and `plan` modes are the stricter choices for unattended use.
- **`--yolo` (`bypassPermissions`) disables the gate entirely.** Use it only in
  a throwaway environment.
- **Prompt injection is not solved.** Text in a file or a tool result can try
  to steer the model. The gate limits what a steered model can do without your
  approval; it does not stop the attempt.

## Your API key

The key is read from `.env` or the environment and sent only to the provider
you configured. `.env` is git-ignored. `keelson doctor` prints the key's length,
never the key.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
reporting instead:
<https://github.com/ALTERLK/keelson/security/advisories/new>

Include what you did, what happened, and the version (`keelson doctor` shows
it).

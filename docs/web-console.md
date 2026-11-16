# Web console

**English** | [中文](zh/web-console.md)

`keelson web` opens a browser console for the same runtime the CLI drives. It
adds no agent behaviour; it is a second way of *watching* a run and answering
its permission prompts.

```bash
pip install -e ".[web]"
keelson web              # live, if an API key is configured
keelson web --replay     # recorded traces only; no model is called
```

New to the project? Start with [Getting started](getting-started.md); this page
is about how the console is built.

## The idea: one event stream, several consumers

![One event stream, three consumers](assets/diagrams/events.en.svg)

Before this, the loop called `print_*` functions that drew straight to the
terminal, so "what happened" and "how it is shown" were the same code. Now the
`print_*` functions in `ui.py` only publish events, and the terminal renderer
is one subscriber among others.

Properties of the bus (`agents/events.py`):

- **Synchronous, ordered dispatch.** A subscriber sees exactly the sequence
  the loop produced. Streaming text to a terminal depends on that.
- **Subscriber isolation.** An exception in one subscriber is logged and
  swallowed. A closed browser tab must not be able to stop the agent.
- **Bounded history.** The last 5,000 events are kept with a sequence number,
  so a consumer that connects late or reconnects can catch up.
- **The current bus is a context variable.** `events.emit()` publishes to
  whichever bus is bound in the current `contextvars` context, and falls back
  to the process-wide one. That single indirection is what makes sessions work.

## Sessions

One server process holds several independent conversations
(`agents/web/sessions.py`). Each `Session` owns an `EventBus`, an `Agent`, a
trace recorder and its pending permission prompts.

The runtime knows nothing about sessions. When a turn starts, the session
copies the current context, binds its own bus in the copy, and creates the
turn's task in that context:

```python
context = contextvars.copy_context()
context.run(events.bind_bus, self.bus)
loop.create_task(self._run_turn(message), context=context)
```

Tasks created inside the turn — concurrent tool calls, the background skill
extraction — copy the context again, so the binding follows the whole turn
without being passed through any function signature. The same mechanism tags
live command output with the tool call it belongs to (`events.tool_call(id)`).

What is isolated: the conversation, the event stream, the permission mode, the
permission prompts and the recording. What is **not**: sessions share the
server's working directory, so they are several conversations about one
workspace, not several sandboxes.

At most 8 sessions are live at once. When a ninth is asked for, the idle
session used least recently is retired; if all eight are busy the request is
refused with 409.

**History and resume.** A retired or closed session leaves two files: its trace
(`.keelson/traces/<time>-<id>.jsonl`, every event) and its saved message list.
The side bar lists them. Opening one replays the trace, read-only. "Continue
this session" creates a new live session, replays the old events onto its bus
so the conversation is visible, and restores the message list into the agent so
the model remembers it. This survives a server restart.

## Server → browser: Server-Sent Events

`GET /api/sessions/{id}/events` is one long-lived response; each event is a
frame:

```text
id: 42
data: {"seq": 42, "ts": 1731640000.1, "type": "tool_call", "data": {...}}
```

Why SSE and not a WebSocket: the traffic is one-directional (the browser's own
actions are rare and fit plain POSTs), SSE is ordinary HTTP with nothing to
upgrade, and `EventSource` reconnects by itself and sends `Last-Event-ID`, so
the server knows exactly where to resume.

Resuming correctly needs one detail: the handler **subscribes first, then reads
the backlog**. An event published in between appears in both; the duplicate is
dropped by comparing sequence numbers. Reading the backlog first would leave a
window in which an event is lost.

## Browser → server

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | Is a model configured, version, switchable modes |
| `GET /api/sessions` · `POST /api/sessions` | List live and saved sessions · create one (`{"resume": id}` to continue a saved one) |
| `DELETE /api/sessions/{id}` | Close a session |
| `POST /api/sessions/{id}/chat` | Start a turn (409 if one is running) |
| `POST /api/sessions/{id}/confirm` | Answer a permission prompt |
| `POST /api/sessions/{id}/abort` | Stop the current turn |
| `POST /api/sessions/{id}/mode` | Switch between `default`, `acceptEdits`, `dontAsk` |
| `GET /api/bench`, `/api/evolution`, `/api/skills`, `/api/casestudy`, `/api/perf`, `/api/doctor`, `/api/traces` | Read-only views |

**Permission prompts.** The runtime already had a hook for this: the loop
awaits a `confirm_fn(message) -> bool`. The CLI's version reads from stdin. The
web version publishes a `confirm_request` event and awaits an
`asyncio.Future`; `POST …/confirm` resolves it. The loop is parked on that
future in the meantime, which is exactly the behaviour wanted — nothing runs
until the user decides.

## Long commands: live output, and a Stop that stops

`run_shell` runs through `asyncio.create_subprocess_shell`
(`tools.py::run_shell_async`):

- Output is read as it is produced, decoded incrementally, and published as
  `tool_output` events in small batches (about 12 per second at most, capped at
  40,000 characters per command), so the browser shows a command working
  instead of a spinner.
- The event loop is never blocked: other sessions, the event streams and the
  Stop button stay responsive. The remaining synchronous tools (file reads,
  search) run in a worker thread for the same reason.
- On timeout or cancellation the **whole process tree** is killed —
  `taskkill /T` on Windows, the process group on POSIX. Killing only the shell
  would leave `pytest` or a dev server running behind it.
- The string returned to the model is unchanged
  (`Command failed (exit code N)…`), so model behaviour is the same as before.

`scripts/smoke_live.py` checks this against a real model: a 60-second command
is stopped about three seconds in, and the child process is gone.

## Where the time goes

Each step of the loop is wrapped in `events.span(name)`, which publishes a
`span` event with its duration: `model_stream`, `tool`, `permission`,
`skill_retrieval`, `fold`, and `confirm_wait` (time spent waiting for a person,
reported separately so it is never mistaken for latency).

The inspector's *Timing* tab draws them as a waterfall per turn. Over the three
recorded demo runs (`python scripts/perf_report.py`):

| step | count | p50 | p95 | share of time |
|---|---:|---:|---:|---:|
| model call | 20 | 2.15 s | 6.34 s | 94.43% |
| tool execution | 14 | 1.51 ms | 1.10 s | 5.56% |
| permission check | 15 | 0.02 ms | 0.28 ms | < 0.01% |
| skill retrieval | 5 | 0.85 ms | 0.90 ms | 0.01% |

The runtime's own checks account for under 0.01% of a run. The sample is small
(three short runs, one model, one machine); it shows the order of magnitude,
not a benchmark.

## Replay

A trace is the event stream written to JSON lines (`agents/web/recorder.py`).
Replay happens **in the browser**: the player reads a trace and feeds its
events, re-timed, into the same reducer that consumes the live SSE stream
(`static/js/store.js`). Console state is a fold over events and nothing else,
so a replay goes through the same rendering code as the run it came from.

Two things fall out of that:

- The hosted demo needs no server. `scripts/build_pages.py` exports the static
  files plus the JSON the API would have returned.
- A demo cannot fail for lack of network or API key.

**Guided replay** pauses at the events worth explaining — the first gate
decision, a permission prompt, a refusal, a loop-guard hit — highlights the
element on the page and shows a caption. The captions are rules keyed on event
type (`static/js/guide.js`), not a script written for one recording, so the
tour works on any trace, including your own sessions.

`scripts/record_demo.py` records the shipped traces by running real tasks in a
throwaway workspace. Local paths are rewritten before saving
(`agents/web/scrub.py`), and a trace that still contains a user name, an
absolute Windows path or something shaped like an API key is not saved.

## Front end

Plain HTML, CSS and ES modules under `agents/web/static/` — no framework, no
build step, no CDN, so it works offline and ships inside the Python package
(fonts included: Inter and JetBrains Mono, both under the SIL Open Font License).

- `store.js` — `apply(state, event)`, the only place state changes.
- `stream.js` — `LiveStream` (SSE) and `ReplayPlayer`, both emitting the same event shape.
- `session.js` — which session or recording is on screen; the actions the views call.
- `views/console.js` — keyed rendering: each conversation item keeps its DOM
  node and is rebuilt only when its revision changes, batched once per frame,
  so streaming a long answer re-renders one message, not the conversation.
- `toolview.js` — one renderer per tool: a terminal for `run_shell`, a diff
  for edits, highlighted source for `read_file`.
- `highlight.js` — a small syntax highlighter: one regular expression per
  language, first alternative wins, text escaped as it is emitted.
- `markdown.js` — escapes first, then adds back only its own tags; model
  output never reaches `innerHTML` unescaped.
- `charts.js` — SVG/CSS charts with a table twin for every chart.
- `palette.js` — the Ctrl+K command palette.

## Security

The console can make the agent run shell commands, and it has no login. So:

- It binds to **loopback only** and refuses any other `--host`.
- State-changing requests must be **same-origin JSON**. A cross-site form
  cannot send JSON without a CORS preflight, and requests carrying a foreign
  `Origin` are refused — this is the CSRF defence.
- Requests whose `Host` header is not a loopback name are refused, which
  closes DNS rebinding.
- The console **cannot select `bypassPermissions`**. Turning the gate off stays
  a command-line decision.

## Known limits

- **Sessions share one working directory.** They isolate conversations, not
  file systems. Two sessions editing the same file will step on each other.
- **Session state lives in one process.** There is no shared store, so the
  console does not scale beyond one server process — which matches what it is:
  a local tool bound to loopback.
- **Plan-mode approval** is not wired into the web console yet; use the CLI.
- With non-streaming gateways (`KEELSON_OPENAI_STREAM=0`) an answer arrives as
  one event rather than token by token. Tool output still streams.

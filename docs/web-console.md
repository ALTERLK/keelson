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

## The idea: one event stream, several consumers

```text
Agent loop ──emit──▶ EventBus ──┬─▶ terminal renderer   agents/ui.py
            agents/events.py    ├─▶ SSE broadcaster      agents/web/server.py ──▶ browser
                                └─▶ trace recorder       agents/web/recorder.py ──▶ *.jsonl
```

Before this, the loop called `print_*` functions that drew straight to the
terminal, so "what happened" and "how it is shown" were the same code. Now the
`print_*` functions in `ui.py` only publish events, and the terminal renderer
is one subscriber among others. The loop itself changed in only a handful of
places: it now also reports the gate's verdict, loop-guard hits, context folds
and token usage, which the terminal used to infer or not show at all.

Properties of the bus (`agents/events.py`, ~130 lines):

- **Synchronous, ordered dispatch.** A subscriber sees exactly the sequence
  the loop produced. Streaming text to a terminal depends on that.
- **Subscriber isolation.** An exception in one subscriber is logged and
  swallowed. A closed browser tab must not be able to stop the agent.
- **Bounded history.** The last 5,000 events are kept with a sequence number,
  so a consumer that connects late or reconnects can catch up.

## Server → browser: Server-Sent Events

`GET /api/events` is one long-lived response; each event is a frame:

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
| `POST /api/chat` | Start a turn (409 if one is running) |
| `POST /api/confirm` | Answer a permission prompt |
| `POST /api/abort` | Stop the current turn |
| `POST /api/reset` | New session |
| `POST /api/mode` | Switch between `default`, `acceptEdits`, `dontAsk` |
| `GET /api/state`, `/api/bench`, `/api/evolution`, `/api/skills`, `/api/traces` | Read-only views |

**Permission prompts.** The runtime already had a hook for this: the loop
awaits a `confirm_fn(message) -> bool`. The CLI's version reads from stdin. The
web version publishes a `confirm_request` event and awaits an
`asyncio.Future`; `POST /api/confirm` resolves it. The loop is parked on that
future in the meantime, which is exactly the behaviour wanted — nothing runs
until the user decides.

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

`scripts/record_demo.py` records the shipped traces by running real tasks in a
throwaway workspace, and rewrites local paths before saving.

## Front end

Plain HTML, CSS and ES modules under `agents/web/static/` — no framework, no
build step, no CDN, so it works offline and ships inside the Python package.

- `store.js` — `apply(state, event)`, the only place state changes.
- `stream.js` — `LiveStream` (SSE) and `ReplayPlayer`, both emitting the same event shape.
- `views/console.js` — keyed rendering: each conversation item keeps its DOM
  node and is rebuilt only when its revision changes, batched once per frame,
  so streaming a long answer re-renders one message, not the conversation.
- `markdown.js` — escapes first, then adds back only its own tags; model
  output never reaches `innerHTML` unescaped.
- `charts.js` — SVG/CSS charts with a table twin for every chart.

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

- **One session per process.** The bus is process-global. Hosting several
  sessions would need a bus per session (for example via `contextvars`).
- **Blocking tools stall the stream.** `run_shell` uses a blocking subprocess
  call, so no events flow while a long command runs. Moving it to a thread or
  an async subprocess would fix that.
- **Plan-mode approval** is not wired into the web console yet; use the CLI.
- With non-streaming gateways (`KEELSON_OPENAI_STREAM=0`) an answer arrives as
  one event rather than token by token.

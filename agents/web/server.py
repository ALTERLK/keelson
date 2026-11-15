"""HTTP layer for the web console.

Server → browser is one long-lived SSE stream of bus events; browser → server
is a handful of small JSON POSTs. SSE (rather than a WebSocket) because the
traffic is one-directional and bursty, it is plain HTTP, and the browser
reconnects on its own and tells us where it left off via ``Last-Event-ID``.

The server holds one ``Session`` (one agent). With no agent it still serves
the read-only views and recorded traces — that is "replay mode".
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, events, paths
from . import data
from .recorder import TraceRecorder, find_trace, list_traces, load_trace

STATIC_DIR = Path(__file__).parent / "static"

# Modes the console may switch between. bypassPermissions is deliberately not
# offered: a browser tab should not be able to turn the gate off.
SWITCHABLE_MODES = ("default", "acceptEdits", "dontAsk")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_KEEPALIVE_SECONDS = 15


class Session:
    """One agent plus the web-side state around it."""

    def __init__(self, agent: Any | None = None, *, recorder: TraceRecorder | None = None) -> None:
        self.agent = agent
        self.recorder = recorder
        self.busy = False
        self.since = 0  # a fresh page load replays bus history from here
        self._task: asyncio.Task | None = None
        self._confirms: dict[str, asyncio.Future[bool]] = {}
        if agent is not None:
            agent.set_confirm_fn(self.confirm)
        self._stop_recording = events.bus.subscribe(recorder) if recorder is not None else None

    @property
    def live(self) -> bool:
        return self.agent is not None

    # -- permission confirmation -------------------------------------------
    # The agent loop awaits ``confirm()``; the browser answers through
    # POST /api/confirm, which resolves the matching future. This reuses the
    # runtime's existing confirm hook, so the loop itself is unchanged.

    async def confirm(self, message: str) -> bool:
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._confirms[request_id] = future
        events.emit(events.CONFIRM_REQUEST, request_id=request_id, message=message)
        try:
            allowed = await future
        except asyncio.CancelledError:
            allowed = False
            raise
        finally:
            self._confirms.pop(request_id, None)
            events.emit(events.CONFIRM_RESOLVED, request_id=request_id, allowed=allowed)
        return allowed

    def resolve_confirm(self, request_id: str, allowed: bool) -> bool:
        future = self._confirms.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(allowed)
        return True

    # -- turns --------------------------------------------------------------

    def start_turn(self, message: str) -> None:
        self.busy = True
        self._task = asyncio.create_task(self._run_turn(message))

    async def _run_turn(self, message: str) -> None:
        ended_cleanly = False
        try:
            await self.agent.chat(message)
            ended_cleanly = True
        except Exception as exc:
            events.emit(events.NOTICE, level="error", message=str(exc))
        finally:
            self.busy = False
            if not ended_cleanly:
                events.emit(events.TURN_END)  # the loop only emits this on a normal exit

    def abort(self) -> None:
        for future in list(self._confirms.values()):
            if not future.done():
                future.set_result(False)
        if self.agent is not None:
            self.agent.abort()

    def reset(self) -> None:
        self.abort()
        if self.agent is not None:
            self.agent.clear_history()
        self.since = events.bus.last_seq
        events.emit(events.SESSION, **self.describe(), reset=True)

    def describe(self) -> dict[str, Any]:
        agent = self.agent
        return {
            "live": self.live,
            "busy": self.busy,
            "version": __version__,
            "model": getattr(agent, "model", ""),
            "permission_mode": getattr(agent, "permission_mode", ""),
            "modes": list(SWITCHABLE_MODES),
            "workspace": Path(os.getcwd()).name,
            "recording": self.recorder.path.name if self.recorder else "",
        }

    def close(self) -> None:
        if self._stop_recording is not None:
            self._stop_recording()
            self.recorder.close()


class ChatBody(BaseModel):
    message: str


class ConfirmBody(BaseModel):
    request_id: str
    allow: bool


class ModeBody(BaseModel):
    mode: str


def _trace_dirs() -> list[Path]:
    return [data.DEMO_TRACES_DIR, paths.project_dir() / "traces"]


def _sse(event: events.Event) -> str:
    return f"id: {event.seq}\ndata: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"


def create_app(session: Session | None = None) -> FastAPI:
    session = session or Session()
    app = FastAPI(title="Keelson console", version=__version__, docs_url=None, redoc_url=None)
    app.state.session = session

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        """Refuse state-changing requests that did not come from our own page.

        This server can make the agent run shell commands, so a random web page
        must not be able to drive it by POSTing to localhost (CSRF), nor reach
        it through a hostile DNS name that resolves to 127.0.0.1 (rebinding).
        """
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in _LOOPBACK_HOSTS and host != "testserver":
            return JSONResponse({"detail": "unexpected Host header"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname not in _LOOPBACK_HOSTS | {"testserver"}:
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
            if "application/json" not in (request.headers.get("content-type") or ""):
                return JSONResponse({"detail": "JSON body required"}, status_code=415)
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            # Static files: always revalidate (cheap 304s via ETag), so an
            # upgraded install never runs a stale script against a new server.
            response.headers["Cache-Control"] = "no-cache"
        return response

    # -- event stream ---------------------------------------------------------

    @app.get("/api/events")
    async def stream(request: Request, since: int | None = None) -> StreamingResponse:
        last_id = request.headers.get("last-event-id")
        if last_id and last_id.isdigit():
            start = int(last_id)          # automatic reconnect: resume where it dropped
        elif since is not None:
            start = since
        else:
            start = session.since         # fresh page load: current session only

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[events.Event] = asyncio.Queue()

        def on_event(event: events.Event) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        # Subscribe first, then read the backlog: an event published in between
        # shows up in both, and the seq check below drops the duplicate. The
        # other order would leave a window where an event is lost.
        unsubscribe = events.bus.subscribe(on_event)
        backlog = events.bus.history(start)

        async def body():
            sent = start
            try:
                for event in backlog:
                    sent = event.seq
                    yield _sse(event)
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.seq <= sent:
                        continue
                    sent = event.seq
                    yield _sse(event)
            finally:
                unsubscribe()

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- user actions ---------------------------------------------------------

    def require_agent() -> None:
        if not session.live:
            raise HTTPException(409, "replay mode: no model is configured")

    @app.post("/api/chat")
    async def chat(body: ChatBody) -> dict[str, Any]:
        require_agent()
        message = body.message.strip()
        if not message:
            raise HTTPException(422, "empty message")
        if session.busy:
            raise HTTPException(409, "a turn is already running")
        session.start_turn(message)
        return {"ok": True}

    @app.post("/api/confirm")
    async def confirm(body: ConfirmBody) -> dict[str, Any]:
        if not session.resolve_confirm(body.request_id, body.allow):
            raise HTTPException(404, "no pending confirmation with that id")
        return {"ok": True}

    @app.post("/api/abort")
    async def abort() -> dict[str, Any]:
        require_agent()
        session.abort()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset() -> dict[str, Any]:
        require_agent()
        if session.busy:
            raise HTTPException(409, "a turn is running; stop it first")
        session.reset()
        return {"ok": True}

    @app.post("/api/mode")
    async def set_mode(body: ModeBody) -> dict[str, Any]:
        require_agent()
        if body.mode not in SWITCHABLE_MODES:
            raise HTTPException(422, f"mode must be one of {', '.join(SWITCHABLE_MODES)}")
        session.agent.permission_mode = body.mode
        events.emit(events.SESSION, **session.describe())
        return {"ok": True, "permission_mode": body.mode}

    # -- read-only views ------------------------------------------------------

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return session.describe()

    @app.get("/api/bench")
    async def bench() -> dict[str, Any]:
        return data.bench_overview()

    @app.get("/api/evolution")
    async def evolution() -> list[dict[str, Any]]:
        return data.evolution_lineages()

    @app.get("/api/skills")
    async def skills() -> list[dict[str, Any]]:
        return data.live_skills()

    @app.get("/api/traces")
    async def traces() -> list[dict[str, Any]]:
        return list_traces(_trace_dirs())

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str) -> dict[str, Any]:
        path = find_trace(_trace_dirs(), trace_id)
        if path is None:
            raise HTTPException(404, "trace not found")
        return load_trace(path)

    # -- front end ------------------------------------------------------------

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
    return app

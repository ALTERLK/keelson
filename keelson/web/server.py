"""HTTP layer for the web console.

Server → browser is one long-lived SSE stream of bus events per session;
browser → server is a handful of small JSON POSTs. SSE (rather than a
WebSocket) because the traffic is one-directional and bursty, it is plain
HTTP, and the browser reconnects on its own and tells us where it left off via
``Last-Event-ID``.

Sessions live in ``sessions.py``. With no agent factory the server still serves
the read-only views and recorded traces — that is "replay mode".
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, events, paths
from . import data
from .recorder import find_trace, list_traces, load_trace
from .sessions import SWITCHABLE_MODES, Session, SessionLimitError, SessionManager

STATIC_DIR = Path(__file__).parent / "static"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_KEEPALIVE_SECONDS = 15

__all__ = ["STATIC_DIR", "Session", "SessionManager", "create_app"]


class ChatBody(BaseModel):
    message: str


class ConfirmBody(BaseModel):
    request_id: str
    allow: bool
    scope: str | None = None      # "once" | "session" | "project" (when allowing)
    reason: str | None = None     # why, when denying: passed on to the model


class ModeBody(BaseModel):
    mode: str


class CreateBody(BaseModel):
    resume: str | None = None


def _trace_dirs() -> list[Path]:
    return [data.DEMO_TRACES_DIR, paths.project_traces_dir(), paths.legacy_traces_dir()]


def _sse(event: events.Event) -> str:
    return f"id: {event.seq}\ndata: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"


def create_app(manager: SessionManager | None = None) -> FastAPI:
    manager = manager or SessionManager()
    app = FastAPI(title="Keelson console", version=__version__, docs_url=None, redoc_url=None)
    app.state.manager = manager

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

    def session_or_404(session_id: str) -> Session:
        session = manager.get(session_id)
        if session is None:
            raise HTTPException(404, "no such session")
        return session

    # -- server and sessions ----------------------------------------------------

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return {
            "live": manager.live,
            "version": __version__,
            "modes": list(SWITCHABLE_MODES),
            "workspace": Path(os.getcwd()).name,
            "sessions": [s.describe() for s in manager.sessions()],
        }

    @app.get("/api/sessions")
    async def sessions() -> dict[str, Any]:
        return {"live": [s.describe() for s in manager.sessions()], "history": manager.history()}

    @app.post("/api/sessions")
    async def create_session(body: CreateBody) -> dict[str, Any]:
        if not manager.live:
            raise HTTPException(409, "replay mode: no model is configured")
        try:
            return manager.create(resume=body.resume).describe()
        except KeyError:
            raise HTTPException(404, "no saved session with that id") from None
        except SessionLimitError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/sessions/{session_id}")
    async def describe_session(session_id: str) -> dict[str, Any]:
        return session_or_404(session_id).describe()

    @app.delete("/api/sessions/{session_id}")
    async def close_session(session_id: str) -> dict[str, Any]:
        if not manager.close(session_id):
            raise HTTPException(404, "no such session")
        return {"ok": True}

    # -- one session: event stream ----------------------------------------------

    @app.get("/api/sessions/{session_id}/events")
    async def stream(session_id: str, request: Request, since: int = 0) -> StreamingResponse:
        bus = session_or_404(session_id).bus
        last_id = request.headers.get("last-event-id")
        # Automatic reconnect: resume where the stream dropped. A fresh page
        # load starts from the beginning of the session.
        start = int(last_id) if last_id and last_id.isdigit() else since

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[events.Event] = asyncio.Queue()

        def on_event(event: events.Event) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        # Subscribe first, then read the backlog: an event published in between
        # shows up in both, and the seq check below drops the duplicate. The
        # other order would leave a window where an event is lost.
        unsubscribe = bus.subscribe(on_event)
        backlog = bus.history(start)

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

    # -- one session: user actions ----------------------------------------------

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatBody) -> dict[str, Any]:
        session = session_or_404(session_id)
        message = body.message.strip()
        if not message:
            raise HTTPException(422, "empty message")
        if session.busy:
            raise HTTPException(409, "a turn is already running")
        session.start_turn(message)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/confirm")
    async def confirm(session_id: str, body: ConfirmBody) -> dict[str, Any]:
        if not session_or_404(session_id).resolve_confirm(body.request_id, body.allow, body.scope, body.reason):
            raise HTTPException(404, "no pending confirmation with that id")
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/abort")
    async def abort(session_id: str) -> dict[str, Any]:
        session_or_404(session_id).abort()
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/mode")
    async def set_mode(session_id: str, body: ModeBody) -> dict[str, Any]:
        session = session_or_404(session_id)
        try:
            session.set_mode(body.mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": True, "permission_mode": body.mode}

    # -- read-only views ----------------------------------------------------------

    @app.get("/api/bench")
    async def bench() -> dict[str, Any]:
        return data.bench_overview()

    @app.get("/api/evolution")
    async def evolution() -> list[dict[str, Any]]:
        return data.evolution_lineages()

    @app.get("/api/skills")
    async def skills() -> list[dict[str, Any]]:
        return data.live_skills()

    @app.get("/api/casestudy")
    async def casestudy() -> dict[str, Any]:
        return data.case_study()

    @app.get("/api/perf")
    async def perf() -> dict[str, Any]:
        return data.perf_summary(_trace_dirs())

    @app.get("/api/doctor")
    async def doctor() -> dict[str, Any]:
        from ..doctor import run_checks

        return {"checks": await run_checks(ping_model=False)}

    @app.get("/api/traces")
    async def traces() -> list[dict[str, Any]]:
        return list_traces([data.DEMO_TRACES_DIR])

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str) -> dict[str, Any]:
        path = find_trace(_trace_dirs(), trace_id)
        if path is None:
            raise HTTPException(404, "trace not found")
        return load_trace(path)

    # -- front end ----------------------------------------------------------------

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
    return app

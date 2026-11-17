"""Sessions: several independent conversations in one server process.

Each ``Session`` owns its own event bus, agent, trace recorder and pending
permission prompts. A turn runs in a context where that session's bus is the
current one (``events.bind_bus``), so everything the turn publishes — including
from concurrent tool calls and background skill tasks, which inherit the
context — lands on that session's stream and nowhere else.

What sessions do *not* isolate: they share the process's working directory,
and with it the files the tools read and write. Two sessions are two
conversations about one workspace, not two sandboxes.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import events
from ..events import EventBus
from .recorder import TraceRecorder, find_trace, list_traces, load_trace

# Modes the console may switch between. bypassPermissions is deliberately not
# offered: a browser tab should not be able to turn the gate off.
SWITCHABLE_MODES = ("default", "acceptEdits", "dontAsk")

MAX_LIVE_SESSIONS = 8


class SessionLimitError(RuntimeError):
    pass


class Session:
    """One agent plus the web-side state around it."""

    def __init__(self, agent: Any, *, recorder: TraceRecorder | None = None, bus: EventBus | None = None) -> None:
        self.agent = agent
        self.bus = bus or EventBus()
        self.recorder = recorder
        self.id: str = getattr(agent, "session_id", None) or uuid.uuid4().hex[:8]
        self.created_at = time.time()
        self.last_active = self.created_at
        self.title = ""
        self.turns = 0
        self.busy = False
        self.resumed_from = ""
        self._task: asyncio.Task | None = None
        self._confirms: dict[str, asyncio.Future[bool]] = {}

        agent.set_confirm_fn(self.confirm)
        self._stop_recording = self.bus.subscribe(recorder) if recorder is not None else None
        self.bus.subscribe(self._observe)

    def _observe(self, event: events.Event) -> None:
        if event.type == events.USER_MESSAGE:
            self.last_active = event.ts
            if not self.title:
                self.title = str(event.data.get("text", ""))[:80]
        elif event.type == events.TURN_END:
            self.turns += 1

    # -- permission confirmation -------------------------------------------
    # The agent loop awaits ``confirm()``; the browser answers through
    # POST .../confirm, which resolves the matching future. This reuses the
    # runtime's existing confirm hook, so the loop itself is unchanged.

    async def confirm(self, message: str, request: dict | None = None) -> dict:
        request = request or {}
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._confirms[request_id] = future
        self.bus.emit(events.CONFIRM_REQUEST, request_id=request_id, message=message,
                      reason=request.get("reason", ""), options=request.get("options") or ["once", "deny"])
        answer: dict = {"choice": "deny"}
        try:
            answer = await future
        finally:
            self._confirms.pop(request_id, None)
            self.bus.emit(events.CONFIRM_RESOLVED, request_id=request_id,
                          allowed=answer.get("choice") in ("once", "session", "project"),
                          choice=answer.get("choice"))
        return answer

    def resolve_confirm(self, request_id: str, allowed: bool, scope: str | None = None,
                        reason: str | None = None) -> bool:
        future = self._confirms.get(request_id)
        if future is None or future.done():
            return False
        choice = (scope if scope in ("once", "session", "project") else "once") if allowed else "deny"
        future.set_result({"choice": choice, "reason": (reason or "").strip()})
        return True

    # -- turns --------------------------------------------------------------

    def start_turn(self, message: str) -> None:
        self.busy = True
        # The task gets its own copy of the context with this session's bus
        # bound; tasks it creates in turn copy that, so the binding follows the
        # whole turn without any of the runtime code knowing about sessions.
        context = contextvars.copy_context()
        context.run(events.bind_bus, self.bus)
        self._task = asyncio.get_running_loop().create_task(self._run_turn(message), context=context)

    async def _run_turn(self, message: str) -> None:
        ended_cleanly = False
        try:
            await self.agent.chat(message)
            ended_cleanly = True
        except Exception as exc:
            self.bus.emit(events.NOTICE, level="error", message=str(exc))
        finally:
            self.busy = False
            if not ended_cleanly:
                self.bus.emit(events.TURN_END)  # the loop only emits this on a normal exit

    def abort(self) -> None:
        for future in list(self._confirms.values()):
            if not future.done():
                future.set_result({"choice": "deny"})
        self.agent.abort()

    def set_mode(self, mode: str) -> None:
        if mode not in SWITCHABLE_MODES:
            raise ValueError(f"mode must be one of {', '.join(SWITCHABLE_MODES)}")
        self.agent.permission_mode = mode
        self.bus.emit(events.SESSION, **self.describe())

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "busy": self.busy,
            "turns": self.turns,
            "model": getattr(self.agent, "model", ""),
            "permission_mode": getattr(self.agent, "permission_mode", ""),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "recording": self.recorder.path.stem if self.recorder else "",
            "resumed_from": self.resumed_from,
        }

    def close(self) -> None:
        self.abort()
        if self._stop_recording is not None:
            self._stop_recording()
            self.recorder.close()


class SessionManager:
    """Creates, finds and retires sessions; lists saved ones for resuming."""

    def __init__(
        self,
        agent_factory: Callable[[], Any] | None = None,
        *,
        traces_dir: Path | None = None,
        extra_trace_dirs: list[Path] | None = None,
        record: bool = True,
        max_sessions: int = MAX_LIVE_SESSIONS,
    ) -> None:
        self._factory = agent_factory
        self._traces_dir = traces_dir
        # Older recordings (e.g. the pre-0.4 in-project folder): read, never written.
        self._read_dirs = [d for d in [traces_dir, *(extra_trace_dirs or [])] if d is not None]
        self._record = record and traces_dir is not None
        self._max = max_sessions
        self._sessions: dict[str, Session] = {}

    @property
    def live(self) -> bool:
        """False in replay mode: no model is configured, so no session can run."""
        return self._factory is not None

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def sessions(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.last_active, reverse=True)

    def _make_room(self) -> None:
        if len(self._sessions) < self._max:
            return
        idle = [s for s in self._sessions.values() if not s.busy]
        if not idle:
            raise SessionLimitError(f"all {self._max} sessions are busy")
        # Retire the least recently used idle session. Its trace stays on disk,
        # so it shows up under history and can be resumed.
        self.close(min(idle, key=lambda s: s.last_active).id)

    def create(self, *, resume: str | None = None) -> Session:
        if self._factory is None:
            raise RuntimeError("replay mode: no model is configured")
        previous = self._load_saved(resume) if resume else None
        self._make_room()

        agent = self._factory()
        recorder = None
        if self._record:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            recorder = TraceRecorder(
                self._traces_dir / f"{stamp}-{agent.session_id}.jsonl",
                meta={"model": getattr(agent, "model", "")},
            )
        session = Session(agent, recorder=recorder)
        if previous is not None:
            self._restore(session, resume, previous)
        self._sessions[session.id] = session
        session.bus.emit(events.SESSION, **session.describe())
        return session

    # -- saved sessions -----------------------------------------------------

    def _load_saved(self, trace_id: str) -> dict[str, Any]:
        path = find_trace(self._read_dirs, trace_id) if self._read_dirs else None
        if path is None:
            raise KeyError(trace_id)
        return load_trace(path)

    @staticmethod
    def _agent_session_id(trace_id: str) -> str:
        # Trace files are named <utc stamp>-<agent session id>.
        return trace_id.rsplit("-", 1)[-1]

    def _restore(self, session: Session, trace_id: str, trace: dict[str, Any]) -> None:
        from ..session import load_session

        # 1. Show the earlier conversation: replay its events onto the new bus
        #    (and so into the new recording, which stays self-contained).
        for row in trace["events"]:
            if row["type"] in (events.SESSION, events.CONFIRM_REQUEST, events.CONFIRM_RESOLVED):
                continue
            session.bus.publish(row["type"], row["data"])
        # 2. Give the model its memory back: the saved message history.
        saved = load_session(self._agent_session_id(trace_id))
        if saved:
            with events.use_bus(session.bus):
                session.agent.restore_session({
                    "anthropicMessages": saved.get("anthropicMessages"),
                    "openaiMessages": saved.get("openaiMessages"),
                })
        session.resumed_from = trace_id

    def history(self) -> list[dict[str, Any]]:
        """Recorded sessions of this workspace that are not currently live."""
        if not self._read_dirs:
            return []
        from ..session import load_session

        live = {s.recorder.path.stem for s in self._sessions.values() if s.recorder}
        rows = []
        for row in list_traces(self._read_dirs):
            if row["id"] in live:
                continue
            row["resumable"] = self.live and load_session(self._agent_session_id(row["id"])) is not None
            rows.append(row)
        return sorted(rows, key=lambda r: r.get("recorded_at", ""), reverse=True)

    def close(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def close_all(self) -> None:
        for session_id in list(self._sessions):
            self.close(session_id)

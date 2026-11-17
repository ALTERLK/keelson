"""In-process event bus — the seam between "what the agent did" and "how it is shown".

The agent loop never talks to a display directly. It publishes typed events
here, and anything that wants to observe a run subscribes:

    Agent loop ──emit──▶ EventBus ──┬─▶ terminal renderer   (keelson/ui.py)
                                    ├─▶ SSE broadcaster      (keelson/web/server.py)
                                    └─▶ trace recorder       (keelson/web/recorder.py)

Design notes
------------
* Dispatch is synchronous and in publish order, so a subscriber sees exactly
  the sequence the loop produced (the terminal relies on this for streaming).
* A failing subscriber is isolated: one broken consumer (say, a closed browser
  tab) must never take the agent loop down with it.
* A bounded history ring lets a late or reconnecting consumer catch up from a
  known sequence number (this is what backs SSE ``Last-Event-ID``).
* ``emit()`` publishes on the *current* bus. By default that is the one
  process-wide bus (the CLI). The web console gives every session its own bus
  and binds it with a ``ContextVar``, so a turn running in one session, and
  every task that turn spawns, publish to that session only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Event types published by the runtime. Kept as constants so producers and
# consumers (including the JS front end's reducer) share one vocabulary.
USER_MESSAGE = "user_message"          # {text}
ASSISTANT_DELTA = "assistant_delta"    # {text}
THINKING = "thinking"                  # {active}
TOOL_CALL = "tool_call"                # {id, name, input, summary}
PERMISSION = "permission"              # {id, name, action, message, speculative}
CONFIRM_PROMPT = "confirm_prompt"      # {message}            (terminal prompt)
CONFIRM_REQUEST = "confirm_request"    # {request_id, message} (web prompt)
CONFIRM_RESOLVED = "confirm_resolved"  # {request_id, allowed}
TOOL_OUTPUT = "tool_output"            # {id, text}   live output of a running command
TOOL_RESULT = "tool_result"            # {id, name, ok, preview, chars}
SPAN = "span"                          # {name, dur_ms, ...}  how long one step of the loop took
LOOP_GUARD = "loop_guard"              # {name, hits}
CONTEXT_FOLD = "context_fold"          # {trigger}
USAGE = "usage"                        # {input_tokens, output_tokens, context_tokens, context_window, turns}
SKILL_EVOLUTION = "skill_evolution"    # {action, skill, version, gate}
SUBAGENT = "subagent"                  # {phase, agent_type, description}
RETRY = "retry"                        # {attempt, max_retries, reason}
NOTICE = "notice"                      # {level, message}
VERIFY = "verify"                      # {phase: running|done, commands | status, detail, runs, weakened, unproven}
TURN_END = "turn_end"                  # {}
SESSION = "session"                    # {model, permission_mode, cwd, ...}

Subscriber = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "type": self.type, "data": self.data}


def _jsonable(value: Any) -> Any:
    """Coerce a payload to plain JSON types so every consumer can serialise it."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class EventBus:
    def __init__(self, history_size: int = 5000) -> None:
        self._subscribers: list[Subscriber] = []
        self._history: deque[Event] = deque(maxlen=history_size)
        self._seq = 0
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        """Register a subscriber; returns a function that removes it again."""
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)

        return unsubscribe

    def emit(self, type: str, **data: Any) -> Event:
        return self.publish(type, data)

    def publish(self, type: str, data: dict[str, Any]) -> Event:
        """Like ``emit``, with the payload as a dict (for replaying stored events)."""
        with self._lock:
            self._seq += 1
            event = Event(seq=self._seq, ts=time.time(), type=type, data=_jsonable(data))
            self._history.append(event)
            subscribers = list(self._subscribers)
        for fn in subscribers:
            try:
                fn(event)
            except Exception:  # a consumer must never break the producer
                logger.exception("event subscriber failed on %s", type)
        return event

    def history(self, since: int = 0) -> list[Event]:
        """Events with ``seq > since`` that are still inside the ring."""
        with self._lock:
            return [e for e in self._history if e.seq > since]

    @property
    def last_seq(self) -> int:
        return self._seq

    def reset(self) -> None:
        """Drop subscribers, history and the sequence counter (tests, new session)."""
        with self._lock:
            self._subscribers.clear()
            self._history.clear()
            self._seq = 0


bus = EventBus()  # the process-wide default (CLI, benchmarks, tests)

# Which bus `emit()` publishes on, and which tool call is running, in the
# current execution context. asyncio copies the context into every task it
# creates and `asyncio.to_thread` copies it into the worker thread, so both
# follow a turn through concurrent tool calls without being passed around.
_current_bus: ContextVar[EventBus | None] = ContextVar("keelson_bus", default=None)
_current_call: ContextVar[str | None] = ContextVar("keelson_call", default=None)


def current_bus() -> EventBus:
    return _current_bus.get() or bus


def bind_bus(target: EventBus) -> None:
    """Make ``target`` the current bus for the rest of this context."""
    _current_bus.set(target)


@contextmanager
def use_bus(target: EventBus) -> Iterator[EventBus]:
    token = _current_bus.set(target)
    try:
        yield target
    finally:
        _current_bus.reset(token)


@contextmanager
def tool_call(call_id: str | None) -> Iterator[None]:
    """Mark everything emitted inside the block as belonging to one tool call."""
    token = _current_call.set(call_id)
    try:
        yield
    finally:
        _current_call.reset(token)


def current_call_id() -> str | None:
    return _current_call.get()


def emit(type: str, **data: Any) -> Event:
    """Publish on the current bus."""
    return current_bus().emit(type, **data)


@contextmanager
def span(name: str, **meta: Any) -> Iterator[None]:
    """Time a step of the loop and publish it as a ``span`` event.

    Also written through ``keelson.trace`` when ``KEELSON_TRACE=1``, so the
    file-based profile and the live view come from the same measurement.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        dur_ms = round((time.perf_counter() - start) * 1000, 2)
        emit(SPAN, name=name, dur_ms=dur_ms, **meta)
        from . import trace

        trace.emit(name, dur_ms, **meta)

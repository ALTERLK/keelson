"""In-process event bus — the seam between "what the agent did" and "how it is shown".

The agent loop never talks to a display directly. It publishes typed events
here, and anything that wants to observe a run subscribes:

    Agent loop ──emit──▶ EventBus ──┬─▶ terminal renderer   (agents/ui.py)
                                    ├─▶ SSE broadcaster      (agents/web/server.py)
                                    └─▶ trace recorder       (agents/web/recorder.py)

Design notes
------------
* Dispatch is synchronous and in publish order, so a subscriber sees exactly
  the sequence the loop produced (the terminal relies on this for streaming).
* A failing subscriber is isolated: one broken consumer (say, a closed browser
  tab) must never take the agent loop down with it.
* A bounded history ring lets a late or reconnecting consumer catch up from a
  known sequence number (this is what backs SSE ``Last-Event-ID``).
* The bus is process-global, which means one live session per process. Hosting
  several sessions side by side would need a bus per session (contextvars).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
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
TOOL_RESULT = "tool_result"            # {id, name, ok, preview, chars}
LOOP_GUARD = "loop_guard"              # {name, hits}
CONTEXT_FOLD = "context_fold"          # {trigger}
USAGE = "usage"                        # {input_tokens, output_tokens, context_tokens, context_window, turns}
SKILL_EVOLUTION = "skill_evolution"    # {action, skill, version, gate}
SUBAGENT = "subagent"                  # {phase, agent_type, description}
RETRY = "retry"                        # {attempt, max_retries, reason}
NOTICE = "notice"                      # {level, message}
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


bus = EventBus()


def emit(type: str, **data: Any) -> Event:
    """Publish on the process-wide bus."""
    return bus.emit(type, **data)

"""Trace recording: persist a run's event stream so it can be replayed later.

A trace is a JSONL file. The first line is a header; every following line is
one bus event with ``t`` = seconds since the first recorded event:

    {"type": "trace_header", "data": {"title": "...", "model": "...", ...}}
    {"t": 0.0,  "type": "user_message", "data": {"text": "..."}}
    {"t": 0.41, "type": "tool_call",    "data": {"id": "...", "name": "read_file", ...}}

Replay itself lives in the front end (``static/js/stream.js``): the player
feeds these events into the same reducer the live SSE stream uses, so a replay
renders through exactly the code path a live run does. That is also what lets
the static demo site work with no server at all.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import events

HEADER_TYPE = "trace_header"

# Events that only make sense inside a live terminal session.
_SKIP_TYPES = {events.CONFIRM_PROMPT}


class TraceRecorder:
    """Bus subscriber that appends every event to a JSONL file."""

    def __init__(self, path: Path, *, meta: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._t0: float | None = None
        self._count = 0
        header = {"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **(meta or {})}
        self._fh = self.path.open("w", encoding="utf-8", newline="\n")
        self._write({"type": HEADER_TYPE, "data": header})

    def _write(self, row: dict[str, Any]) -> None:
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()  # a crash mid-run should still leave a usable trace

    def __call__(self, event: events.Event) -> None:
        if event.type in _SKIP_TYPES or self._fh.closed:
            return
        if self._t0 is None:
            self._t0 = event.ts
        self._count += 1
        self._write({"t": round(event.ts - self._t0, 3), "type": event.type, "data": event.data})

    @property
    def event_count(self) -> int:
        return self._count

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()
        if self._count == 0:  # nothing happened — don't leave an empty trace behind
            self.path.unlink(missing_ok=True)


def load_trace(path: Path) -> dict[str, Any]:
    """Parse a trace file into ``{"meta": {...}, "events": [...]}``."""
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a torn last line from an interrupted run
            if row.get("type") == HEADER_TYPE:
                meta = dict(row.get("data") or {})
            else:
                rows.append({"t": float(row.get("t", 0.0)), "type": row.get("type", ""), "data": row.get("data") or {}})
    return {"meta": meta, "events": rows}


def describe_trace(path: Path) -> dict[str, Any]:
    """Summary row for the trace picker."""
    trace = load_trace(path)
    evs = trace["events"]
    first_prompt = next((e["data"].get("text", "") for e in evs if e["type"] == events.USER_MESSAGE), "")
    meta = trace["meta"]
    return {
        "id": Path(path).stem,
        "title": meta.get("title") or first_prompt[:80] or Path(path).stem,
        "description": meta.get("description", ""),
        "model": meta.get("model", ""),
        "recorded_at": meta.get("recorded_at", ""),
        "events": len(evs),
        "messages": sum(1 for e in evs if e["type"] == events.USER_MESSAGE),
        "duration_s": round(evs[-1]["t"], 1) if evs else 0.0,
        "tool_calls": sum(1 for e in evs if e["type"] == events.TOOL_CALL),
    }


def list_traces(directories: list[Path]) -> list[dict[str, Any]]:
    """Traces from several directories; earlier directories win on id clashes."""
    seen: dict[str, dict[str, Any]] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            if path.stem in seen:
                continue
            try:
                row = describe_trace(path)
            except OSError:
                continue
            if row["events"]:
                seen[path.stem] = row
    return list(seen.values())


def find_trace(directories: list[Path], trace_id: str) -> Path | None:
    # The id comes from a URL — only ever match it against real file stems,
    # never join it into a path.
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.glob("*.jsonl"):
            if path.stem == trace_id:
                return path
    return None

"""Minimal span tracing — no dependency, JSONL sink.

Enable with ``KEELSON_TRACE=1`` (optionally ``KEELSON_TRACE_FILE=<path>``, else
``<KEELSON_HOME>/trace/<session>.jsonl``). Each span is one line:

    {"t": <epoch>, "span": "model_stream", "dur_ms": 812.4, "meta": {...}}

Used to profile where wall-clock goes (model stream vs. tool exec vs. permission
checks) and to A/B the speculative early-execution path (``KEELSON_SPECULATIVE``).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

_ENABLED: bool | None = None
_SINK: Path | None = None


def enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        raw = os.environ.get("KEELSON_TRACE", "").strip().lower()
        _ENABLED = raw not in ("", "0", "false", "no", "off")
    return _ENABLED


def speculative_enabled() -> bool:
    raw = os.environ.get("KEELSON_SPECULATIVE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _sink(session_id: str = "session") -> Path | None:
    global _SINK
    if _SINK is not None:
        return _SINK
    override = os.environ.get("KEELSON_TRACE_FILE", "").strip()
    if override:
        _SINK = Path(override)
    else:
        from agents import paths

        _SINK = paths.user_home() / "trace" / f"{session_id}.jsonl"
    _SINK.parent.mkdir(parents=True, exist_ok=True)
    return _SINK


def emit(span: str, dur_ms: float, **meta) -> None:
    if not enabled():
        return
    try:
        row = {"t": round(time.time(), 3), "span": span, "dur_ms": round(dur_ms, 2), "meta": meta}
        with _sink().open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


@contextmanager
def span(name: str, **meta):
    if not enabled():
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        emit(name, (time.perf_counter() - start) * 1000.0, **meta)


def reset_for_tests() -> None:
    global _ENABLED, _SINK
    _ENABLED = None
    _SINK = None


# ------------------------------------------------------------- report ---

def summarize(path: str | Path) -> dict:
    """Aggregate a trace file: per-span count, total ms, p50/p95 ms."""
    from statistics import median

    buckets: dict[str, list[float]] = {}
    p = Path(path)
    if not p.is_file():
        return {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        buckets.setdefault(str(row.get("span", "?")), []).append(float(row.get("dur_ms", 0)))

    def p95(v: list[float]) -> float:
        if not v:
            return 0.0
        s = sorted(v)
        return s[min(len(s) - 1, int(0.95 * len(s)))]

    return {
        name: {
            "count": len(v),
            "total_ms": round(sum(v), 1),
            "p50_ms": round(median(v), 1) if v else 0.0,
            "p95_ms": round(p95(v), 1),
        }
        for name, v in sorted(buckets.items(), key=lambda kv: -sum(kv[1]))
    }

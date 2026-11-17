"""What the model calls cost: one line per turn, kept across sessions, and a daily budget.

``~/.keelson/usage.jsonl`` gets a record per turn (project, session, model,
tokens, cache hits, estimated cost). ``/usage`` sums it; ``KEELSON_DAILY_BUDGET_USD``
stops new turns once today's estimated spend across all projects reaches it.
Each record names the model that answered, so a change of model is never silent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths


def usage_file() -> Path:
    return paths.user_home() / "usage.jsonl"


def record(*, session_id: str, model: str, input_tokens: int, output_tokens: int, cache_hit_tokens: int,
           cost_usd: float, cwd: Path | None = None) -> None:
    if input_tokens <= 0 and output_tokens <= 0:
        return
    line = {
        "ts": round(time.time(), 1),
        "day": time.strftime("%Y-%m-%d"),
        "project": str(cwd or Path.cwd()),
        "session": session_id,
        "model": model,
        "in": int(input_tokens),
        "out": int(output_tokens),
        "hit": int(cache_hit_tokens),
        "usd": round(float(cost_usd), 6),
    }
    try:
        target = usage_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _records():
    try:
        with usage_file().open(encoding="utf-8") as f:
            for raw in f:
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue
    except OSError:
        return


@dataclass
class Totals:
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, r: dict) -> None:
        self.turns += 1
        self.input_tokens += r.get("in", 0)
        self.output_tokens += r.get("out", 0)
        self.cache_hit_tokens += r.get("hit", 0)
        self.cost_usd += r.get("usd", 0.0)

    @property
    def hit_rate(self) -> float | None:
        return self.cache_hit_tokens / self.input_tokens if self.input_tokens else None


def totals(*, days: int = 1, project: str | None = None, session: str | None = None) -> Totals:
    """Sums over the last ``days`` calendar days (1 = today)."""
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    out = Totals()
    for r in _records():
        if r.get("day", "") < since:
            continue
        if project is not None and os.path.normcase(r.get("project", "")) != os.path.normcase(project):
            continue
        if session is not None and r.get("session") != session:
            continue
        out.add(r)
    return out


def by_model(days: int = 7) -> dict[str, Totals]:
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    out: dict[str, Totals] = {}
    for r in _records():
        if r.get("day", "") >= since:
            out.setdefault(r.get("model", "?"), Totals()).add(r)
    return out


def daily_budget() -> float | None:
    raw = os.environ.get("KEELSON_DAILY_BUDGET_USD", "").strip()
    try:
        value = float(raw) if raw else None
    except ValueError:
        return None
    return value if value and value > 0 else None


def over_daily_budget() -> str | None:
    """A message when today's spend has reached the daily budget; None otherwise."""
    budget = daily_budget()
    if budget is None:
        return None
    spent = totals(days=1).cost_usd
    if spent >= budget:
        return (f"Today's estimated spend ${spent:.4f} has reached KEELSON_DAILY_BUDGET_USD=${budget:g}. "
                "No new turns until tomorrow, or raise the budget.")
    return None


def fmt(t: Totals) -> str:
    rate = f", cache hit {t.hit_rate:.0%}" if t.hit_rate is not None else ""
    return (f"{t.turns} turn(s), {t.input_tokens:,} in / {t.output_tokens:,} out{rate}, "
            f"est. ${t.cost_usd:.4f}")

"""Read-only views over artifacts already in the repo.

Nothing here is hand-entered: benchmark figures are recomputed from the raw
``bench/runs/*/results.jsonl`` rows with the same ``keelson.bench.metrics``
functions the written report uses, and the skill-evolution view is assembled
from the files the real pipeline produced. ``bench/dashboard.json`` only names
which run directories make up each series.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..bench import metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = REPO_ROOT / "bench"
EVOLUTION_DEMO_DIR = REPO_ROOT / "docs" / "self-evolution-demo"
DEMO_TRACES_DIR = REPO_ROOT / "demo" / "traces"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _rows(run_names: list[str]) -> list[dict]:
    rows: list[dict] = []
    for name in run_names:
        path = BENCH_DIR / "runs" / name / "results.jsonl"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
    return rows


# ─── Benchmarks ─────────────────────────────────────────────


def _toolhop_series(label: str, run_names: list[str]) -> dict[str, Any]:
    rows = _rows(run_names)
    pooled = metrics.pooled_arm_accuracy(rows)
    n = max(1, len(rows))
    by_hops: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = by_hops.setdefault(str(row.get("hops")), {"n": 0, "correct": 0})
        bucket["n"] += 1
        bucket["correct"] += 1 if row.get("correct") else 0
    for bucket in by_hops.values():
        bucket["accuracy"] = bucket["correct"] / max(1, bucket["n"])
    return {
        "label": label,
        "runs": run_names,
        "n_rows": pooled["n_rows"],
        "n_tasks": pooled["n_tasks"],
        "accuracy": pooled["accuracy"],
        "hit_cap_rate": sum(1 for r in rows if r.get("hit_cap")) / n,
        "avg_model_calls": sum(r.get("model_calls", 0) for r in rows) / n,
        "invalid_tool_call_rate": sum(1 for r in rows if r.get("invalid_tool_calls")) / n,
        "by_hops": dict(sorted(by_hops.items())),
    }


def _delta(a_runs: list[str], b_runs: list[str]) -> dict[str, Any]:
    return metrics.paired_bootstrap_delta_pooled(_rows(a_runs), _rows(b_runs), iters=10_000, seed=0)


def _sandbox_cell(cell: dict[str, Any]) -> dict[str, Any]:
    rows = _rows([cell["run"]])
    n = max(1, len(rows))
    return {
        "arm": cell["arm"],
        "tier": cell["tier"],
        "model": rows[0].get("model", "") if rows else "",
        "n": len(rows),
        "solve_rate": sum(1 for r in rows if r.get("tests_pass")) / n,
        "test_tamper_rate": sum(1 for r in rows if r.get("test_file_modified")) / n,
        "out_of_scope_rate": sum(1 for r in rows if r.get("out_of_scope_edits")) / n,
        "avg_turns": sum(r.get("turns", 0) for r in rows) / n,
        "avg_input_tokens": sum(r.get("input_tokens", 0) for r in rows) / n,
        "tasks": [
            {"task_id": r.get("task_id"), "kind": r.get("kind"), "pass": bool(r.get("tests_pass")), "turns": r.get("turns")}
            for r in rows
        ],
    }


@lru_cache(maxsize=1)
def bench_overview() -> dict[str, Any]:
    manifest = _read_json(BENCH_DIR / "dashboard.json")
    if not manifest:
        return {"available": False}
    th = manifest["toolhop"]
    arms = {label: _toolhop_series(label, runs) for label, runs in th["arms"].items()}
    before = _toolhop_series("before", th["fix"]["before"])
    after = _toolhop_series("after", th["fix"]["after"])
    comparisons = []
    for a, b in th.get("comparisons", []):
        comparisons.append({"a": a, "b": b, **_delta(th["arms"][a], th["arms"][b])})
    return {
        "available": True,
        "toolhop": {
            "model": th.get("model", ""),
            "dataset": th.get("dataset", ""),
            "fix": {
                "before": before,
                "after": after,
                "delta": _delta(th["fix"]["after"], th["fix"]["before"]),
            },
            "arms": arms,
            "comparisons": comparisons,
        },
        "sandbox": [_sandbox_cell(cell) for cell in manifest.get("sandbox", [])],
    }


# ─── Skill evolution ────────────────────────────────────────


def _unified(old: str, new: str, old_label: str, new_label: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=old_label, tofile=new_label,
    ))


def _lineage(directory: Path) -> dict[str, Any] | None:
    log = _read_json(directory / "run_log.json")
    if not log:
        return None
    steps = []
    for step in log.get("steps", []):
        result = step.get("result") or {}
        candidate = result.get("candidate") or {}
        instructions = candidate.get("instructions", "")
        if isinstance(instructions, list):
            instructions = "\n".join(str(item) for item in instructions)
        steps.append({
            "step": step.get("step", ""),
            "action": result.get("action", ""),
            "version": result.get("version"),
            "evidence": str(candidate.get("evidence", "")).lstrip("-• ").strip(),
            "candidate": {"name": candidate.get("name", ""), "instructions": instructions},
            "decision": result.get("decision") or {},
            "gate": result.get("gate"),
        })

    snapshots = sorted(directory.glob("history-*.md"))
    live = _read_text(directory / "SKILL.md")
    first = _read_text(snapshots[0]) if snapshots else ""
    gate = log.get("gate") or (steps[-1]["gate"] if steps else None) or {}
    rejected = gate.get("action") == "quarantine"

    if rejected and len(snapshots) > 1:
        # The candidate never reached the live file; show what was turned away.
        diff = _unified(live, _read_text(snapshots[-1]), "live SKILL.md", "rejected candidate")
    else:
        diff = _unified(first, live, "v0.1.0", "live SKILL.md")

    # Evolution Cards sit next to the snapshots; the last one records the outcome.
    cards = sorted(p for p in directory.glob("*.md") if p.name != "SKILL.md" and not p.name.startswith("history-"))
    matching = [p for p in cards if ("rejected" in p.name) == rejected]
    final_version = steps[-1]["version"] if steps else None
    versioned = [p for p in matching if final_version and p.stem.endswith(f"-{final_version}")]
    final_card = (versioned or matching or [None])[-1]
    return {
        "id": directory.name,
        "skill": log.get("skill_name") or (steps[0]["candidate"]["name"] if steps else directory.name),
        "outcome": "quarantine" if rejected else "admit",
        "gate": gate,
        "steps": steps,
        "live": live,
        "diff": diff,
        "card": _read_text(final_card) if final_card else "",
    }


@lru_cache(maxsize=1)
def evolution_lineages() -> list[dict[str, Any]]:
    if not EVOLUTION_DEMO_DIR.is_dir():
        return []
    out = []
    for directory in sorted(EVOLUTION_DEMO_DIR.glob("lineage-*")):
        lineage = _lineage(directory)
        if lineage:
            out.append(lineage)
    return out


def live_skills() -> list[dict[str, Any]]:
    """Skills visible to the running agent, with their recorded history."""
    from ..skill_history import list_history
    from ..skills import discover_skills, reset_skill_cache

    reset_skill_cache()  # the pipeline may have written a skill since the last call
    out = []
    for skill in discover_skills():
        history = [
            {k: v for k, v in asdict(snap).items() if k != "content"}
            for snap in list_history(skill.name)
        ]
        out.append({
            "name": skill.name,
            "description": skill.description,
            "when_to_use": skill.when_to_use or "",
            "source": skill.source,
            "context": skill.context,
            "user_invocable": skill.user_invocable,
            "history": history,
        })
    return out


# ─── Performance ────────────────────────────────────────────


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile; ``values`` must be sorted and non-empty."""
    index = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[index]


def summarize_spans(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per span name: count, total, p50 and p95 in milliseconds."""
    buckets: dict[str, list[float]] = {}
    for row in rows:
        if row.get("type") == "span":
            buckets.setdefault(row["data"].get("name", "?"), []).append(float(row["data"].get("dur_ms", 0.0)))
    out = {}
    for name, durations in buckets.items():
        durations.sort()
        out[name] = {
            "count": len(durations),
            "total_ms": round(sum(durations), 2),
            "p50_ms": round(_percentile(durations, 0.50), 2),
            "p95_ms": round(_percentile(durations, 0.95), 2),
            "max_ms": round(durations[-1], 2),
        }
    return out


def perf_summary(directories: list[Path]) -> dict[str, Any]:
    """Where the time goes, over every recorded trace that carries spans."""
    from .recorder import load_trace

    rows: list[dict[str, Any]] = []
    traces = 0
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            events_ = load_trace(path)["events"]
            if any(e["type"] == "span" for e in events_):
                traces += 1
                rows.extend(events_)
    spans = summarize_spans(rows)
    # Time the loop itself accounts for; waiting on a person is listed but not counted.
    accounted = sum(v["total_ms"] for k, v in spans.items() if k != "confirm_wait")
    for value in spans.values():
        value["share"] = round(value["total_ms"] / accounted, 4) if accounted else 0.0
    return {"available": bool(spans), "traces": traces, "spans": spans}


# ─── Case study ─────────────────────────────────────────────


@lru_cache(maxsize=1)
def case_study() -> dict[str, Any]:
    """Results of running Keelson on real issues from real projects."""
    results = _read_json(BENCH_DIR / "casestudy" / "results.json")
    if not results:
        return {"available": False}
    return {"available": True, **results}

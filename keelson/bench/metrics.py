"""Scoring aggregation for paired benchmark arms.

All arms are run on the *same* task subset, so arm-vs-arm comparison is a paired
design. We report a bootstrap CI for the accuracy delta and a McNemar test for
the paired binary outcomes, instead of a bare percentage that hides whether a
few-point gap on ~60 tasks is signal or noise.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class ArmSummary:
    arm: str
    n: int
    correct: int
    accuracy: float
    avg_turns: float = 0.0
    avg_tool_calls: float = 0.0
    invalid_tool_call_rate: float = 0.0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    est_cost_usd: float = 0.0
    wall_time_s: float = 0.0


def _rate(env: str, default: float) -> float:
    import os

    try:
        return float(os.environ.get(env, "") or default)
    except ValueError:
        return default


def summarize_arm(arm: str, results: list[dict]) -> ArmSummary:
    """``results`` = per-task dicts with at least ``correct`` (bool).

    Cost is estimated from token totals at KEELSON_PRICE_IN / KEELSON_PRICE_OUT
    (USD per 1M tokens; defaults are a gemini-flash-tier ballpark). The
    authoritative number is the provider's own usage dashboard.
    """
    n = len(results)
    if n == 0:
        return ArmSummary(arm=arm, n=0, correct=0, accuracy=0.0)

    def mean(key: str) -> float:
        vals = [float(r.get(key, 0) or 0) for r in results]
        return sum(vals) / n

    correct = sum(1 for r in results if r.get("correct"))
    invalid = sum(float(r.get("invalid_tool_calls", 0) or 0) for r in results)
    total_calls = sum(float(r.get("tool_calls", 0) or 0) for r in results)
    return ArmSummary(
        arm=arm,
        n=n,
        correct=correct,
        accuracy=correct / n,
        avg_turns=mean("turns"),
        avg_tool_calls=total_calls / n,
        invalid_tool_call_rate=(invalid / total_calls) if total_calls else 0.0,
        avg_input_tokens=mean("input_tokens"),
        avg_output_tokens=mean("output_tokens"),
        est_cost_usd=round(
            sum(float(r.get("input_tokens", 0) or 0) for r in results) / 1e6 * _rate("KEELSON_PRICE_IN", 0.30)
            + sum(float(r.get("output_tokens", 0) or 0) for r in results) / 1e6 * _rate("KEELSON_PRICE_OUT", 2.50),
            4,
        ),
        wall_time_s=sum(float(r.get("wall_time_s", 0) or 0) for r in results),
    )


def _align(a: list[dict], b: list[dict]) -> tuple[list[int], list[int]]:
    """Return paired 0/1 correctness vectors keyed on task id (intersection, sorted).

    If a task appears more than once (``--repeats`` > 1), the last row wins — use
    :func:`paired_bootstrap_delta_pooled` for repeat-aware comparison.
    """
    am = {r["task_id"]: 1 if r.get("correct") else 0 for r in a}
    bm = {r["task_id"]: 1 if r.get("correct") else 0 for r in b}
    ids = sorted(set(am) & set(bm))
    return [am[i] for i in ids], [bm[i] for i in ids]


def _by_task_mean(rows: list[dict]) -> dict[int, float]:
    """task_id -> mean correctness across its repeats."""
    acc: dict[int, list[int]] = {}
    for r in rows:
        acc.setdefault(r["task_id"], []).append(1 if r.get("correct") else 0)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def pooled_arm_accuracy(rows: list[dict]) -> dict:
    """Accuracy over all rows, plus the per-task-mean spread (repeat noise)."""
    if not rows:
        return {"n_rows": 0, "n_tasks": 0, "accuracy": 0.0}
    per_task = _by_task_mean(rows)
    flat = sorted(per_task.values())
    return {
        "n_rows": len(rows),
        "n_tasks": len(per_task),
        "accuracy": sum(1 for r in rows if r.get("correct")) / len(rows),
        "task_mean_min": flat[0],
        "task_mean_max": flat[-1],
        "tasks_unstable": sum(1 for v in flat if 0.0 < v < 1.0),
    }


def paired_bootstrap_delta_pooled(
    arm_a: list[dict],
    arm_b: list[dict],
    *,
    iters: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """Cluster bootstrap for ``acc(a) - acc(b)`` when each task has >=1 repeat.

    Resamples task ids (not rows); each task contributes its mean correctness, so
    a task run 3x counts once with weight = its hit rate.
    """
    ma, mb = _by_task_mean(arm_a), _by_task_mean(arm_b)
    ids = sorted(set(ma) & set(mb))
    n = len(ids)
    if n == 0:
        return {"n": 0, "delta": 0.0, "ci_low": 0.0, "ci_high": 0.0, "iters": iters}
    xa = [ma[i] for i in ids]
    xb = [mb[i] for i in ids]
    point = (sum(xa) - sum(xb)) / n
    rng = random.Random(seed)
    idx = range(n)
    deltas = []
    for _ in range(iters):
        pick = [rng.choice(idx) for _ in idx]
        deltas.append((sum(xa[i] for i in pick) - sum(xb[i] for i in pick)) / n)
    deltas.sort()
    lo = deltas[int((alpha / 2) * iters)]
    hi = deltas[min(iters - 1, int((1 - alpha / 2) * iters))]
    return {
        "n": n,
        "delta": point,
        "ci_low": lo,
        "ci_high": hi,
        "iters": iters,
        "alpha": alpha,
        "significant": not (lo <= 0.0 <= hi),
    }


def paired_bootstrap_delta(
    arm_a: list[dict],
    arm_b: list[dict],
    *,
    iters: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """Bootstrap CI for ``acc(a) - acc(b)`` over paired tasks."""
    xa, xb = _align(arm_a, arm_b)
    n = len(xa)
    if n == 0:
        return {"n": 0, "delta": 0.0, "ci_low": 0.0, "ci_high": 0.0, "iters": iters}

    point = (sum(xa) - sum(xb)) / n
    rng = random.Random(seed)
    deltas = []
    idx = range(n)
    for _ in range(iters):
        pick = [rng.choice(idx) for _ in idx]
        da = sum(xa[i] for i in pick)
        db = sum(xb[i] for i in pick)
        deltas.append((da - db) / n)
    deltas.sort()
    lo = deltas[int((alpha / 2) * iters)]
    hi = deltas[min(iters - 1, int((1 - alpha / 2) * iters))]
    return {
        "n": n,
        "delta": point,
        "ci_low": lo,
        "ci_high": hi,
        "iters": iters,
        "alpha": alpha,
        "significant": not (lo <= 0.0 <= hi),
    }


def mcnemar(arm_a: list[dict], arm_b: list[dict], *, exact_threshold: int = 25) -> dict:
    """McNemar test on paired binary outcomes.

    ``b`` = a wrong, b right; ``c`` = a right, b wrong. Uses the exact binomial
    when discordant pairs are few, else the continuity-corrected chi-square.
    """
    xa, xb = _align(arm_a, arm_b)
    b = sum(1 for i, j in zip(xa, xb, strict=True) if i == 0 and j == 1)
    c = sum(1 for i, j in zip(xa, xb, strict=True) if i == 1 and j == 0)
    disc = b + c
    if disc == 0:
        return {"b": 0, "c": 0, "statistic": 0.0, "p_value": 1.0, "method": "none"}

    if disc <= exact_threshold:
        k = min(b, c)
        # two-sided exact binomial, p = 0.5
        p = 2.0 * sum(math.comb(disc, i) for i in range(k + 1)) / (2.0 ** disc)
        return {"b": b, "c": c, "statistic": float(k), "p_value": min(1.0, p), "method": "exact"}

    stat = (abs(b - c) - 1) ** 2 / disc
    # survival of chi-square with df=1  ->  erfc(sqrt(stat/2))
    p = math.erfc(math.sqrt(stat / 2.0))
    return {"b": b, "c": c, "statistic": stat, "p_value": p, "method": "chi2_cc"}


def stratify_by_hops(results: list[dict], task_hops: dict[int, int]) -> dict[int, dict]:
    """Accuracy per hop-depth bucket. ``results`` need ``task_id`` + ``correct``."""
    buckets: dict[int, list[int]] = {}
    for r in results:
        h = task_hops.get(r["task_id"])
        if h is None:
            continue
        buckets.setdefault(h, []).append(1 if r.get("correct") else 0)
    return {
        h: {"n": len(v), "correct": sum(v), "accuracy": (sum(v) / len(v)) if v else 0.0}
        for h, v in sorted(buckets.items())
    }

"""Paired-comparison statistics."""

from __future__ import annotations

from agents.bench.metrics import (
    mcnemar,
    paired_bootstrap_delta,
    paired_bootstrap_delta_pooled,
    pooled_arm_accuracy,
    stratify_by_hops,
    summarize_arm,
)


def _arm(pattern: list[int]):
    return [{"task_id": i, "correct": bool(v)} for i, v in enumerate(pattern)]


def test_summarize_basic():
    res = [
        {"task_id": 0, "correct": True, "turns": 4, "tool_calls": 3, "invalid_tool_calls": 0},
        {"task_id": 1, "correct": False, "turns": 6, "tool_calls": 5, "invalid_tool_calls": 1},
    ]
    s = summarize_arm("x", res)
    assert s.n == 2 and s.correct == 1 and s.accuracy == 0.5
    assert s.avg_turns == 5.0
    assert s.avg_tool_calls == 4.0
    assert abs(s.invalid_tool_call_rate - 1 / 8) < 1e-9


def test_bootstrap_identical_arms_delta_zero():
    a = _arm([1, 0, 1, 0] * 10)
    d = paired_bootstrap_delta(a, [dict(x) for x in a], iters=1000, seed=1)
    assert d["delta"] == 0.0
    assert d["ci_low"] == 0.0 == d["ci_high"]
    assert d["significant"] is False


def test_bootstrap_detects_real_gap_and_is_deterministic():
    a = _arm([1] * 40)
    b = _arm([1] * 30 + [0] * 10)     # a beats b on 10/40
    d1 = paired_bootstrap_delta(a, b, iters=3000, seed=7)
    d2 = paired_bootstrap_delta(a, b, iters=3000, seed=7)
    assert d1 == d2                      # seeded -> reproducible
    assert abs(d1["delta"] - 0.25) < 1e-9
    assert d1["ci_low"] > 0 and d1["significant"] is True


def test_mcnemar_exact_small_samples():
    a = _arm([1] * 20)
    b = _arm([1] * 15 + [0] * 5)         # b=0, c=5 discordant
    m = mcnemar(a, b)
    assert m["method"] == "exact"
    assert (m["b"], m["c"]) == (0, 5)
    assert m["p_value"] < 0.07           # 2 * 0.5**5 = 0.0625


def test_mcnemar_no_discordant_pairs():
    a = _arm([1, 0, 1, 0])
    m = mcnemar(a, [dict(x) for x in a])
    assert m["p_value"] == 1.0


def test_mcnemar_chi2_for_many_discordant():
    # 60 discordant, lopsided 45/15 -> chi2 with continuity correction, p small
    a = _arm([1] * 45 + [0] * 15 + [1] * 40)
    b = _arm([0] * 45 + [1] * 15 + [1] * 40)
    m = mcnemar(a, b)
    assert m["method"] == "chi2_cc"
    assert m["p_value"] < 0.001


def test_stratify_by_hops():
    res = _arm([1, 1, 0, 0, 1])
    hops = {0: 3, 1: 3, 2: 3, 3: 4, 4: 4}
    strat = stratify_by_hops(res, hops)
    assert strat[3] == {"n": 3, "correct": 2, "accuracy": 2 / 3}
    assert strat[4] == {"n": 2, "correct": 1, "accuracy": 0.5}


def test_align_uses_task_id_intersection():
    a = [{"task_id": 1, "correct": True}, {"task_id": 2, "correct": False}]
    b = [{"task_id": 2, "correct": True}, {"task_id": 3, "correct": True}]
    d = paired_bootstrap_delta(a, b, iters=200)
    assert d["n"] == 1     # only task_id 2 is shared


# --- repeat-aware (pooled) comparison, Day 1 diagnostics ---------------------

def _rep_arm(rows):
    """rows = [(task_id, correct), ...] — may repeat a task_id."""
    return [{"task_id": t, "correct": bool(c)} for t, c in rows]


def test_pooled_accuracy_flags_unstable_tasks():
    rows = _rep_arm([(1, 1), (1, 1), (1, 0),   # task 1: 2/3
                     (2, 1), (2, 1), (2, 1),   # task 2: 3/3
                     (3, 0), (3, 0), (3, 0)])  # task 3: 0/3
    p = pooled_arm_accuracy(rows)
    assert p["n_rows"] == 9 and p["n_tasks"] == 3
    assert p["accuracy"] == 5 / 9      # 2 + 3 + 0 correct rows
    assert p["tasks_unstable"] == 1          # only task 1 is between 0 and 1
    assert p["task_mean_min"] == 0.0 and p["task_mean_max"] == 1.0


def test_pooled_bootstrap_resamples_tasks_not_rows():
    # arm A perfect, arm B always wrong, 3 repeats each on 5 tasks
    a = _rep_arm([(t, 1) for t in range(5) for _ in range(3)])
    b = _rep_arm([(t, 0) for t in range(5) for _ in range(3)])
    d = paired_bootstrap_delta_pooled(a, b, iters=500, seed=0)
    assert d["n"] == 5                        # 5 tasks, not 15 rows
    assert d["delta"] == 1.0
    assert d["ci_low"] > 0 and d["significant"] is True


def test_pooled_bootstrap_partial_task_hit_rates():
    a = _rep_arm([(t, 1) for t in range(4) for _ in range(2)])          # 1.0 each
    b = _rep_arm([(t, i % 2) for t in range(4) for i in range(2)])      # 0.5 each
    d = paired_bootstrap_delta_pooled(a, b, iters=500, seed=1)
    assert abs(d["delta"] - 0.5) < 1e-9

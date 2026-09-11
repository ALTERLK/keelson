"""Deterministic, disjoint, hop-balanced train/test splitting."""

from __future__ import annotations

from collections import Counter

from agents.bench.splits import build_split, load_split, save_split


def test_split_is_deterministic_disjoint_and_sized(toolhop_tasks):
    s1 = build_split(toolhop_tasks, n_train=40, n_test=40)
    s2 = build_split(toolhop_tasks, n_train=40, n_test=40)
    assert s1 == s2
    assert s1["n_train"] == 40 and s1["n_test"] == 40
    assert set(s1["train"]).isdisjoint(s1["test"])


def test_salt_changes_the_split(toolhop_tasks):
    a = build_split(toolhop_tasks, n_train=30, n_test=30, salt="A")
    b = build_split(toolhop_tasks, n_train=30, n_test=30, salt="B")
    assert a["train"] != b["train"]


def test_hop_distribution_matches_between_train_and_test(toolhop_tasks):
    hop = {t.id: t.hops for t in toolhop_tasks}
    s = build_split(toolhop_tasks, n_train=60, n_test=60)
    tr = Counter(hop[i] for i in s["train"])
    te = Counter(hop[i] for i in s["test"])
    for h in set(tr) | set(te):
        assert abs(tr[h] - te[h]) <= 3   # near-identical difficulty mix


def test_save_load_round_trip(toolhop_tasks, tmp_path):
    s = build_split(toolhop_tasks, n_train=20, n_test=20)
    p = tmp_path / "splits.json"
    save_split(s, p)
    assert load_split(p) == s

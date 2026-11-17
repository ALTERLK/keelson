"""Deterministic train/test splitting for benchmark tasks.

The split is a pure function of ``(salt, task_id)`` — stable across runs and
machines, independent of dataset order. It filters to safe, executable tasks and
splits *within* each hop-depth bucket so train and test have the same difficulty
mix.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from keelson.bench.toolhop import ToolHopTask, build_callables

_DEFAULT_SALT = "keelson-toolhop-v1"


def _coin(task_id: int, salt: str, channel: str) -> float:
    h = hashlib.sha1(f"{salt}:{channel}:{task_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


_runnable_cache: dict[int, bool] = {}


def is_runnable(task: ToolHopTask) -> bool:
    """Safe *and* its tool bodies import + define cleanly on this machine."""
    if task.id in _runnable_cache:
        return _runnable_cache[task.id]
    ok = False
    if task.is_safe:
        try:
            fns = build_callables(task)
            ok = {s["name"] for s in task.tool_specs}.issubset(set(fns))
        except Exception:
            ok = False
    _runnable_cache[task.id] = ok
    return ok


def build_split(
    tasks: list[ToolHopTask],
    *,
    n_train: int = 60,
    n_test: int = 60,
    salt: str = _DEFAULT_SALT,
    balance_hops: bool = True,
) -> dict:
    """Return ``{"salt","n_train","n_test","train":[ids],"test":[ids]}`` — disjoint."""
    pool = [t for t in tasks if is_runnable(t)]
    need = n_train + n_test

    if balance_hops:
        by_hop: dict[int, list[ToolHopTask]] = defaultdict(list)
        for t in pool:
            by_hop[t.hops].append(t)
        hops = sorted(by_hop)
        total = len(pool)
        selected: list[ToolHopTask] = []
        for h in hops:
            bucket = sorted(by_hop[h], key=lambda t: _coin(t.id, salt, "select"))
            quota = max(1, round(need * len(by_hop[h]) / total))
            selected.extend(bucket[:quota])
        selected.sort(key=lambda t: _coin(t.id, salt, "select"))
        selected = selected[:need]
    else:
        selected = sorted(pool, key=lambda t: _coin(t.id, salt, "select"))[:need]

    # Assign to train/test within each hop bucket, ratio n_train : n_test.
    ratio = n_train / need
    train_ids: list[int] = []
    test_ids: list[int] = []
    per_hop: dict[int, list[ToolHopTask]] = defaultdict(list)
    for t in selected:
        per_hop[t.hops].append(t)
    for h in sorted(per_hop):
        b = sorted(per_hop[h], key=lambda t: _coin(t.id, salt, "assign"))
        cut = round(len(b) * ratio)
        train_ids.extend(t.id for t in b[:cut])
        test_ids.extend(t.id for t in b[cut:])

    # Trim/pad to exact sizes deterministically if rounding drifted.
    train_ids.sort(key=lambda i: _coin(i, salt, "assign"))
    test_ids.sort(key=lambda i: _coin(i, salt, "assign"))
    while len(train_ids) > n_train:
        test_ids.append(train_ids.pop())
    while len(test_ids) > n_test:
        train_ids.append(test_ids.pop())

    return {
        "salt": salt,
        "n_train": len(train_ids),
        "n_test": len(test_ids),
        "train": sorted(train_ids),
        "test": sorted(test_ids),
    }


def save_split(split: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_split(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

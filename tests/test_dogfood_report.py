"""scripts/dogfood_report.py: the table in docs/dogfooding.md and the path-scrubbing ingest.

Every test points the script's module-level paths at a temporary directory and builds its own records; nothing
reads the developer's bench/dogfood or docs.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("dogfood_report", ROOT / "scripts" / "dogfood_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dogfood = _load()


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    records = root / "bench" / "dogfood"
    records.mkdir(parents=True)
    doc = root / "docs" / "dogfooding.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(f"# Dogfooding\n\nintro\n\n{dogfood.BEGIN}\n{dogfood.END}\n\nafter\n", encoding="utf-8")
    monkeypatch.setattr(dogfood, "ROOT", root)
    monkeypatch.setattr(dogfood, "RECORDS", records)
    monkeypatch.setattr(dogfood, "DOC", doc)
    return root


def _record(n: int, *, cost: float, input_tokens: int, cache_hit_tokens: int, seconds: int = 10,
            status: str = "passed", weakened: bool = False, permissions: dict | None = None,
            events_logged: bool = True, outcome: str = "kept", note: str = "", request: str = "do it") -> dict:
    return {"n": n, "request": request, "model": "m", "seconds": seconds,
            "verify": {"status": status, "detail": "", "weakened": weakened, "unproven": [], "out_of_scope": [],
                       "changed": []},
            "usage": {"input_tokens": input_tokens, "cache_hit_tokens": cache_hit_tokens, "cost_usd": cost},
            "error": None, "events_logged": events_logged, "permissions": permissions or {},
            "undo_events": 0, "scope_events": 0, "folds": 0, "outcome": outcome, "note": note, "answer_tail": ""}


def _write(paths: Path, record: dict) -> None:
    (paths / "bench" / "dogfood" / f"{record['n']:02d}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _table(paths: Path) -> str:
    assert dogfood.main([]) == 0
    text = (paths / "docs" / "dogfooding.md").read_text(encoding="utf-8")
    return text[text.index(dogfood.BEGIN):text.index(dogfood.END) + len(dogfood.END)]


def test_table_row_follows_the_record_and_totals_add_up(paths):
    _write(paths, _record(1, cost=0.1234, input_tokens=1000, cache_hit_tokens=750, seconds=42,
                          permissions={"deny": 2, "confirm": 1, "allow": 5}))
    _write(paths, _record(2, cost=0.25, input_tokens=1000, cache_hit_tokens=250, seconds=7, status="failed",
                          weakened=True, permissions={"allow": 3}, outcome="kept-edited", note="改了两行"))
    table = _table(paths)

    assert ("| [1](../bench/dogfood/01.json) | do it | passed | $0.1234 | 75% | 42 秒 | 2 / 1 | 原样留下 |"
            in table)
    assert ("| [2](../bench/dogfood/02.json) | do it | failed（标记：测试被削弱） | $0.2500 | 25% | 7 秒 | "
            "0 / 0 | 改过再留 |" in table)
    # 2 records, kept = both start with "kept" (incl. kept-edited), 1 edited by hand.
    assert "共 2 个任务，留下 2 个（其中 1 个我改过再留）；合计 $0.373；输入 token 的缓存命中率 50%。" in table
    assert "- **2.** 改了两行" in table


def test_uncounted_events_and_unknown_outcomes_are_said_as_such(paths):
    _write(paths, _record(1, cost=0.0, input_tokens=0, cache_hit_tokens=0, events_logged=False,
                          permissions={"deny": 9}, outcome="dropped"))
    _write(paths, _record(2, cost=0.0, input_tokens=0, cache_hit_tokens=0, status=None, outcome="weird"))
    table = _table(paths)
    # Deny counts are ignored without an events log, and no tokens means no ratio printed.
    assert "| 未记录 | 丢掉 |" in table
    assert "| $0.0000 | – |" in table
    assert "| – |" in table              # status None → "–"
    assert "| weird |" in table           # an outcome outside OUTCOMES is printed as-is
    assert "留下 0 个（其中 0 个我改过再留）；合计 $0.000；输入 token 的缓存命中率 0%。" in table


def test_ingest_scrubs_the_local_home_paths(tmp_path, paths):
    raw = tmp_path / "raw"
    raw.mkdir()
    answer = {
        "model": "m", "result": "Edited C:\\Users\\alice\\proj\\a.py and /home/bob/proj/b.py",
        "verify": {"status": "passed", "weakened": [], "unproven": [], "out_of_scope": []},
        "usage": {"input_tokens": 100, "cache_hit_tokens": 50, "cost_usd": 0.01},
    }
    (raw / "3.json").write_text(json.dumps(answer), encoding="utf-8")
    (raw / "3.meta").write_text("model=m\nseconds=37\n", encoding="utf-8")
    events = [
        {"type": "permission", "data": {"action": "deny"}},
        {"type": "permission", "data": {"action": "deny", "speculative": True}},
        {"type": "permission", "data": {"action": "allow"}},
        {"type": "undo", "data": {}},
        {"type": "undo", "data": {}},
        {"type": "scope", "data": {"paths": []}},
        {"type": "context_fold", "data": {}},
    ]
    (raw / "3.events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (raw / "3.diff").write_text("--- a/C:\\Users\\alice\\proj\\a.py\n+++ b/C:\\Users\\alice\\proj\\a.py\n",
                                encoding="utf-8")

    out = dogfood.ingest(3, raw, "fix it", "kept", "fine")

    text = (paths / "bench" / "dogfood" / "03.json").read_text(encoding="utf-8")
    record = json.loads(text)
    assert out == paths / "bench" / "dogfood" / "03.json"
    assert "C:\\Users\\alice" not in text and "/home/bob" not in text
    assert "alice" not in record["answer_tail"] and "bob" not in record["answer_tail"]
    assert "a.py" in record["answer_tail"] and "b.py" in record["answer_tail"]
    assert record["seconds"] == 37
    assert record["permissions"] == {"deny": 1, "allow": 1}       # the speculative ask is not a decision
    assert record["undo_events"] == 2 and record["scope_events"] == 1 and record["folds"] == 1
    assert record["events_logged"] is True
    assert record["outcome"] == "kept" and record["request"] == "fix it" and record["note"] == "fine"
    assert set(record["verify"]) == {"status", "detail", "weakened", "unproven", "out_of_scope", "changed"}

    diff = (paths / "bench" / "dogfood" / "03.diff").read_text(encoding="utf-8")
    assert "C:\\Users\\alice" not in diff
    assert diff.count("~\\proj\\a.py") == 2


def test_ingest_without_an_events_log_records_nothing_and_writes_no_diff(tmp_path, paths):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "5.json").write_text(json.dumps({"result": "ok", "usage": {}}), encoding="utf-8")
    (raw / "5.meta").write_text("seconds=3\n", encoding="utf-8")

    dogfood.ingest(5, raw, "", "dropped", "")

    record = json.loads((paths / "bench" / "dogfood" / "05.json").read_text(encoding="utf-8"))
    assert record["events_logged"] is False
    assert record["permissions"] == {} and record["undo_events"] == 0
    assert not (paths / "bench" / "dogfood" / "05.diff").exists()


def test_redact_handles_both_path_styles_and_leaves_the_rest():
    text = "a C:\\Users\\alice\\x.py b D:/Users/bob/y c /home/carol/z d /Users/dave/w e /usr/bin/python"
    scrubbed = dogfood._redact(text)
    assert "alice" not in scrubbed and "bob" not in scrubbed and "carol" not in scrubbed and "dave" not in scrubbed
    assert "/usr/bin/python" in scrubbed          # only home folders are replaced
    assert scrubbed.startswith("a ~\\x.py b ~/y c ~/z d ~/w e ")


def test_doc_without_markers_is_an_error(paths):
    (paths / "docs" / "dogfooding.md").write_text("# plain\n", encoding="utf-8")
    assert dogfood.main([]) == 2


def test_rewrite_keeps_the_text_around_the_block(paths):
    _write(paths, _record(1, cost=0.5, input_tokens=10, cache_hit_tokens=5))
    table = _table(paths)
    text = (paths / "docs" / "dogfooding.md").read_text(encoding="utf-8")
    assert text.startswith("# Dogfooding\n\nintro\n\n<!-- dogfood:begin")
    assert text.endswith("<!-- dogfood:end -->\n\nafter\n")
    assert table in text

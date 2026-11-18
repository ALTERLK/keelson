"""scripts/features_report.py: the tables it generates from raw feature-experiment results.

Each test writes throwaway run folders (results.jsonl + config.json) into a temporary bench/, points the script's
module-level paths at them, and checks the numbers in the generated block and the --check verdict.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("features_report", ROOT / "scripts" / "features_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load()


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty bench/ with a doc that has the generated-block markers; the script's paths point into it."""
    root = tmp_path / "repo"
    runs = root / "bench" / "runs"
    runs.mkdir(parents=True)
    doc = root / "bench" / "FEATURES.md"
    doc.write_text(f"# Feature experiments\n\nintro\n\n{report.BEGIN}\n{report.END}\n\ntail\n", encoding="utf-8")
    monkeypatch.setattr(report, "ROOT", root)
    monkeypatch.setattr(report, "RUNS", runs)
    monkeypatch.setattr(report, "REPORT", doc)
    monkeypatch.setattr(report, "MANIFEST", root / "bench" / "features_runs.json")
    return root


def _run(bench: Path, name: str, rows: list[dict], config: dict | None = None) -> Path:
    folder = bench / "bench" / "runs" / name
    folder.mkdir(parents=True)
    (folder / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    if config is not None:
        (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return folder


def _verify(task: str, arm: str, *, kind: str = "spec", tests_pass: bool, check_pass: bool = True,
            suite_pass: bool = True, false_success: bool = False, outcome: str = "passed",
            model_test_runs: bool = False, cost: float = 0.0, input_tokens: int = 100,
            cache_hit_tokens: int = 50) -> dict:
    return {"experiment": "verify", "task_id": task, "kind": kind, "arm": arm, "tests_pass": tests_pass,
            "check_pass": check_pass, "suite_pass": suite_pass, "false_success": false_success,
            "outcome": outcome, "model_test_runs": model_test_runs, "cost_usd": cost,
            "input_tokens": input_tokens, "cache_hit_tokens": cache_hit_tokens}


def _block(bench: Path) -> str:
    assert report.main([]) == 0
    text = (bench / "bench" / "FEATURES.md").read_text(encoding="utf-8")
    return text[text.index(report.BEGIN):text.index(report.END) + len(report.END)]


def test_verify_table_counts_each_arm_and_keeps_the_doc_around_the_block(bench):
    _run(bench, "20260101T000000Z-features-verify-m",
         [_verify("t1", "on", tests_pass=True, model_test_runs=True, cost=0.5),
          _verify("t1", "off", tests_pass=False, false_success=True, outcome="failed"),
          _verify("t2", "on", tests_pass=True, check_pass=True, suite_pass=False, outcome="unverified", cost=0.25),
          _verify("t2", "off", tests_pass=True)],
         {"model": "m", "repeats": 2, "git_commit": "abc123"})
    block = _block(bench)

    assert "### Verified completion" in block
    # on: 2/2 tests pass, 1 request done with another test broken, 0 false successes, model ran tests in 1/2.
    assert "| verification on | 2 | 2/2 (100%) | 1 | 0/2 (0%) | 0/0 | 1/2 (50%) | passed 1 / failed 0 / unverified 1 |" in block
    # off: 1/2 pass, 1 false success, and of the one failure 0 admitted it (off is sorted first).
    assert "| verification off | 2 | 1/2 (50%) | 0 | 1/2 (50%) | 0/1 | 0/2 (0%) | – |" in block
    assert "| t1 | 0/1 (0%) | 1/1 (100%) |" in block      # per task, columns are off then on
    assert "model `m`, 2 repeats" in block and "commit `abc123`" in block

    text = (bench / "bench" / "FEATURES.md").read_text(encoding="utf-8")
    assert text.startswith("# Feature experiments\n\nintro\n\n<!-- features:begin")
    assert text.endswith("<!-- features:end -->\n\ntail\n")


def test_ungraded_rows_count_in_runs_but_stay_out_of_the_rates(bench):
    # An exception before grading leaves only an error: no tests_pass, no verdict.
    _run(bench, "20260101T000000Z-features-verify-m",
         [_verify("t1", "on", tests_pass=True),
          {"experiment": "verify", "task_id": "t1", "arm": "on", "error": "boom", "cost_usd": 0.2}],
         {"model": "m"})
    block = _block(bench)
    # runs counts both rows, the rate only the graded one; money sums the arm's rows, the errored one included.
    assert "| verification on | 2 | 1/1 (100%) | 0 | 0/1 (0%) | 0/0 | 0/1 (0%) | passed 1 / failed 0 / unverified 0 |" \
           in block
    assert " $0.200 |" in block


def test_scope_ledger_undo_and_danger_tables(bench):
    _run(bench, "20260101T000000Z-features-scope-m",
         [{"experiment": "scope", "task_id": "s1", "arm": "on", "check_pass": True,
           "out_of_scope_changed": [], "out_of_scope_asks_denied": ["x"], "new_files": []},
          {"experiment": "scope", "task_id": "s1", "arm": "off", "check_pass": True,
           "out_of_scope_changed": ["helper.py"], "out_of_scope_asks_denied": [], "new_files": ["new.py"]}],
         {"model": "m"})
    _run(bench, "20260101T000000Z-features-ledger-m",
         [{"experiment": "ledger", "task_id": "l1", "arm": "on", "rules_kept": 3, "rules_total": 3, "task_done": True,
           "rules": {"a": True, "b": True, "c": True}, "ledger_constraints": ["Never touch legacy_api.py"]},
          {"experiment": "ledger", "task_id": "l1", "arm": "off", "rules_kept": 1, "rules_total": 3,
           "task_done": False, "rules": {"a": True, "b": False, "c": False}, "ledger_constraints": []}],
         {"model": "m"})
    _run(bench, "20260101T000000Z-features-undo-m",
         [{"experiment": "undo", "task_id": "u1", "arm": "on", "checkpoint_mode": "git", "shell_calls": 2,
           "delete_calls": 1, "files_changed": 4, "identical_after_undo": True, "edit_tools_only_would_restore": 3,
           "undo_ok": True, "cost_usd": 0.01}],
         {"model": "m"})
    _run(bench, "20260101T000000Z-features-danger-no-model",
         [{"command": "rm -rf /", "kind": "catastrophic", "default": "deny", "acceptEdits": "deny",
           "dontAsk": "deny", "bypassPermissions": "deny"},
          {"command": "python -c \"import shutil; shutil.rmtree('~')\"", "kind": "catastrophic", "default": "confirm",
           "acceptEdits": "confirm", "dontAsk": "confirm", "bypassPermissions": "allow"},
          {"command": "ls", "kind": "everyday", "default": "allow", "acceptEdits": "allow", "dontAsk": "allow",
           "bypassPermissions": "allow"}])

    block = _block(bench)

    assert "| scope guard off | 1 | 1/1 (100%) | 1/1 (100%) | 1 | 0 | 1/1 (100%) |" in block
    assert "Other files changed: s1: helper.py (off)" in block

    assert "| ledger on | 1 | 3/3 (100%) | 1/1 (100%) | 1/1 (100%) |" in block
    assert "| ledger off | 1 | 1/3 (33%) | 0/1 (0%) | 0/1 (0%) |" in block
    # Per rule, a cell is how often the rule survived; off keeps only "a", on keeps all three.
    assert "| a | 1/1 (100%) | 1/1 (100%) |" in block
    assert "| b | 0/1 (0%) | 1/1 (100%) |" in block
    assert "- Never touch legacy_api.py" in block

    # No token counts in the undo rows, so the cache column is "–".
    assert "| 1 | 1 | 2 | 1 | 4 | 1/1 (100%) | 3/4 (75%) | $0.010 | – |" in block

    # Everyday ls is allowed everywhere and is not a slip; the -c interpreter command is.
    assert "| everyday | 1 | allow 1 | allow 1 | allow 1 | allow 1 |" in block
    assert "| catastrophic | 2 | deny 1 / confirm 1 | deny 1 / confirm 1 | deny 1 / confirm 1 | deny 1 / allow 1 |" in block
    assert "not refused outright" in block
    assert "`python -c \"import shutil; shutil.rmtree('~')\"` (default: confirm)" in block
    assert "- `rm -rf /`" not in block


def test_danger_with_no_slips_says_none(bench):
    _run(bench, "20260101T000000Z-features-danger-no-model",
         [{"command": "rm -rf /", "kind": "catastrophic", "default": "deny", "acceptEdits": "deny",
           "dontAsk": "deny", "bypassPermissions": "deny"}], {"git_commit": "deadbeef"})
    block = _block(bench)
    tail = block.split("run in bypassPermissions):\n\n")[1].split("\n")[0]
    assert tail.strip() == "- none"
    assert "no model" in block and "commit `deadbeef`" in block


def test_check_reports_a_missing_block(bench):
    (bench / "bench" / "FEATURES.md").write_text("# no markers\n", encoding="utf-8")
    assert report.main(["--check"]) == 2


def test_check_passes_when_current_and_fails_when_stale(bench, capsys):
    _run(bench, "20260101T000000Z-features-verify-m", [_verify("t1", "on", tests_pass=True)], {"model": "m"})
    assert report.main([]) == 0
    capsys.readouterr()
    assert report.main(["--check"]) == 0
    assert capsys.readouterr().out == ""          # --check prints nothing

    # A newer run for the same experiment makes the committed block stale.
    newer = _run(bench, "20260102T000000Z-features-verify-m",
                 [_verify("t1", "on", tests_pass=False, false_success=True)], {"model": "m"})
    assert newer.name > "20260101T000000Z-features-verify-m"
    assert report.main(["--check"]) == 1


def test_newest_run_wins_unless_the_manifest_names_one(bench):
    old = _run(bench, "20260101T000000Z-features-verify-m", [_verify("t1", "on", tests_pass=False)], {"model": "old"})
    _run(bench, "20260102T000000Z-features-verify-m", [_verify("t1", "on", tests_pass=True)], {"model": "new"})

    assert "model `new`" in _block(bench)

    (bench / "bench" / "features_runs.json").write_text(json.dumps({"verify": [old.name]}), encoding="utf-8")
    assert "model `old`" in _block(bench)


def test_manifest_pools_several_runs_of_one_experiment(bench):
    one = _run(bench, "20260101T000000Z-features-verify-m", [_verify("t1", "on", tests_pass=True)], {"model": "m"})
    two = _run(bench, "20260102T000000Z-features-verify-m", [_verify("t1", "on", tests_pass=False)], {"model": "m"})
    (bench / "bench" / "features_runs.json").write_text(json.dumps({"verify": [one.name, two.name]}),
                                                       encoding="utf-8")
    block = _block(bench)
    assert "| verification on | 2 | 1/2 (50%) |" in block
    assert f"`bench/runs/{one.name}/results.jsonl`" in block and f"`bench/runs/{two.name}/results.jsonl`" in block


def test_an_experiment_with_no_runs_is_left_out(bench):
    _run(bench, "20260101T000000Z-features-verify-m", [_verify("t1", "on", tests_pass=True)], {"model": "m"})
    (bench / "bench" / "runs" / "20260101T000000Z-features-undo-m").mkdir()   # no results.jsonl yet
    block = _block(bench)
    assert "### Verified completion" in block
    assert "### Undo after shell changes" not in block


def test_pct_is_a_dash_when_nothing_was_graded():
    assert report.pct(0, 0) == "–"
    assert report.pct(1, 4) == "1/4 (25%)"

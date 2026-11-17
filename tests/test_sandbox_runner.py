"""Sandbox driver plumbing that does not need a model."""

from __future__ import annotations

import json

from keelson.bench import sandbox_runner as sr


def test_sandbox_tool_defs_are_the_seven_named_tools():
    defs = sr._sandbox_tool_defs()
    assert [d["name"] for d in defs] == list(sr.SANDBOX_TOOL_NAMES)
    # excludes the sub-agent tool (round 2: construction drops api_key/base_url)
    assert "agent" not in [d["name"] for d in defs]
    assert "skill" not in [d["name"] for d in defs]


def test_raw_result_shape():
    r = sr._raw_result(turns=5, max_turns=20, tin=100, tout=50, final_text="ANSWER: done")
    assert r == {
        "turns": 5, "hit_cap": False, "loop_guard_hits": 0,
        "input_tokens": 100, "output_tokens": 50, "final_text": "ANSWER: done",
    }


def test_raw_result_hit_cap_when_turns_reach_max():
    r = sr._raw_result(turns=20, max_turns=20, tin=0, tout=0, final_text="")
    assert r["hit_cap"] is True


def test_make_run_dir_names_by_arm_and_model(tmp_path):
    cfg = sr.SandboxRunConfig(arm="harness", model="gpt-4o/mini", api_base=None, use_openai=True)
    d = sr.make_run_dir(cfg, tmp_path)
    assert d.is_dir()
    assert "sandbox-harness-gpt-4o_mini" in d.name   # "/" sanitised out of the model name


def test_write_artifacts_and_summary(tmp_path):
    cfg = sr.SandboxRunConfig(arm="raw", model="m", api_base=None, use_openai=True)
    results = [
        {"task_id": "a", "kind": "bugfix", "tests_pass": True, "hit_cap": False, "loop_guard_hits": 0, "turns": 3},
        {"task_id": "b", "kind": "feature", "tests_pass": False, "hit_cap": True, "loop_guard_hits": 2, "turns": 20,
         "error": "timeout after 120s"},
    ]
    run_dir = sr.write_artifacts(results, cfg, tmp_path)
    rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["grader"]["solve_rate"] == 0.5
    assert summary["hit_cap_rate"] == 0.5
    assert summary["loop_guard_hits"] == 2
    assert len(summary["errors"]) == 1


def test_validate_cli_all_tasks_ok(capsys):
    rc = sr.main(["--validate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "all tasks OK" in out


def test_missing_arm_without_validate_errors(capsys, monkeypatch):
    rc = sr.main([])
    assert rc == 2
    assert "--arm is required" in capsys.readouterr().out


def test_unknown_task_id_errors(monkeypatch, capsys):
    monkeypatch.setattr(sr, "_resolve_api", lambda: ("https://x/v1", "k", True))
    rc = sr.main(["--arm", "raw", "--task-ids", "does-not-exist"])
    assert rc == 2
    assert "unknown task ids" in capsys.readouterr().out


def test_no_api_key_errors(monkeypatch, capsys):
    monkeypatch.setattr(sr, "_resolve_api", lambda: (None, None, True))
    rc = sr.main(["--arm", "raw"])
    assert rc == 2
    assert "no API key" in capsys.readouterr().out

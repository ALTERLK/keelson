"""Runner plumbing that does not need a model: answer extraction, tool counting,
config building, dry-run."""

from __future__ import annotations

import json

import pytest

from keelson.bench import runner
from keelson.bench.toolhop import ToolHopTask


@pytest.mark.parametrize("text,expected", [
    ("thinking...\nANSWER: 656", "656"),
    ("ANSWER:   Thomas  ", "Thomas"),
    ("no marker here\njust 42", "just 42"),  # no marker -> whole last line
    ("", None),
    ("   \n  \n", None),
])
def test_extract_answer(text, expected):
    assert runner._extract_answer(text) == expected


def test_counting_tool_runner_tallies():
    task = ToolHopTask(
        id=1, question="q", answer="3", answer_type="number", sub_task={"a": "b"},
        tool_specs=[{"name": "adder", "description": "", "parameters": {"type": "object", "properties": {}}}],
        function_src=["def adder(a, b):\n    return a + b\n"],
    )
    tr = runner._CountingToolRunner(task)
    assert tr("adder", {"a": 1, "b": 2}) == "3"
    assert tr("nope", {}).startswith("Error:")
    assert (tr.calls, tr.invalid) == (2, 1)


def test_build_config_and_dry_run(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path.parents[0] if False else tmp_path)  # cwd irrelevant, dataset path is absolute below
    ds = _real_dataset_path()
    if ds is None:
        pytest.skip("ToolHop dataset not present")
    split_file = tmp_path / "splits.json"
    rc = runner.main([
        "--arm", "raw_api", "--split", "test", "--n", "8",
        "--dataset", str(ds), "--split-file", str(split_file), "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"arm": "raw_api"' in out
    assert "0 fail tool construction" in out
    saved = json.loads(split_file.read_text(encoding="utf-8"))
    assert saved["n_train"] == 8 and saved["n_test"] == 8
    assert set(saved["train"]).isdisjoint(saved["test"])


def test_run_without_key_errors(tmp_path, capsys, monkeypatch):
    ds = _real_dataset_path()
    if ds is None:
        pytest.skip("ToolHop dataset not present")
    # main() calls load_dotenv(); neutralise it so this really is a no-key run
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("dotenv.find_dotenv", lambda *a, **k: "")
    for var in ("APIKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "API", "KEELSON_API_KEY",
                "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    rc = runner.main([
        "--arm", "no_skill", "--n", "2", "--dataset", str(ds),
        "--split-file", str(tmp_path / "s.json"),
    ])
    assert rc == 2
    assert "no API key" in capsys.readouterr().out


def _real_dataset_path():
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "data" / "ToolHop" / "ToolHop.json"
    return p if p.exists() else None


def test_build_config_task_ids_override(monkeypatch):
    ds = _real_dataset_path()
    if ds is None:
        pytest.skip("ToolHop dataset not present")
    monkeypatch.setattr(runner, "_resolve_api", lambda: ("https://x/v1", "k", True))
    args = runner.argparse.Namespace(
        dataset=str(ds), arm="no_skill", split="test", split_file="", n=60,
        salt="s", model="m", concurrency=4, max_turns=20, seed=0,
        task_ids="314, 392 ,453", repeats=3, variant="no_sysprompt",
        save_transcripts=True, dump_payloads=False,
    )
    cfg = runner.build_config(args)
    assert cfg.task_ids == [314, 392, 453]
    assert cfg.split_name == "task-ids(3)"
    assert cfg.repeats == 3 and cfg.variant == "no_sysprompt" and cfg.save_transcripts is True
    assert "run_dir" not in cfg.public()          # self-referential, kept out of the config json


def test_run_arm_expands_repeats(monkeypatch, tmp_path):
    calls = []

    async def fake_harness(task, cfg, api_key, rep=0):
        calls.append((task.id, rep))
        return {"task_id": task.id, "repeat": rep, "correct": True, "hops": 2,
                "hit_cap": False, "model_calls": 3}

    monkeypatch.setattr(runner, "_run_harness_task", fake_harness)
    monkeypatch.setattr(runner, "_setup_arm_env", lambda cfg: ("h", "w"))
    monkeypatch.setattr(runner, "_teardown_arm_env", lambda h, w: None)

    def mk(i):
        return ToolHopTask(id=i, question="q", answer="1", answer_type="number",
                           sub_task={"a": "b"}, tool_specs=[], function_src=[])
    cfg = runner.RunConfig(
        dataset_path="d", arm="no_skill", split_name="x", split_path="x",
        task_ids=[7, 8], model="m", api_base=None, use_openai=True, repeats=3,
    )
    import asyncio
    res = asyncio.run(runner.run_arm({7: mk(7), 8: mk(8)}, cfg, "key"))
    assert len(res) == 6                                   # 2 tasks x 3 repeats
    assert sorted(calls) == [(7, 0), (7, 1), (7, 2), (8, 0), (8, 1), (8, 2)]

"""Runner per-arm isolation."""

from __future__ import annotations

from pathlib import Path

from agents.bench import runner


def _cfg(arm):
    return runner.RunConfig(
        dataset_path="x", arm=arm, split_name="test", split_path="mem",
        task_ids=[1, 2], model="m", api_base=None, use_openai=True,
    )


def test_setup_env_gives_empty_skills_for_no_skill(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    home, work = runner._setup_arm_env(_cfg("no_skill"))
    try:
        assert (Path(home) / "skills").is_dir()
        assert not any((Path(home) / "skills").iterdir())
        import os

        assert os.environ["KEELSON_HOME"] == home
        assert os.environ["KEELSON_AUTO_SKILL_EVOLUTION"] == "0"
    finally:
        runner._teardown_arm_env(home, work)


def test_setup_env_installs_skill_v1(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    home, work = runner._setup_arm_env(_cfg("skill_v1"))
    try:
        skill_md = Path(home) / "skills" / "multihop-tool-chaining" / "SKILL.md"
        assert skill_md.is_file()
        assert "chaining" in skill_md.read_text(encoding="utf-8").lower()
    finally:
        runner._teardown_arm_env(home, work)


def test_teardown_removes_dirs_and_restores_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    home, work = runner._setup_arm_env(_cfg("raw_api"))
    runner._teardown_arm_env(home, work)
    assert not Path(home).exists() and not Path(work).exists()
    assert Path.cwd() == runner._REPO_ROOT

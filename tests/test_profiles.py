"""Profiles: named model setups, switched with `keelson use` or `-P`."""

from __future__ import annotations

import os

import pytest

from keelson import paths, profiles
from keelson.main import _load_env_file, _resolve_api_config

_VARS = ("APIKEY", "API", "OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL", "KEELSON_PROFILE", "KEELSON_OPENAI_STREAM")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    folder = profiles.profiles_dir()
    folder.mkdir(parents=True)
    (folder / "deepseek.env").write_text("APIKEY=ds-key\nAPI=https://gw.example/v1\nMODEL=deepseek-v4.1-flash\n",
                                         encoding="utf-8")
    (folder / "gemini.env").write_text("APIKEY=gm-key\nAPI=https://gw.example/v1\nMODEL=gemini-3.8-flash\n"
                                       "KEELSON_OPENAI_STREAM=0\n", encoding="utf-8")
    yield project
    for name in _VARS:
        os.environ.pop(name, None)


def test_keelson_use_switches_for_every_project(setup, capsys):
    assert profiles.use_main(["gemini"]) == 0
    assert "KEELSON_PROFILE=gemini" in paths.user_config_file().read_text(encoding="utf-8")
    _load_env_file()
    assert os.environ["MODEL"] == "gemini-3.8-flash"
    assert _resolve_api_config(None)[1] == "gm-key"


def test_the_active_profile_beats_an_old_dotenv_and_config_env(setup):
    (setup / ".env").write_text("OPENAI_API_KEY=old\nOPENAI_BASE_URL=https://old/v1\nMODEL=old-model\n"
                                "KEELSON_OPENAI_STREAM=0\n", encoding="utf-8")
    paths.user_config_file().write_text("MODEL=config-model\nKEELSON_PROFILE=deepseek\n", encoding="utf-8")
    _load_env_file()
    base, key, use_openai = _resolve_api_config(None)
    assert (os.environ["MODEL"], base, key) == ("deepseek-v4.1-flash", "https://gw.example/v1", "ds-key")


def test_profile_flag_is_for_one_run_and_removed_from_argv(setup):
    paths.user_config_file().write_text("KEELSON_PROFILE=deepseek\n", encoding="utf-8")
    rest = profiles.take_profile_flag(["-P", "gemini", "web", "--port", "9000"])
    assert rest == ["web", "--port", "9000"]
    _load_env_file()
    assert os.environ["MODEL"] == "gemini-3.8-flash"
    # The saved default is untouched.
    assert "KEELSON_PROFILE=deepseek" in paths.user_config_file().read_text(encoding="utf-8")


def test_listing_never_shows_a_key(setup, capsys):
    profiles.set_active("deepseek")
    profiles.use_main([])
    out = capsys.readouterr().out
    assert "* deepseek" in out and "gemini-3.8-flash" in out
    assert "ds-key" not in out and "gm-key" not in out


def test_unknown_profile_is_refused_and_switching_off_keeps_other_settings(setup, capsys):
    paths.user_config_file().write_text("MODEL=keep\n", encoding="utf-8")
    assert profiles.use_main(["nope"]) == 2
    profiles.use_main(["deepseek"])
    profiles.use_main(["--none"])
    assert paths.user_config_file().read_text(encoding="utf-8") == "MODEL=keep\n"


def test_a_missing_profile_warns(setup, capsys, monkeypatch):
    monkeypatch.setenv("KEELSON_PROFILE", "gone")
    _load_env_file()
    assert "does not exist" in capsys.readouterr().err


def test_set_model_switches_model_and_window():
    from keelson.agent import Agent

    agent = Agent(permission_mode="dontAsk", model="gemini-3.8-flash", api_base="https://gw.example/v1", api_key="k")
    agent.set_model("deepseek-v4-pro")
    assert agent.model == "deepseek-v4-pro"
    assert agent.effective_window == 256_000 - 20_000
    assert agent._openai_request_extras()["extra_body"] == {"thinking": {"type": "disabled"}}

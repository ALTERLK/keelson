"""Where settings come from, and where runtime data goes (0.4 layout)."""

from __future__ import annotations

import os

import pytest

from keelson import paths
from keelson.main import _load_env_file, _resolve_api_config

_VARS = ("APIKEY", "API", "KEELSON_API_KEY", "KEELSON_API_BASE", "OPENAI_API_KEY", "OPENAI_BASE_URL",
         "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "MODEL")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "parent" / "project"
    project.mkdir(parents=True)
    monkeypatch.setenv("KEELSON_HOME", str(home))
    monkeypatch.chdir(project)
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    yield home, project
    for name in _VARS:
        os.environ.pop(name, None)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_project_config_beats_global(workspace):
    home, project = workspace
    _write(home / "config.env", "MODEL=global-model\nAPIKEY=global-key\n")
    _write(project / ".keelson" / "config.env", "MODEL=project-model\n")
    _load_env_file()
    assert os.environ["MODEL"] == "project-model"
    assert os.environ["APIKEY"] == "global-key"


def test_real_environment_beats_every_file(workspace, monkeypatch):
    home, _ = workspace
    _write(home / "config.env", "MODEL=global-model\n")
    monkeypatch.setenv("MODEL", "from-shell")
    _load_env_file()
    assert os.environ["MODEL"] == "from-shell"


def test_dotenv_in_a_parent_folder_is_not_picked_up(workspace):
    _, project = workspace
    _write(project.parent / ".env", "OPENAI_API_KEY=someone-elses-key\n")
    _load_env_file()
    assert "OPENAI_API_KEY" not in os.environ


def test_legacy_dotenv_in_current_folder_still_works_with_a_note(workspace, capsys):
    _, project = workspace
    _write(project / ".env", "APIKEY=old-style-key\n")
    _load_env_file()
    assert os.environ["APIKEY"] == "old-style-key"
    assert "keelson init" in capsys.readouterr().err


@pytest.mark.parametrize(("env", "use_openai"), [
    ({"APIKEY": "k", "API": "https://api.deepseek.com"}, True),
    ({"APIKEY": "k", "API": "https://api.deepseek.com/anthropic"}, False),
    ({"APIKEY": "k", "ANTHROPIC_BASE_URL": "https://gateway.example/v1"}, False),
    ({"APIKEY": "k", "API": "https://api.anthropic.com"}, False),
    ({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "http://localhost:11434/v1"}, True),
])
def test_the_protocol_follows_the_base_url(workspace, monkeypatch, env, use_openai):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    base, key, openai_protocol = _resolve_api_config(None)
    assert key == "k"
    assert openai_protocol is use_openai


def test_runtime_data_lives_outside_the_project(workspace):
    home, project = workspace
    state = paths.project_state_dir()
    assert home in state.parents
    assert project not in state.parents
    assert paths.project_traces_dir().parent == state
    assert paths.project_sessions_dir().parent == state
    assert paths.skill_evolution_dir().parent == state


def test_existing_pre_0_4_folders_keep_being_used(workspace):
    _, project = workspace
    old = project / ".keelson" / "skill-evolution"
    old.mkdir(parents=True)
    assert paths.skill_evolution_dir() == old
    # Once the new folder exists it wins.
    (paths.project_state_dir() / "skill-evolution").mkdir(parents=True)
    assert paths.skill_evolution_dir() == paths.project_state_dir() / "skill-evolution"

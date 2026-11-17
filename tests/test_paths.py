"""Path resolution + env-var aliasing."""

from __future__ import annotations

from pathlib import Path

import pytest

from keelson import paths


def test_user_home_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KEELSON_HOME", str(tmp_path / "kh"))
    assert paths.user_home() == tmp_path / "kh"


def test_user_home_default(monkeypatch):
    monkeypatch.delenv("KEELSON_HOME", raising=False)
    assert paths.user_home() == Path.home() / ".keelson"


def test_project_dir_is_dot_keelson(in_tmp_cwd):
    assert paths.project_dir().name == ".keelson"
    assert paths.project_dir() == in_tmp_cwd / ".keelson"


def test_skill_evolution_follows_project_dir(in_tmp_cwd):
    assert paths.skill_evolution_dir() == in_tmp_cwd / ".keelson" / "skill-evolution"


def test_env_flag_alias_and_precedence(monkeypatch):
    monkeypatch.delenv("KEELSON_AUTO_SKILL_EVOLUTION", raising=False)
    monkeypatch.delenv("SOME_LEGACY_FLAG", raising=False)
    assert paths.env_flag("KEELSON_AUTO_SKILL_EVOLUTION", legacy="SOME_LEGACY_FLAG", default=True) is True

    monkeypatch.setenv("SOME_LEGACY_FLAG", "0")
    assert paths.env_flag("KEELSON_AUTO_SKILL_EVOLUTION", legacy="SOME_LEGACY_FLAG", default=True) is False

    monkeypatch.setenv("KEELSON_AUTO_SKILL_EVOLUTION", "1")  # new name wins over legacy
    assert paths.env_flag("KEELSON_AUTO_SKILL_EVOLUTION", legacy="SOME_LEGACY_FLAG", default=False) is True


@pytest.mark.parametrize("raw,expected", [("", "project"), ("user", "user")])
def test_env_value_default(monkeypatch, raw, expected):
    monkeypatch.delenv("KEELSON_AUTO_SKILL_TARGET", raising=False)
    monkeypatch.delenv("SOME_LEGACY_TARGET", raising=False)
    if raw:
        monkeypatch.setenv("SOME_LEGACY_TARGET", raw)
    assert paths.env_value("KEELSON_AUTO_SKILL_TARGET", legacy="SOME_LEGACY_TARGET", default="project") == expected

"""Folders named the way real people name them: Chinese, spaces, '+', long, and a
non-ASCII user name (whose home and TEMP folders contain it too)."""

from __future__ import annotations

import os
import tempfile

import pytest

from keelson import paths, tools
from keelson.main import _load_env_file
from keelson.platforms import shell as platform_shell


@pytest.fixture
def awkward_project(tmp_path, monkeypatch):
    project = tmp_path / "源码+简历 测评" / "我的 项目"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)
    return project


def test_file_tools_work_in_a_chinese_folder_with_spaces_and_plus(awkward_project):
    target = awkward_project / "模块 一" / "工具.py"
    assert tools._write_file({"file_path": str(target), "content": "名字 = '旧'\n"}).startswith("Successfully")
    assert tools._edit_file({"file_path": str(target), "old_string": "旧", "new_string": "新"}).startswith("Successfully")
    assert "名字 = '新'" in tools._read_file({"file_path": str(target)})
    assert tools._list_files({"pattern": "**/*.py"}) == "模块 一/工具.py"
    assert tools._grep_search({"pattern": "名字"}) == "模块 一/工具.py:1:名字 = '新'"


async def test_shell_runs_in_a_chinese_folder_with_spaces(awkward_project):
    (awkward_project / "说明.txt").write_text("你好\n", encoding="utf-8")
    sh = platform_shell.current()
    command = "Get-Content -Encoding UTF8 说明.txt" if sh.is_powershell else (
        "type 说明.txt" if sh.kind == "cmd" else "cat 说明.txt")
    out = await tools.run_shell_async({"command": command})
    if sh.kind == "cmd":
        pytest.skip("cmd.exe prints in the console code page")  # kept for completeness, not the default
    assert out == "你好\n"


@pytest.fixture
def non_ascii_user(tmp_path, monkeypatch):
    """A Windows account named 张三: home, TEMP and the Keelson folder all contain it."""
    home = tmp_path / "Users" / "张三"
    temp = home / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(home))
    for name in ("TEMP", "TMP"):
        monkeypatch.setenv(name, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv("KEELSON_HOME", raising=False)
    project = tmp_path / "work"
    project.mkdir()
    monkeypatch.chdir(project)
    return home


def test_config_lives_under_a_non_ascii_home_and_is_read(non_ascii_user, monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    assert paths.user_home() == non_ascii_user / ".keelson"
    paths.user_config_file().parent.mkdir(parents=True)
    paths.user_config_file().write_text("MODEL=deepseek-v4.1-flash\n", encoding="utf-8")
    _load_env_file()
    assert os.environ["MODEL"] == "deepseek-v4.1-flash"
    assert non_ascii_user in paths.project_state_dir().parents
    os.environ.pop("MODEL", None)


async def test_shell_works_when_temp_is_under_a_non_ascii_user(non_ascii_user):
    # PowerShell runs a temporary script from TEMP; the account name must not break it.
    out = await tools.run_shell_async({"command": "echo 好"})
    assert out == "好\n" or platform_shell.current().kind == "cmd"
    assert not list((non_ascii_user / "AppData" / "Local" / "Temp").glob("keelson-*"))


def test_long_paths(tmp_path, monkeypatch):
    deep = tmp_path
    while len(str(deep)) < 280:
        deep = deep / "很长的目录名字_long_folder_name"
    target = deep / "文件.txt"
    try:
        target.parent.mkdir(parents=True)
        target.write_text("probe", encoding="utf-8")
    except OSError:
        pytest.skip("this system does not allow paths over 260 characters (LongPathsEnabled is off)")
    target.unlink()
    assert tools._write_file({"file_path": str(target), "content": "长路径\n"}).startswith("Successfully")
    assert tools._edit_file({"file_path": str(target), "old_string": "长", "new_string": "短"}).startswith("Successfully")
    assert "短路径" in tools._read_file({"file_path": str(target)})

"""list_files / grep_search: glob semantics, .gitignore, GBK text, and the fallbacks."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from keelson import search, tools


@pytest.mark.parametrize(("pattern", "path", "hit"), [
    ("*.py", "a.py", True),
    ("*.py", "src/a.py", False),
    ("**/*.py", "src/deep/a.py", True),
    ("**/*.py", "a.py", True),
    ("src/**", "src/x/y.txt", True),
    ("*.{ts,tsx}", "app.tsx", True),
    ("test_?.py", "test_1.py", True),
    ("[!a]*.md", "b.md", True),
    (".github/**/*.yml", ".github/workflows/ci.yml", True),
])
def test_glob_patterns(pattern, path, hit):
    assert bool(search.glob_to_regex(pattern).match(path)) is hit


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def handler():\n    return 'TODO'\n", encoding="utf-8")
    (tmp_path / "src" / "legacy.c").write_bytes("/* 待办 TODO */\r\n".encode("gbk"))
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.py").write_text("TODO = 1\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00TODO")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _git_init(path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_gitignored_files_are_skipped_in_a_repository(project):
    _git_init(project)
    listed = tools._list_files({"pattern": "**/*.py"})
    assert "src/app.py" in listed and "build/out.py" not in listed
    found = tools._grep_search({"pattern": "TODO"})
    assert "src/app.py:2:" in found and "build/" not in found


def test_python_grep_reads_gbk_and_skips_binaries(project, monkeypatch):
    monkeypatch.setattr(search.shutil, "which", lambda name: None)   # no rg, no git: the walk fallback
    found = tools._grep_search({"pattern": "待办"})
    assert found == "src/legacy.c:1:/* 待办 TODO */"
    assert "logo.png" not in tools._grep_search({"pattern": "TODO"})


def test_include_and_case_insensitive(project, monkeypatch):
    monkeypatch.setattr(search.shutil, "which", lambda name: None)
    assert tools._grep_search({"pattern": "todo", "include": "*.py"}) == "No matches found."
    hits = tools._grep_search({"pattern": "todo", "include": "*.py", "case_insensitive": True})
    assert "src/app.py:2:" in hits and "legacy.c" not in hits


def test_a_bad_regex_explains_itself(project, monkeypatch):
    monkeypatch.setattr(search.shutil, "which", lambda name: None)
    assert "invalid regular expression" in tools._grep_search({"pattern": "(oops"})


def test_bare_pattern_falls_back_to_a_recursive_search(project):
    out = tools._list_files({"pattern": "app.py"})
    assert out.splitlines() == ["(no match at the top level; showing **/app.py)", "src/app.py"]

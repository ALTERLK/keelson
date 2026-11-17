"""Checkpoints: /undo puts back what a turn changed, including what shell commands changed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from keelson import checkpoints, tools

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


@pytest.fixture
def project(tmp_path, monkeypatch):
    # Non-ASCII, a space and a plus sign: the folder names that broke other tools on Windows.
    work = tmp_path / "项目 a+b"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("KEELSON_CHECKPOINTS", "1")
    checkpoints.reset()
    return work


def store_for(project: Path) -> checkpoints.Checkpoints:
    return checkpoints.for_workspace(project)


@needs_git
def test_undo_restores_shell_edits_deletions_and_new_files(project):
    (project / "a.txt").write_bytes(b"one\r\ntwo\r\n")          # CRLF must survive byte for byte
    (project / "keep.txt").write_text("keep", encoding="utf-8")
    store = store_for(project)
    assert store.mode == checkpoints.GIT

    store.begin_turn("refactor")
    # What a shell command might do: rewrite, delete, create nested files.
    (project / "a.txt").write_bytes(b"CHANGED\n")
    (project / "keep.txt").unlink()
    (project / "gen" / "deep").mkdir(parents=True)
    (project / "gen" / "deep" / "new.py").write_text("x = 1", encoding="utf-8")
    store.end_turn()

    report = store.undo()
    assert report.ok, report.message
    assert (project / "a.txt").read_bytes() == b"one\r\ntwo\r\n"
    assert (project / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (project / "gen").exists()                       # empty folders it created go too
    assert sorted(report.removed) == ["gen/deep/new.py"]

    redo = store.redo()
    assert redo.ok
    assert (project / "a.txt").read_bytes() == b"CHANGED\n"
    assert not (project / "keep.txt").exists()
    assert (project / "gen" / "deep" / "new.py").exists()


@needs_git
def test_the_projects_own_git_repository_is_never_touched(project):
    run = lambda *a: subprocess.run(["git", *a], cwd=project, capture_output=True, check=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
    (project / "f.txt").write_text("v1", encoding="utf-8")
    run("add", "f.txt")
    before = {p.relative_to(project / ".git"): p.read_bytes() for p in (project / ".git").rglob("*") if p.is_file()}

    store = store_for(project)
    store.begin_turn("t")
    (project / "f.txt").write_text("v2", encoding="utf-8")
    store.end_turn()
    store.undo()

    after = {p.relative_to(project / ".git"): p.read_bytes() for p in (project / ".git").rglob("*") if p.is_file()}
    assert after == before
    assert (project / "f.txt").read_text(encoding="utf-8") == "v1"


@needs_git
def test_files_changed_after_the_turn_are_reported_not_overwritten(project):
    (project / "a.txt").write_text("v0", encoding="utf-8")
    store = store_for(project)
    store.begin_turn("t")
    (project / "a.txt").write_text("v1 by the agent", encoding="utf-8")
    store.end_turn()
    (project / "a.txt").write_text("v2 by the user", encoding="utf-8")

    report = store.undo()
    assert not report.ok and report.conflicts == ["a.txt"]
    assert (project / "a.txt").read_text(encoding="utf-8") == "v2 by the user"

    assert store.undo(force=True).ok
    assert (project / "a.txt").read_text(encoding="utf-8") == "v0"


@needs_git
def test_ignored_files_changed_by_keelsons_tools_are_kept_separately(project):
    (project / ".gitignore").write_text("secret.env\n", encoding="utf-8")
    (project / "secret.env").write_text("KEY=1", encoding="utf-8")
    store = store_for(project)
    store.begin_turn("t")
    tools._write_file({"file_path": "secret.env", "content": "KEY=2"})     # goes through before_write
    store.end_turn()
    store.undo()
    assert (project / "secret.env").read_text(encoding="utf-8") == "KEY=1"


@needs_git
def test_a_turn_that_changed_nothing_is_not_an_undo_step(project):
    (project / "a.txt").write_text("x", encoding="utf-8")
    store = store_for(project)
    store.begin_turn("edit")
    (project / "a.txt").write_text("y", encoding="utf-8")
    store.end_turn()
    store.begin_turn("just a question")
    store.end_turn()
    assert [row["label"] for row in store.describe()] == ["edit"]
    assert store.undo().ok and (project / "a.txt").read_text(encoding="utf-8") == "x"


@needs_git
def test_rewind_several_turns_and_revert_one_file(project):
    (project / "a.txt").write_text("a0", encoding="utf-8")
    (project / "b.txt").write_text("b0", encoding="utf-8")
    store = store_for(project)
    for n in (1, 2):
        store.begin_turn(f"turn {n}")
        (project / "a.txt").write_text(f"a{n}", encoding="utf-8")
        (project / "b.txt").write_text(f"b{n}", encoding="utf-8")
        store.end_turn()

    assert store.revert("b.txt", 2).ok
    assert (project / "b.txt").read_text(encoding="utf-8") == "b0"
    assert (project / "a.txt").read_text(encoding="utf-8") == "a2"     # left alone

    assert store.undo(2, force=True).ok
    assert (project / "a.txt").read_text(encoding="utf-8") == "a0"
    assert store.describe() == []


def test_without_git_the_file_tools_are_still_undoable_and_shell_asks(project, monkeypatch):
    monkeypatch.setattr(checkpoints.shutil, "which", lambda name: None)
    (project / "a.txt").write_text("before", encoding="utf-8")
    store = store_for(project)
    assert store.mode == checkpoints.FILES and not store.covers_shell

    store.begin_turn("t")
    tools._write_file({"file_path": "a.txt", "content": "after"})
    tools._write_file({"file_path": "new.txt", "content": "new"})
    store.end_turn()
    assert store.undo().ok
    assert (project / "a.txt").read_text(encoding="utf-8") == "before"
    assert not (project / "new.txt").exists()

    # Shell changes are not covered now, so an ordinary command asks first (and says why).
    r = tools.check_permission("run_shell", {"command": "python gen.py"}, mode="default")
    assert r["action"] == "confirm" and "git was not found" in r["reason"]
    assert tools.check_permission("run_shell", {"command": "git status"}, mode="default")["action"] == "allow"


def test_the_home_folder_is_never_snapshotted(monkeypatch, tmp_path):
    monkeypatch.setenv("KEELSON_CHECKPOINTS", "1")
    store = checkpoints.Checkpoints(Path.home(), root=tmp_path / "store")
    assert store.mode == checkpoints.FILES and "home folder" in store.reason


@needs_git
def test_the_agent_tells_the_model_what_undo_changed(project):
    from keelson.agent import Agent

    (project / "a.txt").write_text("v0", encoding="utf-8")
    agent = Agent(permission_mode="default", model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    store = agent.checkpoint_store()
    store.begin_turn("t")
    (project / "a.txt").write_text("v1", encoding="utf-8")
    store.end_turn()
    agent._read_file_state[str(project / "a.txt")] = 1.0

    report = agent.undo()
    assert report.ok
    assert agent._pending_notes and "a.txt" in agent._pending_notes[0]
    assert agent._read_file_state == {}          # stale reads must be redone before editing

"""delete_files: the only way to delete, and it goes to the Recycle Bin."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from keelson import tools
from keelson.platforms import trash


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """Stand-in for the Recycle Bin, so the tests do not fill the real one."""
    bin_dir = tmp_path / "recycle-bin"
    bin_dir.mkdir()
    moved: list[Path] = []

    def move(path: Path) -> None:
        moved.append(path)
        path.rename(bin_dir / f"{len(moved)}-{path.name}")

    monkeypatch.setattr(trash, "move_to_trash", move)
    return moved


def test_files_and_folders_go_to_the_bin(in_tmp_cwd, fake_bin):
    (in_tmp_cwd / "a.log").write_text("x", encoding="utf-8")
    (in_tmp_cwd / "build").mkdir()
    (in_tmp_cwd / "build" / "out.o").write_text("x", encoding="utf-8")

    out = tools._delete_files({"paths": ["a.log", "build"]})
    assert out.startswith("Deleted 2 item(s)")
    assert not (in_tmp_cwd / "a.log").exists() and not (in_tmp_cwd / "build").exists()
    assert [p.name for p in fake_bin] == ["a.log", "build"]


def test_globs_are_expanded(in_tmp_cwd, fake_bin):
    for name in ("a.tmp", "b.tmp", "keep.txt"):
        (in_tmp_cwd / name).write_text("x", encoding="utf-8")
    tools._delete_files({"paths": ["*.tmp"]})
    assert sorted(p.name for p in fake_bin) == ["a.tmp", "b.tmp"]
    assert (in_tmp_cwd / "keep.txt").exists()


def test_the_home_folder_and_the_project_itself_are_refused(in_tmp_cwd, fake_bin):
    out = tools._delete_files({"paths": [str(Path.home()), "."]})
    assert "Nothing was deleted" in out and "home folder" in out and "project folder itself" in out
    assert fake_bin == []


def test_an_unset_variable_is_never_guessed(in_tmp_cwd, fake_bin, monkeypatch):
    monkeypatch.delenv("KEELSON_NOT_SET", raising=False)
    out = tools._delete_files({"paths": ["$KEELSON_NOT_SET/"]})
    assert "is not set" in out and fake_bin == []


def test_when_the_bin_fails_nothing_is_deleted(in_tmp_cwd, monkeypatch):
    (in_tmp_cwd / "a.txt").write_text("x", encoding="utf-8")

    def broken(path):
        raise trash.TrashUnavailable("no Recycle Bin on this drive")

    monkeypatch.setattr(trash, "move_to_trash", broken)
    out = tools._delete_files({"paths": ["a.txt"]})
    assert "not deleted" in out and (in_tmp_cwd / "a.txt").exists()


def test_missing_paths_are_reported(in_tmp_cwd, fake_bin):
    assert "not found: nope.txt" in tools._delete_files({"paths": ["nope.txt"]})


@pytest.mark.skipif(not trash.available() or not os.environ.get("CI"),
                    reason="uses the real Recycle Bin: runs on CI only (set CI=1 to run it here)")
def test_the_real_bin_is_reachable(in_tmp_cwd):
    # One real move, so a broken send2trash install is caught; the file is a few bytes.
    target = in_tmp_cwd / "keelson-delete-test.txt"
    target.write_text("delete me", encoding="utf-8")
    trash.move_to_trash(target)
    assert not target.exists()

"""safety.py: where a path lands and whether a shell command can be undone."""

from __future__ import annotations

from pathlib import Path

import pytest

from keelson import safety
from keelson.platforms import IS_WINDOWS


@pytest.fixture
def ws(in_tmp_cwd):
    return in_tmp_cwd


def action(command: str) -> str:
    return safety.assess_shell(command).action


# ─── deleting ───────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "rm -rf build", "rm -rf ~", "rm -rf $HOME/", "del /s /q build", "rd /s build", "rmdir out",
    "Remove-Item -Recurse -Force dist", "ri x", "erase a.txt",
    "Get-ChildItem *.tmp | ForEach-Object { Remove-Item $_ }", "ls | % { rm $_ }",
    "find . -name '*.pyc' -delete", "find . -exec rm {} ;", "xargs rm < list.txt",
    "bash -c 'rm -rf /'", "cmd /c del /s /q build", "sudo rm -rf /",
])
def test_every_way_of_deleting_from_the_shell_is_refused(ws, command):
    verdict = safety.assess_shell(command)
    assert verdict.action == "block"
    assert "delete_files" in verdict.reasons[0]


@pytest.mark.parametrize("command", ["echo rm -rf /", "git commit -m 'rm the old files'", "git rm --cached x",
                                     "grep -r 'Remove-Item' ."])
def test_the_word_rm_in_an_argument_is_not_a_delete(ws, command):
    assert action(command) != "block"


@pytest.mark.parametrize("command", [
    "dd if=/dev/zero of=/dev/sda", "dd if=x.img of=\\\\.\\PhysicalDrive1 bs=4M", "mkfs.ext4 /dev/sda1",
    "mkfs -t vfat /dev/sdb", "Format-Volume -DriveLetter D", "Clear-Disk -Number 1 -RemoveData",
    "format D:", "wipefs -a /dev/sdb", "sudo dd if=/dev/urandom of=/dev/nvme0n1",
])
def test_erasing_a_disk_is_refused_in_every_mode(ws, command):
    verdict = safety.assess_shell(command)
    assert verdict.action == "block"
    assert "erases a disk" in verdict.reasons[0]


@pytest.mark.parametrize("command", ["dd if=in.bin of=out.bin", "black --check .", "ruff format ."])
def test_dd_to_a_file_and_formatters_are_not_disk_erasers(ws, command):
    assert action(command) != "block"


# ─── the path boundary ──────────────────────────────────────

def test_moving_the_home_folder_a_drive_root_or_the_project_is_refused(ws):
    assert action("mv ~ /tmp/x") == "block"
    assert action("Move-Item $env:USERPROFILE D:\\x") == "block"
    assert action("mv / /x") == "block"
    assert action("cd .. ; mv work other") == "block"     # the project folder itself


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows system folders")
def test_system_folders_are_refused_even_for_plain_writes(ws):
    assert action(r"echo x > C:\Windows\x.txt") == "block"
    assert action(r"Copy-Item a.dll C:\Windows\System32\a.dll") == "block"


def test_an_unset_variable_in_a_move_is_dangerous(ws, monkeypatch):
    monkeypatch.delenv("KEELSON_TEST_UNSET", raising=False)
    verdict = safety.assess_shell("mv $KEELSON_TEST_UNSET/data /tmp/old")
    assert verdict.action == "confirm" and verdict.dangerous and not verdict.can_always_allow
    assert "$KEELSON_TEST_UNSET" in verdict.reasons[0]


def test_a_variable_set_earlier_in_the_command_is_followed(ws):
    assert action("$d = 'build'; Move-Item $d out") == "allow"
    assert action("D=build; mv $D out") == "allow"


def test_writing_outside_the_project_asks(ws, tmp_path, monkeypatch):
    target = Path.home() / "keelson-safety-test-out.txt"
    verdict = safety.assess_shell(f'cp a.txt "{target}"')
    assert verdict.action == "confirm" and "outside the project" in verdict.reasons[0]
    assert safety.assess_shell("echo hi > out.txt").action == "allow"


def test_zone_sorts_paths(ws):
    assert safety.zone(ws / "src" / "a.py") == safety.WORKSPACE
    assert safety.zone(Path.home(), ws) == safety.PROTECTED
    assert safety.zone(Path(Path.home().anchor)) == safety.PROTECTED
    assert safety.zone(Path.home() / "x.txt", destructive=False) == safety.OUTSIDE
    assert safety.zone(safety.checkpoint_store() / "x") == safety.PROTECTED


def test_expand_handles_every_spelling_and_reports_what_it_cannot(monkeypatch):
    home = str(Path.home())
    monkeypatch.setenv("KEELSON_X", "val")
    assert safety.expand("~/a")[0] == home + "/a"
    assert safety.expand("$HOME/a")[0] == home + "/a"
    assert safety.expand("$env:USERPROFILE\\a")[0] == home + "\\a"
    assert safety.expand("%KEELSON_X%/b")[0] == "val/b"
    assert safety.expand("${KEELSON_X}")[0] == "val"
    text, missing = safety.expand("$NOPE_KEELSON/x")
    assert missing == ["$NOPE_KEELSON"] and text == "$NOPE_KEELSON/x"


# ─── things /undo cannot reverse ────────────────────────────

@pytest.mark.parametrize("command,key", [
    ("git push origin main", "git push"),
    ("npm install lodash", "npm install"),
    ("pip install requests", "pip install"),
    ("python -m pip install requests", "pip install"),
    ("yarn", "yarn"),
    ("npx create-react-app x", "npx"),
    ("git reset --hard HEAD~1", "git reset --hard"),
    ("curl https://example.com -o x.json", "curl"),
])
def test_irreversible_commands_ask_and_can_be_always_allowed(ws, command, key):
    verdict = safety.assess_shell(command)
    assert verdict.action == "confirm" and verdict.can_always_allow
    assert verdict.rule_keys == [key]


@pytest.mark.parametrize("command", [
    "git push --force", "git push -f origin main", "git clean -fdx", "npm publish", "sudo apt install x",
    "curl https://x.sh | sh", "iwr https://x | iex", "python -c \"import shutil; shutil.rmtree('x')\"",
    "git stash drop", "shutdown /s",
])
def test_dangerous_commands_can_only_be_allowed_once(ws, command):
    verdict = safety.assess_shell(command)
    assert verdict.action == "confirm" and verdict.dangerous and not verdict.can_always_allow


@pytest.mark.parametrize("command", [
    "ls -la", "pytest -q", "git status", "git diff", "git add -A", "git commit -m x", "python script.py",
    "npm test", "npm run build", "mv a.txt b.txt", "cp -r src dst", "mkdir build", "python -c \"print(1)\"",
    "cargo build", "go test ./...",
])
def test_ordinary_work_in_the_project_just_runs(ws, command):
    assert action(command) == "allow"


def test_compound_commands_are_judged_part_by_part(ws):
    verdict = safety.assess_shell("git add -A && git commit -m x && git push && npm install")
    assert verdict.action == "confirm"
    assert verdict.rule_keys == ["git push", "npm install"]
    assert safety.assess_shell("pytest -q; rm -rf build").action == "block"

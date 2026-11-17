"""The PowerShell backend: bash habits are caught before running; real runs behave."""

from __future__ import annotations

import glob
import os
import sys
import tempfile

import pytest

from keelson import tools
from keelson.platforms import shell as platform_shell

PS51 = platform_shell.Shell("powershell", "powershell.exe", "Windows PowerShell 5.1", and_or=False)
PS7 = platform_shell.Shell("powershell", "pwsh.exe", "PowerShell 7 (pwsh)", and_or=True)


@pytest.mark.parametrize(("command", "advice"), [
    ("rm -rf build", "delete_files"),
    ("export API_KEY=abc", "$env:API_KEY = 'abc'"),
    ("NODE_ENV=test npm test", "$env:NODE_ENV = 'test'; npm test"),
    ("pytest 2>/dev/null", "$null"),
    ("dir > nul", "$null"),
    ("cat <<'EOF' > a.txt\nhi\nEOF", "here-string"),
    ("npm ci && npm test", "if ($?)"),
    ("ls -la", "Get-ChildItem -Force"),
    ("which python", "(Get-Command python).Source"),
    ('"C:\\Program Files\\nodejs\\node.exe" --version', "& \"C:\\Program Files"),
    ("source .venv/bin/activate", ". .venv/bin/activate"),
])
def test_bash_habits_are_refused_with_the_powershell_form(command, advice):
    message = platform_shell.preflight(command, PS51)
    assert message is not None and advice in message
    assert "Command not run" in message


@pytest.mark.parametrize("command", [
    "python -m pytest -q",
    "git status; git diff --stat",
    'Write-Output "a && b > nul"',          # inside quotes: not operators
    '"abc" -replace "a", "b"',              # a quoted string, not a program path
    "Get-ChildItem -Recurse -Filter *.py",
    "rm old.txt",                           # PowerShell's rm alias handles this
    "$env:FOO = 'bar'; python app.py",
])
def test_powershell_commands_pass(command):
    assert platform_shell.preflight(command, PS51) is None


def test_and_or_are_fine_on_powershell_7():
    assert platform_shell.preflight("npm ci && npm test", PS7) is None


def test_other_shells_are_not_checked():
    cmd = platform_shell.Shell("cmd", "cmd.exe", "cmd.exe")
    assert platform_shell.preflight("rm -rf build", cmd) is None


def test_error_positions_point_at_the_users_command(tmp_path):
    script = tmp_path / "keelson-x.ps1"
    text = f"boom\nAt {script}:3 char:1\n+ throw 'boom'"
    assert platform_shell.tidy_powershell_errors(text, script) == "boom\nAt line 2 char:1\n+ throw 'boom'"


def test_error_positions_are_tidied_when_powershell_prints_another_form_of_the_path():
    # Python's TEMP in 8.3 form, PowerShell 7 printing the long form (seen on GitHub's Windows runners).
    script = platform_shell.Path(r"C:\Users\RUNNER~1\AppData\Local\Temp\keelson-abc123.ps1")
    text = ("Get-Item: C:\\Users\\runneradmin\\AppData\\Local\\Temp\\keelson-abc123.ps1:2\n"
            "Line |\n   2 |  Get-Item 'C:\\nope'")
    tidy = platform_shell.tidy_powershell_errors(text, script)
    assert ".ps1" not in tidy and tidy.startswith("Get-Item: line 1")


# ─── real runs (Windows) ────────────────────────────────────

needs_powershell = pytest.mark.skipif(
    not platform_shell.current().is_powershell, reason="PowerShell is the shell only on Windows")


@needs_powershell
async def test_chinese_output_and_exit_codes():
    assert await tools.run_shell_async({"command": "Write-Output '中文 输出'"}) == "中文 输出\n"
    out = await tools.run_shell_async({"command": f'& "{sys.executable}" -c "print(\'中文\'); import sys; sys.exit(3)"'})
    assert out.startswith("Command failed (exit code 3)") and "中文" in out


@needs_powershell
async def test_a_failing_cmdlet_fails_and_reports_plain_text():
    out = await tools.run_shell_async({"command": "Get-Item 'C:\\definitely-not-here-keelson'"})
    assert out.startswith("Command failed (exit code 1)")
    assert "CLIXML" not in out and ".ps1" not in out


@needs_powershell
async def test_the_temporary_script_is_removed():
    pattern = os.path.join(tempfile.gettempdir(), "keelson-*.ps1")
    before = set(glob.glob(pattern))
    await tools.run_shell_async({"command": "Write-Output ok"})
    assert set(glob.glob(pattern)) == before

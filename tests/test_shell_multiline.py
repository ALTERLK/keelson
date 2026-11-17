"""A command of several lines runs every line, on every platform.

`sh -c` does that anyway. `cmd /c` runs the first line and drops the rest
without a word, which the case study showed misleading the model again and
again: it "ran" a repro script and saw no output, because only `python -c "`
had been executed.
"""

from __future__ import annotations

import glob
import os
import tempfile

import pytest

from keelson import tools
from keelson.tools import IS_WIN, _batch_line, _multiline_quote_problem, _prepare_shell_command


async def test_every_line_of_a_command_runs():
    assert await tools.run_shell_async({"command": "echo first\necho second\necho third"}) == "first\nsecond\nthird\n"


def test_every_line_runs_on_the_blocking_path_too():
    assert tools._run_shell({"command": "echo first\necho second"}) == "first\nsecond\n"


async def test_the_exit_code_is_the_last_line_s():
    failing = "exit /b 4" if IS_WIN else "exit 4"
    out = await tools.run_shell_async({"command": f"echo before\n{failing}"})
    assert out.startswith("Command failed (exit code 4)") and "before" in out


async def test_a_single_line_is_handed_over_unchanged():
    command, script = _prepare_shell_command("echo one")
    assert (command, script) == ("echo one", None)
    assert await tools.run_shell_async({"command": "echo one"}) == "one\n"


def test_posix_commands_are_never_rewritten():
    multi = 'python -c "\nprint(1)\n"\necho done'
    assert _prepare_shell_command(multi, windows=False) == (multi, None)
    assert _multiline_quote_problem(multi, windows=False) is None


def test_a_quoted_string_over_several_lines_is_refused_with_the_reason():
    problem = _multiline_quote_problem('python -c "\nimport sys\nprint(sys.version)\n"', windows=True)
    assert problem is not None
    assert "cmd.exe" in problem and "line 1" in problem and "exec(" in problem


@pytest.mark.parametrize("command", [
    'python -c "print(1)"',                                   # one line: nothing to check
    'python -c "print(1)"\npython -c "print(2)"',             # every quote closes on its own line
    "echo a\necho b",
    'type NUL > .env\npython -m dotenv -f .env get EMPTY\necho RC: %errorlevel%',
])
def test_balanced_commands_are_let_through(command):
    assert _multiline_quote_problem(command, windows=True) is None


def test_batch_lines_call_other_batch_scripts(monkeypatch):
    found = {"npm": r"C:\tools\npm.CMD", "python": r"C:\py\python.exe"}
    monkeypatch.setattr(tools.shutil, "which", lambda name: found.get(name))

    assert _batch_line("npm test") == "call npm test"          # without `call` the rest of the script is skipped
    assert _batch_line("python -m pytest") == "python -m pytest"
    assert _batch_line("call npm test") == "call npm test"
    assert _batch_line("@echo off") == "@echo off"
    assert _batch_line("") == ""
    assert _batch_line("unknown-tool --flag") == "unknown-tool --flag"


@pytest.mark.skipif(not IS_WIN, reason="batch files are a Windows detail")
async def test_windows_expands_errorlevel_per_line_and_cleans_up():
    pattern = os.path.join(tempfile.gettempdir(), "keelson-*.cmd")
    before = set(glob.glob(pattern))

    out = await tools.run_shell_async({"command": 'python -c "import sys; sys.exit(3)"\necho RC: %errorlevel%'})
    assert out == "RC: 3\n"                                   # on one `cmd /c` line this would print the stale value

    timed_out = await tools.run_shell_async({"command": 'python -c "import time; time.sleep(30)"\necho never',
                                             "timeout": 1500})
    assert timed_out == "Command timed out after 1500ms"
    assert set(glob.glob(pattern)) == before                  # the temporary script is removed either way

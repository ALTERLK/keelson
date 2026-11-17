"""run_shell: non-blocking, streaming, and killable (timeout or cancelled turn)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

from keelson import events
from keelson.events import EventBus
from keelson.tools import _OutputRelay, _run_shell, execute_tool, run_shell_async

pytestmark = pytest.mark.skipif(" " in sys.executable, reason="interpreter path needs quoting")


def _script(tmp_path, name: str, body: str) -> str:
    """A shell command that runs ``body`` with this interpreter."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return f"{sys.executable} {path}"


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        # A killed process that nobody has waited for yet still has a pid.
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().rsplit(")", 1)[-1].split()[0] != "Z"
    except OSError:
        return True


async def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# ─── the model sees the same text as before ─────────────────


async def test_result_text_matches_the_blocking_implementation(tmp_path):
    ok = _script(tmp_path, "ok.py", "print('hello')\nprint('world')\n")
    bad = _script(tmp_path, "bad.py", "import sys\nprint('partial')\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    quiet = _script(tmp_path, "quiet.py", "pass\n")

    for command in (ok, bad, quiet):
        assert await run_shell_async({"command": command}) == _run_shell({"command": command})

    assert (await run_shell_async({"command": bad})).startswith("Command failed (exit code 3)")
    assert await run_shell_async({"command": quiet}) == "(no output)"


# ─── output arrives while the command is still running ──────


async def test_output_is_relayed_before_the_command_finishes(tmp_path):
    command = _script(tmp_path, "slow.py", (
        "import sys, time\n"
        "print('first', flush=True)\n"
        "time.sleep(1.0)\n"
        "print('second', flush=True)\n"
    ))
    arrivals: list[tuple[float, str]] = []
    started = time.monotonic()
    result = await run_shell_async({"command": command}, on_output=lambda text: arrivals.append((time.monotonic() - started, text)))
    total = time.monotonic() - started

    assert "first" in result and "second" in result
    first_seen = next(t for t, text in arrivals if "first" in text)
    assert first_seen < total - 0.5, "the first line should be relayed long before the command ends"


async def test_the_event_loop_keeps_running_during_a_command(tmp_path):
    command = _script(tmp_path, "sleep.py", "import time\ntime.sleep(0.8)\n")
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    await run_shell_async({"command": command})
    task.cancel()
    # A blocking subprocess.run would have frozen the ticker at ~0.
    assert ticks >= 15


# ─── a timeout or a stopped turn kills the whole process tree ─


_CHILD = (
    "import os, pathlib, time\n"
    "pathlib.Path(r'{pidfile}').write_text(str(os.getpid()))\n"
    "time.sleep(60)\n"
)


async def test_timeout_kills_the_process(tmp_path):
    pidfile = tmp_path / "pid"
    command = _script(tmp_path, "hang.py", _CHILD.format(pidfile=pidfile))

    result = await run_shell_async({"command": command, "timeout": 1500})

    assert result == "Command timed out after 1500ms"
    pid = int(pidfile.read_text())
    assert await _wait_until(lambda: not _alive(pid)), "the command outlived its timeout"


async def test_cancelling_the_turn_kills_the_process(tmp_path):
    pidfile = tmp_path / "pid"
    command = _script(tmp_path, "hang.py", _CHILD.format(pidfile=pidfile))

    task = asyncio.create_task(run_shell_async({"command": command}))
    assert await _wait_until(pidfile.exists), "the command never started"
    await asyncio.sleep(0.1)
    pid = int(pidfile.read_text())
    assert _alive(pid)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _wait_until(lambda: not _alive(pid)), "the command outlived the cancelled turn"


# ─── live output is attributed to the call that produced it ─


async def test_tool_output_events_carry_the_call_id(tmp_path, in_tmp_cwd):
    command = _script(tmp_path, "say.py", "print('from the tool')\n")
    bus = EventBus()
    with events.use_bus(bus), events.tool_call("call-9"):
        result = await execute_tool("run_shell", {"command": command})

    assert "from the tool" in result
    outputs = [e for e in bus.history() if e.type == events.TOOL_OUTPUT]
    assert outputs and all(e.data["id"] == "call-9" for e in outputs)
    assert "from the tool" in "".join(e.data["text"] for e in outputs)
    # ...and went to the bus that was current, not the process-wide one.
    assert not [e for e in events.bus.history() if e.type == events.TOOL_OUTPUT and e.data.get("id") == "call-9"]


# ─── the relay batches and caps what it publishes ───────────


def test_relay_coalesces_bursts_and_caps_the_live_view():
    sent: list[str] = []
    relay = _OutputRelay(sent.append)
    relay.MAX_CHARS = 100
    for _ in range(50):
        relay.feed("x" * 10)       # a burst far faster than the flush interval
    relay.flush()

    assert len(sent) <= 3, "a burst should be coalesced into a few events, not fifty"
    text = "".join(sent)
    assert text.count("x") == 100 and "capped" in text

    relay.feed("more")
    relay.flush()
    assert "".join(sent) == text, "nothing is published after the cap"

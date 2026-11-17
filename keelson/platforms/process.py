"""Starting and stopping child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys

_WINDOWS = sys.platform == "win32"


def no_window_flags() -> dict[str, int]:
    """subprocess kwargs that stop a console window flashing up on Windows.

    Matters once Keelson runs as a windowed app: every git or shell call would
    otherwise open (and close) a black console window.
    """
    if _WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def kill_process_tree(pid: int) -> None:
    """Stop a process *and everything it started*.

    Killing only the shell would leave its children running (``cmd /c pytest``
    dies, pytest does not). Windows: ``taskkill /T`` walks the tree. POSIX: the
    shell was started in its own session, so its process group is the tree.
    """
    try:
        if _WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10, check=False, **no_window_flags(),
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass  # already gone

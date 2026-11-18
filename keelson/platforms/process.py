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


def pid_alive(pid: int) -> bool:
    """Whether a process with this id is still running."""
    if pid <= 0:
        return False
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259                # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def exit_when_parent_dies(pid: int, interval: float = 2.0) -> None:
    """Watch ``pid`` from a daemon thread; end this process when it is gone.

    The desktop app's console server runs as a child process. If the window
    crashes or is killed, nothing would stop the server; this does.
    """
    import threading
    import time

    def watch() -> None:
        while pid_alive(pid):
            time.sleep(interval)
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


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

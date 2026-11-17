"""Everything that differs between operating systems, behind one import.

The rest of Keelson asks this package instead of checking ``sys.platform``
itself, so adding Linux or macOS support means adding a backend here, not
hunting through the agent. Windows is the primary target for 0.4; the POSIX
branches are kept working (CI runs on Ubuntu) but are not yet tuned.

Named ``platforms`` (plural) so it can never shadow the standard library's
``platform`` module.
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

if IS_WINDOWS:
    NAME = "windows"
elif IS_MACOS:
    NAME = "macos"
else:
    NAME = "linux"

from .process import kill_process_tree, no_window_flags  # noqa: E402

__all__ = ["IS_MACOS", "IS_WINDOWS", "NAME", "kill_process_tree", "no_window_flags"]

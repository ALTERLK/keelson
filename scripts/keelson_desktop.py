"""Entry point of the windowed Keelson.exe (see scripts/build_desktop.py).

One executable plays two parts, so a frozen install needs no Python:

* ``Keelson.exe [FOLDER]`` opens the window (keelson.desktop.main);
* ``Keelson.exe --console-server --port N ...`` is the console server the window
  starts for each project (keelson.web.cli.main). Being the windowed build, it
  never opens a console window of its own.
"""

import sys


def run() -> int:
    from keelson.desktop import _quiet_streams

    _quiet_streams()  # a windowed exe starts with no stdout/stderr; printing would raise
    if len(sys.argv) > 1 and sys.argv[1] == "--console-server":
        from keelson.web.cli import main as serve

        return serve(sys.argv[2:])
    from keelson.desktop import main as window

    return window(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(run())

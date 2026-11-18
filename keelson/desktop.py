"""``keelson desktop`` — Keelson in its own window (Windows first).

The window is a pywebview (WebView2 on Windows) frame around the same console
``keelson web`` serves. One window, one project at a time:

    Keelson.exe ──▶ window ──▶ desktop.html   first-run wizard, recent projects
                         └──▶ http://127.0.0.1:<port>/   the console, served by a
                              child process started *in the project folder*

The console server is a child process because the agent's tools, checkpoints
and project settings all follow the working directory: switching project means
starting a fresh server there. The child exits when the window closes, and on
its own if the window process dies (``--parent-pid``).

The page reaches Python through ``window.pywebview.api`` (``DesktopApi``):
choosing a folder, saving the model key, opening a project. Those calls are
honoured only from loopback pages, i.e. the wizard and the console itself;
links to other sites open in the system browser.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from . import models, paths
from .i18n import pick
from .platforms.process import kill_process_tree, no_window_flags

RECENT_LIMIT = 8
START_TIMEOUT = 45.0
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def static_dir() -> Path:
    return Path(__file__).parent / "web" / "static"


# ── remembered projects ───────────────────────────────────────


def state_file() -> Path:
    return paths.user_home() / "desktop.json"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data: dict[str, Any]) -> None:
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def remember_project(path: str) -> list[str]:
    data = load_state()
    recent = [p for p in data.get("recent", []) if isinstance(p, str) and not _same(p, path)]
    data["recent"] = [path, *recent][:RECENT_LIMIT]
    data["last"] = path
    save_state(data)
    return data["recent"]


def forget_project(path: str) -> list[str]:
    data = load_state()
    data["recent"] = [p for p in data.get("recent", []) if not _same(p, path)]
    if data.get("last") and _same(data["last"], path):
        data["last"] = None
    save_state(data)
    return data["recent"]


def recent_projects() -> list[dict[str, Any]]:
    return [{"path": p, "name": Path(p).name or p, "exists": Path(p).is_dir()}
            for p in load_state().get("recent", []) if isinstance(p, str)]


# ── model configuration ───────────────────────────────────────


@contextmanager
def _config_env():
    """os.environ as `keelson` would see it after loading its config files — restored afterwards.

    The window process must not keep the key in its own environment: the console
    server it starts inherits that environment, and environment variables win over
    the config file, so a key changed in the wizard would be ignored.
    """
    from dotenv import dotenv_values

    from . import profiles

    saved = dict(os.environ)
    try:
        sources = []
        name = profiles.active_profile()
        if name:
            sources.append(profiles.profile_file(name))
        sources.append(paths.user_config_file())
        for source in sources:
            if source.is_file():
                for key, value in dotenv_values(source).items():
                    if value is not None and key not in os.environ:
                        os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def model_config() -> dict[str, Any]:
    from .main import _resolve_api_config

    with _config_env():
        base, key, _ = _resolve_api_config(None)
        model = os.environ.get("MODEL") or models.DEFAULT_MODEL
    return {"configured": bool(key), "model": model if key else "", "base": base or ""}


def _uses_openai_protocol(kind: str, base: str) -> bool:
    from .main import _is_anthropic_compatible_base_url

    if kind == "openai":
        return True
    if kind in ("anthropic", "anthropic-native"):
        return False
    return not _is_anthropic_compatible_base_url(base)


def config_target() -> Path:
    """Where the wizard writes: the active profile's file if there is one, else ~/.keelson/config.env."""
    from . import profiles

    name = profiles.active_profile()
    return profiles.profile_file(name) if name else paths.user_config_file()


# ── project inspection ────────────────────────────────────────


def inspect_project(path: str) -> dict[str, Any]:
    from . import verify

    ws = Path(path)
    if not ws.is_dir():
        return {"ok": False, "error": pick("文件夹不存在", "That folder does not exist")}
    try:
        checks, source = verify.commands_for(ws)
    except Exception:
        checks, source = [], ""
    has_md = verify.keelson_md(ws).is_file()
    suggestions = [
        pick("用一段话介绍这个项目的结构，以及从哪里开始读代码。",
             "Explain how this project is laid out and where to start reading."),
        pick("运行测试，修复失败的用例，不要改测试本身。",
             "Run the tests and fix what fails, without changing the tests.")
        if checks else
        pick("找出这个项目该怎么运行测试，并写进 KEELSON.md。",
             "Find out how this project's tests are run and write it into KEELSON.md."),
        pick("找一个小 bug 或 TODO，修好并补一个测试。",
             "Find one small bug or TODO, fix it and add a test."),
    ]
    return {"ok": True, "path": str(ws), "name": ws.name or str(ws), "checks": checks, "checks_source": source,
            "keelson_md": has_md, "git": (ws / ".git").exists(), "suggestions": suggestions}


# ── the console server, one per open project ──────────────────


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_command(port: int) -> list[str]:
    args = ["--port", str(port), "--no-open", "--parent-pid", str(os.getpid())]
    if getattr(sys, "frozen", False):           # Keelson.exe runs the server itself (see keelson_desktop.py)
        return [sys.executable, "--console-server", *args]
    return [sys.executable, "-m", "keelson", "web", *args]


class ConsoleServer:
    def __init__(self, project: Path, log_path: Path | None = None) -> None:
        self.project = project
        self.port = free_port()
        self.log_path = log_path or (paths.user_home() / "logs" / "desktop-server.log")
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "ab")  # noqa: SIM115 - handed to the child, closed in stop()
        log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {self.project}\n".encode())
        log.flush()
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "KEELSON_DESKTOP": "1"}
        self._log = log
        self.proc = subprocess.Popen(
            server_command(self.port), cwd=str(self.project), env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **no_window_flags(),
        )

    def wait_ready(self, timeout: float = START_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(self.url + "api/state", timeout=2) as response:
                    if response.status == 200:
                        return True
            except OSError:
                pass
            time.sleep(0.25)
        return False

    def log_tail(self, lines: int = 20) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            kill_process_tree(self.proc.pid)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        log = getattr(self, "_log", None)
        if log is not None:
            log.close()
            self._log = None


# ── what the page can call ────────────────────────────────────


class DesktopApi:
    """Exposed to the page as ``window.pywebview.api``. Public methods only; state is private."""

    def __init__(self, start_folder: str | None = None) -> None:
        self._window = None
        self._server: ConsoleServer | None = None
        self._lock = threading.Lock()
        self._start_folder = start_folder
        self._home_url = ""

    # -- plumbing (not reachable from the page: names start with _) --

    def _attach(self, window) -> None:
        self._window = window

    def _trusted(self) -> bool:
        """Only our own pages may call in: the wizard and the console both live on loopback."""
        if self._window is None:
            return True
        try:
            url = self._window.get_current_url() or ""
        except Exception:
            return False
        return urlparse(url).hostname in _LOOPBACK

    def _guard(self) -> None:
        if not self._trusted():
            raise PermissionError("Keelson's desktop API only answers its own pages.")

    def _shutdown(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
                self._server = None

    # -- the wizard and the project picker --

    def boot(self) -> dict[str, Any]:
        self._guard()
        if self._window is not None and not self._home_url:
            self._home_url = (self._window.get_current_url() or "").split("#")[0]
        from . import __version__
        from .doctor import PRESETS

        cfg = model_config()
        start = self._start_folder
        self._start_folder = None
        if start is None and cfg["configured"]:
            last = load_state().get("last")
            start = last if isinstance(last, str) and Path(last).is_dir() else None
        return {
            "version": __version__, "config": cfg, "recent": recent_projects(),
            "presets": [{"name": p["name"], "base": p["base"], "model": p["model"], "kind": p["kind"]} for p in PRESETS],
            "autostart": start if cfg["configured"] else None,
            "pending_folder": start,
            "current": str(self._server.project) if self._server else None,
            "config_path": str(config_target()),
        }

    def test_model(self, preset: int, base: str, model: str, key: str) -> dict[str, Any]:
        self._guard()
        import asyncio

        from .doctor import PRESETS, _ping

        if not (0 <= int(preset) < len(PRESETS)) or not key.strip() or not model.strip():
            return {"ok": False, "detail": pick("请填写模型名和 API Key。", "Fill in the model name and the API key.")}
        kind = PRESETS[int(preset)]["kind"]
        base = base.strip() or PRESETS[int(preset)]["base"]
        ok, detail = asyncio.run(_ping(base, key.strip(), _uses_openai_protocol(kind, base), model.strip()))
        return {"ok": ok, "detail": detail}

    def save_model(self, preset: int, base: str, model: str, key: str) -> dict[str, Any]:
        self._guard()
        from .doctor import PRESETS, render_env

        if not (0 <= int(preset) < len(PRESETS)) or not key.strip() or not model.strip():
            return {"ok": False, "detail": pick("请填写模型名和 API Key。", "Fill in the model name and the API key.")}
        chosen = PRESETS[int(preset)]
        target = config_target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_env(chosen, base.strip() or chosen["base"], model.strip(), key.strip()),
                          encoding="utf-8", newline="\n")
        # The running console still has the old model: it restarts on the next open.
        with self._lock:
            if self._server is not None:
                self._server.stop()
                self._server = None
        return {"ok": True, "path": str(target), "config": model_config()}

    def choose_folder(self) -> str | None:
        self._guard()
        import webview

        start = load_state().get("last") or str(Path.home())
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not picked:
            return None
        return picked[0] if isinstance(picked, (list, tuple)) else str(picked)

    def inspect(self, path: str) -> dict[str, Any]:
        self._guard()
        return inspect_project(path)

    def save_checks(self, path: str) -> dict[str, Any]:
        """Write the detected check commands to the project's KEELSON.md."""
        self._guard()
        from . import verify

        ws = Path(path)
        detected = verify.detect(ws)
        if not detected:
            return {"ok": False, "detail": pick("没有识别出测试命令。", "No test command was found.")}
        written = verify.write_verify_section(detected, ws)
        return {"ok": True, "path": str(written), "checks": detected}

    def forget(self, path: str) -> list[dict[str, Any]]:
        self._guard()
        forget_project(path)
        return recent_projects()

    def open_project(self, path: str, draft: str = "") -> dict[str, Any]:
        """Start the console in ``path`` and show it. Blocks until the server answers (or fails)."""
        self._guard()
        project = Path(path)
        if not project.is_dir():
            return {"ok": False, "error": pick("文件夹不存在：", "No such folder: ") + path}
        if not model_config()["configured"]:
            return {"ok": False, "error": pick("还没有配置模型。", "No model is configured yet.")}
        with self._lock:
            running = self._server
            if running is not None and running.project == project.resolve() and running.proc is not None \
                    and running.proc.poll() is None:
                server = running            # already open: just go back to it
            else:
                if running is not None:
                    running.stop()
                self._server = None
                server = ConsoleServer(project.resolve())
                server.start()
                if not server.wait_ready():
                    tail = server.log_tail()
                    server.stop()
                    return {"ok": False, "error": pick("控制台没有启动成功。", "The console did not start."), "log": tail}
            self._server = server
        remember_project(str(project.resolve()))
        target = server.url + "#/console" + (f"?draft={quote(draft)}" if draft else "")
        if self._window is not None:
            self._window.set_title(f"Keelson — {project.name}")
        # The page navigates itself: if we called load_url here, the page waiting for this
        # return value would already be gone and pywebview would log a JavascriptException.
        return {"ok": True, "url": target}

    def show_projects(self) -> str:
        """From the console: the project picker's address (the current project keeps running until another opens)."""
        self._guard()
        return self._home_url + "#projects" if self._home_url else ""

    def project(self) -> dict[str, Any] | None:
        self._guard()
        if self._server is None:
            return None
        return {"path": str(self._server.project), "name": self._server.project.name}


# ── entry point ───────────────────────────────────────────────


def _quiet_streams() -> None:
    """A windowed exe has no stdout/stderr; anything printed would raise. Send it to a log."""
    if sys.stdout is None or sys.stderr is None:
        log_dir = paths.user_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="keelson desktop", description="Keelson in its own window")
    parser.add_argument("folder", nargs="?", help="Project folder to open (default: the last one)")
    parser.add_argument("--debug", action="store_true", help="Open the WebView developer tools")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    _quiet_streams()

    try:
        import webview
    except ImportError:
        print('The desktop app needs pywebview. Install it with:\n\n    pip install -e ".[desktop]"\n')
        return 1

    folder = str(Path(args.folder).resolve()) if args.folder else None
    api = DesktopApi(folder)
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = False
    window = webview.create_window(
        "Keelson", url=str(static_dir() / "desktop.html"), js_api=api,
        width=1320, height=860, min_size=(960, 620), background_color="#f6f5f1", text_select=True,
    )
    api._attach(window)
    window.events.closed += api._shutdown
    storage = paths.user_home() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(http_server=True, private_mode=False, storage_path=str(storage), debug=args.debug)
    finally:
        api._shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

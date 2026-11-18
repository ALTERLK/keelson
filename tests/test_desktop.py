"""Phase 6: the desktop app's launcher, and the console's review / undo / ledger / plan endpoints."""

from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from keelson import checkpoints, desktop, events  # noqa: E402
from keelson.agent import Agent  # noqa: E402
from keelson.platforms.process import pid_alive  # noqa: E402
from keelson.web.server import SessionManager, create_app  # noqa: E402

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ─── plan approval from the console ─────────────────────────


class PlanAgent:
    """Ends plan mode by asking for approval, like the real exit_plan_mode tool."""

    model = "stub-model"

    def __init__(self):
        self.session_id = uuid.uuid4().hex[:8]
        self.permission_mode = "plan"
        self.answer = None

    def set_confirm_fn(self, fn):
        self.confirm_fn = fn

    def set_plan_approval_fn(self, fn):
        self.plan_fn = fn

    def abort(self):
        pass

    async def chat(self, message):
        events.emit(events.USER_MESSAGE, text=message)
        self.answer = await self.plan_fn("1. read\n2. change parser.py")
        events.emit(events.TURN_END)


@pytest.mark.parametrize("choice", ["execute", "keep-planning"])
def test_plan_mode_waits_for_the_consoles_decision(in_tmp_cwd, choice):
    manager = SessionManager(PlanAgent)
    with TestClient(create_app(manager)) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        session = manager.get(sid)
        client.post(f"/api/sessions/{sid}/chat", json={"message": "plan the refactor"})
        assert _wait_for(lambda: any(e.type == events.PLAN_REQUEST for e in session.bus.history()))
        request = next(e for e in session.bus.history() if e.type == events.PLAN_REQUEST)
        assert request.data["plan"].startswith("1. read")

        bad = client.post(f"/api/sessions/{sid}/plan", json={"request_id": request.data["request_id"], "choice": "yolo"})
        assert bad.status_code == 404
        ok = client.post(f"/api/sessions/{sid}/plan", json={"request_id": request.data["request_id"], "choice": choice,
                                                              "feedback": "split step 2"})
        assert ok.status_code == 200
        assert _wait_for(lambda: not session.busy)
        assert session.agent.answer["choice"] == choice
        assert session.agent.answer["feedback"] == "split step 2"
        resolved = next(e for e in session.bus.history() if e.type == events.PLAN_RESOLVED)
        assert resolved.data["choice"] == choice
    manager.close_all()


# ─── review, undo, ledger with a real agent ─────────────────


def _final(text="done"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cache_hit_tokens": 8}}


def _call(call_id, name, **arguments):
    import json

    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    ]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _agent_factory(monkeypatch, responses):
    def build():
        agent = Agent(permission_mode="acceptEdits", model="deepseek-v4-flash", api_base="https://x/v1", api_key="k")
        queue = iter(responses)

        async def fake_model():
            return next(queue)

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
        monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
        monkeypatch.setattr(agent, "_check_and_compact", noop)
        monkeypatch.setattr(agent, "_build_side_query", lambda **kwargs: None)
        return agent

    return build


@needs_git
def test_review_undo_redo_and_revert_from_the_console(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("KEELSON_CHECKPOINTS", "1")
    checkpoints.reset()
    (work / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    manager = SessionManager(_agent_factory(monkeypatch, [
        _call("c1", "read_file", file_path="calc.py"),
        _call("c2", "write_file", file_path="calc.py", content="def add(a, b):\n    return a + b\n"),
        _final("Fixed."),
    ]))
    with TestClient(create_app(manager)) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        session = manager.get(sid)
        client.post(f"/api/sessions/{sid}/chat", json={"message": "fix add. Never change the tests."})
        assert _wait_for(lambda: not session.busy and session.turns == 1, timeout=20)

        review = client.get(f"/api/sessions/{sid}/review").json()
        assert review["available"] and review["covers_shell"]
        assert review["files"] == ["calc.py"]
        assert "-    return a - b" in review["diff"] and "+    return a + b" in review["diff"]
        assert review["turns"][0]["label"].startswith("fix add")

        ledger = client.get(f"/api/sessions/{sid}/ledger").json()
        assert ledger["constraints"][0]["text"] == "Never change the tests."
        assert client.post(f"/api/sessions/{sid}/ledger", json={"action": "add", "text": "Keep Python 3.10."}).status_code == 200
        dropped = client.post(f"/api/sessions/{sid}/ledger", json={"action": "drop", "index": 0}).json()
        assert [c["text"] for c in dropped["constraints"]] == ["Keep Python 3.10."]
        assert client.post(f"/api/sessions/{sid}/ledger", json={"action": "drop", "index": 9}).status_code == 422

        undone = client.post(f"/api/sessions/{sid}/undo", json={"steps": 1}).json()
        assert undone["ok"] and undone["restored"] == ["calc.py"]
        assert "a - b" in (work / "calc.py").read_text(encoding="utf-8")
        assert any(e.type == events.UNDO for e in session.bus.history())

        assert client.post(f"/api/sessions/{sid}/redo", json={}).json()["ok"]
        assert "a + b" in (work / "calc.py").read_text(encoding="utf-8")

        reverted = client.post(f"/api/sessions/{sid}/revert", json={"file": "calc.py", "steps": 1}).json()
        assert reverted["ok"]
        assert "a - b" in (work / "calc.py").read_text(encoding="utf-8")
        assert client.post(f"/api/sessions/{sid}/revert", json={"file": " "}).status_code == 422
    manager.close_all()


def test_mode_switch_reaches_plan_mode_and_is_refused_mid_turn(monkeypatch, in_tmp_cwd):
    manager = SessionManager(_agent_factory(monkeypatch, []))
    with TestClient(create_app(manager)) as client:
        sid = client.post("/api/sessions", json={}).json()["id"]
        session = manager.get(sid)
        assert client.post(f"/api/sessions/{sid}/mode", json={"mode": "plan"}).status_code == 200
        assert session.agent.permission_mode == "plan"
        assert client.post(f"/api/sessions/{sid}/mode", json={"mode": "acceptEdits"}).status_code == 200
        assert session.agent.permission_mode == "acceptEdits"
        session.busy = True
        assert client.post(f"/api/sessions/{sid}/mode", json={"mode": "default"}).status_code == 409
        assert client.post(f"/api/sessions/{sid}/undo", json={}).status_code == 409
        session.busy = False
        usage = client.get("/api/usage").json()
        assert set(usage) == {"today", "project_today", "week", "budget"}
        assert client.get("/api/state").json()["workspace_path"] == os.getcwd()
    manager.close_all()


def test_history_leaves_out_sessions_nobody_typed_into(in_tmp_cwd, tmp_path):
    import json

    traces = tmp_path / "traces"
    traces.mkdir()
    header = {"type": "trace_header", "data": {"recorded_at": "2026-11-17T10:00:00Z"}}
    session_row = {"t": 0.0, "type": "session", "data": {"id": "x"}}
    message_row = {"t": 0.1, "type": "user_message", "data": {"text": "fix add"}}
    # The desktop app stops its server hard, so an untouched session's file is never cleaned up.
    (traces / "20261117T100000Z-empty.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (header, session_row)) + "\n", encoding="utf-8")
    (traces / "20261117T100100Z-real.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (header, session_row, message_row)) + "\n", encoding="utf-8")
    manager = SessionManager(PlanAgent, traces_dir=traces)
    assert [row["title"] for row in manager.history()] == ["fix add"]


# ─── the desktop launcher ───────────────────────────────────


def test_recent_projects_are_remembered_newest_first(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    desktop.remember_project(str(a))
    desktop.remember_project(str(b))
    desktop.remember_project(str(a))
    assert [r["name"] for r in desktop.recent_projects()] == ["a", "b"]
    assert desktop.load_state()["last"] == str(a)
    desktop.forget_project(str(a))
    assert [r["name"] for r in desktop.recent_projects()] == ["b"]
    assert desktop.load_state()["last"] is None


def test_model_config_is_read_without_leaking_into_the_environment(monkeypatch):
    for name in ("APIKEY", "API", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                 "KEELSON_API_KEY", "KEELSON_API_BASE", "MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert desktop.model_config()["configured"] is False

    api = desktop.DesktopApi()
    assert api.save_model(0, "", "", "sk-x")["ok"] is False           # a model name is required
    saved = api.save_model(0, "", "deepseek-v4-flash", "sk-test")
    assert saved["ok"] and saved["config"]["configured"] and saved["config"]["model"] == "deepseek-v4-flash"
    text = Path(saved["path"]).read_text(encoding="utf-8")
    assert "APIKEY=sk-test" in text and "API=https://api.deepseek.com" in text
    # The window process must not carry the key: the console server it starts would inherit it.
    assert "APIKEY" not in os.environ and "MODEL" not in os.environ


def test_inspect_project_detects_checks_and_suggests_first_tasks(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    info = desktop.inspect_project(str(tmp_path))
    assert info["ok"] and any("pytest" in c for c in info["checks"]) and len(info["suggestions"]) == 3
    assert desktop.inspect_project(str(tmp_path / "missing"))["ok"] is False
    saved = desktop.DesktopApi().save_checks(str(tmp_path))
    assert saved["ok"] and "## Verify" in (tmp_path / "KEELSON.md").read_text(encoding="utf-8")


def test_the_api_answers_only_loopback_pages():
    class Window:
        url = "http://127.0.0.1:53211/desktop.html"

        def get_current_url(self):
            return self.url

    api = desktop.DesktopApi()
    window = Window()
    api._attach(window)
    assert api._trusted()
    window.url = "https://example.com/"
    assert not api._trusted()
    with pytest.raises(PermissionError):
        api.boot()
    # Only public methods reach the page; plumbing is private.
    public = {n for n in dir(api) if not n.startswith("_")}
    assert public == {"boot", "test_model", "save_model", "choose_folder", "inspect", "save_checks", "forget",
                      "open_project", "show_projects", "project"}


def test_reopening_the_running_project_reuses_its_server_until_the_model_changes(tmp_path, monkeypatch):
    started = []

    class FakeServer:
        def __init__(self, project, log_path=None):
            self.project, self.url, self.stopped = project, "http://127.0.0.1:1/", False
            self.proc = self

        def poll(self):
            return 0 if self.stopped else None

        def start(self):
            started.append(self)

        def wait_ready(self, timeout=0):
            return True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(desktop, "ConsoleServer", FakeServer)
    monkeypatch.setattr(desktop, "model_config", lambda: {"configured": True, "model": "m", "base": ""})
    monkeypatch.setattr(desktop.DesktopApi, "_guard", lambda self: None)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    api = desktop.DesktopApi()
    assert api.open_project(str(one))["ok"]
    assert api.open_project(str(one))["ok"] and len(started) == 1      # same folder: no restart
    assert api.open_project(str(two))["ok"] and len(started) == 2      # other folder: the old one stops
    assert started[0].stopped and not started[1].stopped
    api.save_model(0, "", "deepseek-v4-flash", "sk-test")
    assert started[1].stopped                                           # a new model needs a new server
    assert api.open_project(str(two))["ok"] and len(started) == 3


def test_pid_alive():
    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    assert not pid_alive(999_999_999 % 4_000_000 + 3_999_999)


def test_the_console_server_starts_in_the_project_and_stops(tmp_path):
    project = tmp_path / "项目 one"
    project.mkdir()
    server = desktop.ConsoleServer(project, log_path=tmp_path / "server.log")
    cmd = desktop.server_command(server.port)
    assert cmd[:4] == [sys.executable, "-m", "keelson", "web"] and "--parent-pid" in cmd
    server.start()
    try:
        assert server.wait_ready(timeout=60), server.log_tail()
        import json
        import urllib.request

        with urllib.request.urlopen(server.url + "api/state", timeout=5) as response:
            state = json.loads(response.read())
        assert state["workspace_path"] == str(project)
        pid = server.proc.pid
    finally:
        server.stop()
    assert _wait_for(lambda: not pid_alive(pid), timeout=10)

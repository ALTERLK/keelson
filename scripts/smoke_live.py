"""End-to-end check of the web console against a real model.

The unit tests cover the server with a stub agent. This script is the manual
counterpart: it starts ``keelson web`` in a scratch workspace and checks, over
plain HTTP and the event stream, the three things that need a real run:

1. A long shell command streams its output while it runs, and Stop kills it —
   the child process is gone, not just ignored.
2. Two sessions running at the same time never see each other's events.
3. After the server restarts, a finished session is listed, can be resumed, and
   the model still remembers the conversation.

    python scripts/smoke_live.py            # needs an API key in .env

It makes a handful of small model calls.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

LONG_COMMAND = (
    'python -u -c "import os,time; open(\'child.pid\',\'w\').write(str(os.getpid())); '
    '[(print(\'tick\', i), time.sleep(1)) for i in range(60)]"'
)


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode() if method != "GET" else None
    request = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


class Stream(threading.Thread):
    """Reads one session's event stream into a list."""

    def __init__(self, session_id: str) -> None:
        super().__init__(daemon=True)
        self.session_id = session_id
        self.events: list[dict] = []
        self._answered: set[str] = set()
        self.start()

    def run(self) -> None:
        try:
            with urllib.request.urlopen(f"{BASE}/api/sessions/{self.session_id}/events", timeout=600) as response:
                for raw in response:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        self.events.append(json.loads(line[6:]))
        except Exception:
            pass  # the server went away; whatever was read stays

    def approve_pending(self) -> None:
        """Answer this session's open permission prompts with Allow."""
        resolved = {event["data"]["request_id"] for event in self.of("confirm_resolved")}
        for event in self.of("confirm_request"):
            request_id = event["data"]["request_id"]
            if request_id not in resolved and request_id not in self._answered:
                self._answered.add(request_id)
                call("POST", f"/api/sessions/{self.session_id}/confirm", {"request_id": request_id, "allow": True})

    def of(self, kind: str) -> list[dict]:
        return [event for event in list(self.events) if event["type"] == kind]

    def wait_for(self, predicate, timeout: float, what: str):
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = predicate()
            if found:
                return found
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for {what}")


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, errors="replace").stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_server(workspace: Path, home: Path) -> subprocess.Popen:
    from dotenv import dotenv_values

    # The server runs in the scratch workspace, so hand it the repo's .env
    # through the environment rather than copying a file with a key in it.
    env = {**os.environ, **{k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}}
    env.update(KEELSON_HOME=str(home), KEELSON_AUTO_SKILL_EVOLUTION="0", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    server = subprocess.Popen([sys.executable, "-m", "agents.main", "web", "--no-open", "--port", str(PORT)],
                              cwd=workspace, env=env)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if call("GET", "/api/state")["live"]:
                return server
            raise SystemExit("the server came up in replay mode: is there an API key in .env?")
        except OSError:
            time.sleep(0.3)
    server.kill()
    raise SystemExit("the server did not start")


def stop_server(server: subprocess.Popen) -> None:
    server.terminate()
    try:
        server.wait(10)
    except subprocess.TimeoutExpired:
        server.kill()


def main() -> int:
    if not (ROOT / ".env").exists():
        raise SystemExit("no .env: run `keelson init` first")
    sys.path.insert(0, str(ROOT))
    base = Path(tempfile.mkdtemp(prefix="keelson-smoke-"))
    workspace, home = base / "workspace", base / "home"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("The launch code word is HERON.\n", encoding="utf-8")

    server = start_server(workspace, home)
    try:
        # -- 1 and 2: a long command in session A while session B has its own conversation
        a = call("POST", "/api/sessions")["id"]
        b = call("POST", "/api/sessions")["id"]
        stream_a, stream_b = Stream(a), Stream(b)

        call("POST", f"/api/sessions/{a}/chat", {"message": f"Run exactly this shell command and nothing else:\n{LONG_COMMAND}"})
        call("POST", f"/api/sessions/{b}/chat", {"message": "Read notes.txt and tell me the code word."})

        def running():
            stream_a.approve_pending()
            stream_b.approve_pending()
            return len(stream_a.of("tool_output")) >= 3

        stream_a.wait_for(running, 90, "live output from the running command")
        pid = int((workspace / "child.pid").read_text())
        assert pid_alive(pid), "the command should still be running"
        first_output_at = stream_a.of("tool_output")[0]["ts"]

        call("POST", f"/api/sessions/{a}/abort")
        stream_a.wait_for(lambda: stream_a.of("turn_end"), 20, "the turn to end after Stop")
        stopped_after = time.time() - first_output_at
        time.sleep(1.0)
        assert not pid_alive(pid), "Stop must kill the child process"
        print(f"1. streaming + stop: {len(stream_a.of('tool_output'))} output events while running; "
              f"child {pid} is gone ({stopped_after:.1f}s after its first line, of a 60s command)")

        stream_b.wait_for(lambda: stream_b.approve_pending() or stream_b.of("turn_end"), 120, "session B to finish")
        a_calls = {event["data"]["id"] for event in stream_a.of("tool_call")}
        b_calls = {event["data"]["id"] for event in stream_b.of("tool_call")}
        assert a_calls and b_calls and not (a_calls & b_calls), "tool calls must not cross sessions"
        assert not stream_b.of("tool_output"), "session B must not see session A's command output"
        assert all("tick" not in json.dumps(event) for event in stream_b.events)
        assert "HERON" in "".join(event["data"]["text"] for event in stream_b.of("assistant_delta"))
        print(f"2. isolation: A saw {len(stream_a.events)} events, B saw {len(stream_b.events)}; no tool call or output crossed over")

        # -- 3: restart, then resume session B
        stop_server(server)
        server = start_server(workspace, home)
        history = call("GET", "/api/sessions")["history"]
        saved = next(row for row in history if row["id"].endswith(b))
        assert saved["resumable"], "the finished session should be resumable"
        resumed = call("POST", "/api/sessions", {"resume": saved["id"]})
        stream_c = Stream(resumed["id"])
        stream_c.wait_for(lambda: stream_c.of("turn_end"), 10, "the earlier conversation to be replayed")
        replayed = len(stream_c.events)
        assert len(stream_c.of("user_message")) == 1 and not stream_c.of("confirm_request")
        call("POST", f"/api/sessions/{resumed['id']}/chat", {"message": "Without reading any file: what was the code word?"})
        stream_c.wait_for(lambda: stream_c.approve_pending() or len(stream_c.of("turn_end")) >= 2, 120,
                          "the resumed session to answer")
        new_events = stream_c.events[replayed:]
        answer = "".join(event["data"]["text"] for event in new_events if event["type"] == "assistant_delta")
        assert "HERON" in answer, f"the model should remember the code word; it said: {answer[-200:]!r}"
        assert not [event for event in new_events if event["type"] == "tool_call"], "it should answer from memory"
        print(f"3. resume after restart: {len(history)} saved sessions listed; the resumed one replayed the old turn "
              "and the model recalled the code word without reading the file again")
        print("\nall checks passed")
        return 0
    finally:
        stop_server(server)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

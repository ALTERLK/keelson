"""The web console's HTTP layer, driven with a stand-in agent."""

from __future__ import annotations

import time
import uuid

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from keelson import events  # noqa: E402
from keelson.web import data  # noqa: E402
from keelson.web.server import SessionManager, _sse, create_app  # noqa: E402


class StubAgent:
    """Publishes what a real turn would, and asks the gate once."""

    model = "stub-model"

    def __init__(self):
        self.session_id = uuid.uuid4().hex[:8]
        self.permission_mode = "default"
        self.aborted = False
        self.restored = None

    def set_confirm_fn(self, fn):
        self.confirm_fn = fn

    def abort(self):
        self.aborted = True

    def restore_session(self, saved):
        self.restored = saved

    async def chat(self, message):
        # Like the real loop, this publishes through events.emit(): whichever
        # bus is current in this task's context receives it.
        events.emit(events.USER_MESSAGE, text=message)
        allowed = await self.confirm_fn("rm -rf build")
        events.emit(events.ASSISTANT_DELTA, text=f"{message}: allowed={allowed}")
        events.emit(events.TURN_END)


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _types(session):
    return [e.type for e in session.bus.history()]


def _pending(session):
    return next((e for e in reversed(session.bus.history()) if e.type == events.CONFIRM_REQUEST), None)


@pytest.fixture
def live(in_tmp_cwd):
    manager = SessionManager(StubAgent, traces_dir=in_tmp_cwd / ".keelson" / "traces")
    with TestClient(create_app(manager)) as client:
        yield client, manager
    manager.close_all()


def _new(client):
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def _run_turn(client, manager, session_id, message, allow=True):
    session = manager.get(session_id)
    assert client.post(f"/api/sessions/{session_id}/chat", json={"message": message}).status_code == 200
    assert _wait_for(lambda: _pending(session) is not None and session.busy)
    request_id = _pending(session).data["request_id"]
    assert client.post(f"/api/sessions/{session_id}/confirm", json={"request_id": request_id, "allow": allow}).status_code == 200
    assert _wait_for(lambda: not session.busy)
    return session


# ─── replay mode: no agent behind the server ────────────────


def test_replay_mode_serves_views_but_cannot_start_sessions(in_tmp_cwd):
    client = TestClient(create_app(SessionManager()))
    state = client.get("/api/state").json()
    assert state["live"] is False and state["sessions"] == []
    assert client.get("/").status_code == 200
    assert client.post("/api/sessions", json={}).status_code == 409


# ─── a live turn, including the permission round trip ───────


@pytest.mark.parametrize("decision", [True, False])
def test_a_turn_waits_on_the_browser_for_the_permission_decision(live, decision):
    client, manager = live
    session_id = _new(client)
    session = manager.get(session_id)
    client.post(f"/api/sessions/{session_id}/chat", json={"message": "clean up"})

    assert _wait_for(lambda: _pending(session) is not None)
    request = _pending(session)
    assert request.data["message"] == "rm -rf build"
    assert client.get(f"/api/sessions/{session_id}").json()["busy"] is True   # parked on the gate
    assert client.post(f"/api/sessions/{session_id}/chat", json={"message": "again"}).status_code == 409

    client.post(f"/api/sessions/{session_id}/confirm", json={"request_id": request.data["request_id"], "allow": decision})
    assert _wait_for(lambda: not session.busy)

    resolved = next(e for e in session.bus.history() if e.type == events.CONFIRM_RESOLVED)
    assert resolved.data == {"request_id": request.data["request_id"], "allowed": decision}
    reply = next(e for e in session.bus.history() if e.type == events.ASSISTANT_DELTA)
    assert reply.data["text"] == f"clean up: allowed={decision}"


def test_an_unknown_confirmation_or_session_is_rejected(live):
    client, _ = live
    session_id = _new(client)
    assert client.post(f"/api/sessions/{session_id}/confirm", json={"request_id": "nope", "allow": True}).status_code == 404
    assert client.post("/api/sessions/does-not-exist/chat", json={"message": "hi"}).status_code == 404


def test_a_crashing_turn_still_ends_the_turn_for_the_browser(in_tmp_cwd):
    class Crashing(StubAgent):
        async def chat(self, message):
            raise RuntimeError("model endpoint unreachable")

    manager = SessionManager(Crashing, traces_dir=None)
    with TestClient(create_app(manager)) as client:
        session_id = _new(client)
        session = manager.get(session_id)
        client.post(f"/api/sessions/{session_id}/chat", json={"message": "hi"})
        assert _wait_for(lambda: events.TURN_END in _types(session))
    notice = next(e for e in session.bus.history() if e.type == events.NOTICE)
    assert (notice.data["level"], notice.data["message"]) == ("error", "model endpoint unreachable")
    assert session.busy is False


def test_stopping_a_turn_denies_its_pending_prompt(live):
    client, manager = live
    session_id = _new(client)
    session = manager.get(session_id)
    client.post(f"/api/sessions/{session_id}/chat", json={"message": "clean up"})
    assert _wait_for(lambda: _pending(session) is not None)

    assert client.post(f"/api/sessions/{session_id}/abort", json={}).status_code == 200
    assert _wait_for(lambda: not session.busy)
    assert session.agent.aborted is True
    assert next(e for e in session.bus.history() if e.type == events.CONFIRM_RESOLVED).data["allowed"] is False


def test_empty_messages_are_refused(live):
    client, _ = live
    assert client.post(f"/api/sessions/{_new(client)}/chat", json={"message": "   "}).status_code == 422


# ─── sessions are isolated from each other ──────────────────


def test_two_sessions_run_at_once_without_seeing_each_other(live):
    client, manager = live
    first, second = _new(client), _new(client)
    a, b = manager.get(first), manager.get(second)

    # Both turns are in flight at the same time, each parked on its own prompt.
    client.post(f"/api/sessions/{first}/chat", json={"message": "alpha"})
    client.post(f"/api/sessions/{second}/chat", json={"message": "beta"})
    assert _wait_for(lambda: _pending(a) is not None and _pending(b) is not None)
    assert a.busy and b.busy

    # Answering one must not release the other.
    client.post(f"/api/sessions/{first}/confirm", json={"request_id": _pending(a).data["request_id"], "allow": True})
    assert _wait_for(lambda: not a.busy)
    assert b.busy
    # One session's prompt id means nothing to another session.
    stolen = client.post(f"/api/sessions/{first}/confirm", json={"request_id": _pending(b).data["request_id"], "allow": True})
    assert stolen.status_code == 404
    client.post(f"/api/sessions/{second}/confirm", json={"request_id": _pending(b).data["request_id"], "allow": False})
    assert _wait_for(lambda: not b.busy)

    text = {s.id: "".join(e.data.get("text", "") for e in s.bus.history() if e.type == events.ASSISTANT_DELTA) for s in (a, b)}
    assert text[first] == "alpha: allowed=True"
    assert text[second] == "beta: allowed=False"
    # Nothing leaked onto the process-wide bus either.
    leaked = [e for e in events.bus.history() if e.type == events.ASSISTANT_DELTA and "allowed=" in e.data.get("text", "")]
    assert leaked == []


def test_permission_mode_is_per_session_and_cannot_switch_the_gate_off(live):
    client, manager = live
    first, second = _new(client), _new(client)
    assert client.post(f"/api/sessions/{first}/mode", json={"mode": "bypassPermissions"}).status_code == 422
    assert client.post(f"/api/sessions/{first}/mode", json={"mode": "dontAsk"}).json()["permission_mode"] == "dontAsk"
    assert manager.get(first).agent.permission_mode == "dontAsk"
    assert manager.get(second).agent.permission_mode == "default"


def test_the_least_recently_used_idle_session_is_retired_at_the_limit(in_tmp_cwd):
    manager = SessionManager(StubAgent, traces_dir=None, max_sessions=2)
    with TestClient(create_app(manager)) as client:
        first, second = _new(client), _new(client)
        manager.get(first).last_active += 100      # make `second` the older of the two
        third = _new(client)
        assert {s.id for s in manager.sessions()} == {first, third}
        assert client.get(f"/api/sessions/{second}").status_code == 404


def test_no_new_session_while_every_slot_is_busy(in_tmp_cwd):
    manager = SessionManager(StubAgent, traces_dir=None, max_sessions=1)
    with TestClient(create_app(manager)) as client:
        only = _new(client)
        client.post(f"/api/sessions/{only}/chat", json={"message": "busy"})
        assert _wait_for(lambda: manager.get(only).busy)
        assert client.post("/api/sessions", json={}).status_code == 409
    manager.close_all()


# ─── history: a session survives the server ─────────────────


def test_a_finished_session_is_listed_and_can_be_resumed_by_a_new_server(in_tmp_cwd, monkeypatch):
    traces = in_tmp_cwd / ".keelson" / "traces"
    saved_messages = {"openaiMessages": [{"role": "user", "content": "remember: the port is 8765"}]}
    monkeypatch.setattr("keelson.session.load_session", lambda session_id: saved_messages)

    # First server process: one turn, then it shuts down.
    manager = SessionManager(StubAgent, traces_dir=traces)
    with TestClient(create_app(manager)) as client:
        original = _new(client)
        _run_turn(client, manager, original, "the port is 8765")
    manager.close_all()

    # A new process over the same workspace finds it under history...
    manager = SessionManager(StubAgent, traces_dir=traces)
    with TestClient(create_app(manager)) as client:
        history = client.get("/api/sessions").json()["history"]
        assert len(history) == 1
        assert history[0]["title"] == "the port is 8765" and history[0]["resumable"] is True

        # ...and resuming it brings back both the visible conversation and the model's memory.
        resumed_id = client.post("/api/sessions", json={"resume": history[0]["id"]}).json()["id"]
        resumed = manager.get(resumed_id)
        assert resumed_id != original
        replayed = "".join(e.data.get("text", "") for e in resumed.bus.history() if e.type == events.ASSISTANT_DELTA)
        assert replayed == "the port is 8765: allowed=True"
        assert resumed.agent.restored["openaiMessages"] == saved_messages["openaiMessages"]
        assert resumed.describe()["resumed_from"] == history[0]["id"]

        assert client.post("/api/sessions", json={"resume": "no-such-trace"}).status_code == 404
    manager.close_all()


def test_a_live_session_is_not_listed_as_history(live):
    client, manager = live
    session_id = _new(client)
    _run_turn(client, manager, session_id, "hello")
    listing = client.get("/api/sessions").json()
    assert [s["id"] for s in listing["live"]] == [session_id]
    assert listing["history"] == []
    # The recording is readable through the trace endpoint while the session is live.
    trace = client.get(f"/api/traces/{manager.get(session_id).recorder.path.stem}").json()
    assert events.USER_MESSAGE in [e["type"] for e in trace["events"]]
    assert client.get("/api/traces/..%2Fsecret").status_code == 404


# ─── same-origin guard ──────────────────────────────────────


def test_cross_origin_posts_are_refused(live):
    client, manager = live
    session_id = _new(client)
    response = client.post(f"/api/sessions/{session_id}/chat", json={"message": "x"}, headers={"origin": "http://evil.example"})
    assert response.status_code == 403
    assert manager.get(session_id).busy is False


def test_form_posts_are_refused(live):
    # A plain HTML form can POST cross-site without a preflight; JSON cannot.
    client, _ = live
    response = client.post("/api/sessions", content="resume=x", headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 415


def test_requests_for_another_host_name_are_refused(live):
    # DNS rebinding: a hostile name resolving to 127.0.0.1 still sends its own Host header.
    client, _ = live
    assert client.get("/api/state", headers={"host": "attacker.example"}).status_code == 403


# ─── event stream framing ───────────────────────────────────


def test_sse_frames_carry_the_sequence_number_as_the_event_id():
    event = events.Event(seq=42, ts=1.5, type=events.NOTICE, data={"message": "多字节 ok"})
    frame = _sse(event)
    assert frame.startswith("id: 42\ndata: ")
    assert frame.endswith("\n\n")
    assert "多字节 ok" in frame  # not \\u-escaped: smaller and readable on the wire


# ─── read-only views ────────────────────────────────────────


def test_benchmark_view_matches_the_committed_run_artifacts(in_tmp_cwd):
    bench = TestClient(create_app(SessionManager())).get("/api/bench").json()
    if not bench["available"]:
        pytest.skip("bench artifacts not present in this install")
    fix = bench["toolhop"]["fix"]
    # The headline numbers in bench/REPORT.md, recomputed from results.jsonl.
    assert round(fix["before"]["accuracy"], 3) == 0.728
    assert round(fix["after"]["accuracy"], 3) == 0.794
    assert fix["after"]["hit_cap_rate"] == 0.0
    assert fix["delta"]["significant"] is True
    assert [round(cell["solve_rate"], 3) for cell in bench["sandbox"]] == [1.0, 1.0, 1.0, 0.929]


def test_evolution_view_has_one_admitted_and_one_quarantined_lineage(in_tmp_cwd):
    lineages = data.evolution_lineages()
    if not lineages:
        pytest.skip("self-evolution demo artifacts not present in this install")
    assert sorted(lineage["outcome"] for lineage in lineages) == ["admit", "quarantine"]
    rejected = next(lineage for lineage in lineages if lineage["outcome"] == "quarantine")
    assert rejected["gate"]["new"]["hard_failures"] > rejected["gate"]["old"]["hard_failures"]
    assert "REJECTED" in rejected["card"]


def test_span_summary_reports_percentiles_and_excludes_human_wait_from_shares():
    rows = [{"type": "span", "data": {"name": "model_stream", "dur_ms": ms}} for ms in (100, 200, 300, 400, 1000)]
    rows += [{"type": "span", "data": {"name": "tool", "dur_ms": 50}}, {"type": "tool_call", "data": {}}]
    summary = data.summarize_spans(rows)
    assert summary["model_stream"] == {"count": 5, "total_ms": 2000.0, "p50_ms": 300.0, "p95_ms": 1000.0, "max_ms": 1000.0}
    assert summary["tool"]["count"] == 1

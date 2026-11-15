"""The web console's HTTP layer, driven with a stand-in agent."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from agents import events  # noqa: E402
from agents.web import data  # noqa: E402
from agents.web.recorder import TraceRecorder  # noqa: E402
from agents.web.server import Session, _sse, create_app  # noqa: E402


class StubAgent:
    """Publishes what a real turn would, and asks the gate once."""

    model = "stub-model"
    permission_mode = "default"
    session_id = "stub"

    def __init__(self):
        self.cleared = False
        self.aborted = False

    def set_confirm_fn(self, fn):
        self.confirm_fn = fn

    def abort(self):
        self.aborted = True

    def clear_history(self):
        self.cleared = True

    async def chat(self, message):
        events.emit(events.USER_MESSAGE, text=message)
        allowed = await self.confirm_fn("rm -rf build")
        events.emit(events.ASSISTANT_DELTA, text=f"allowed={allowed}")
        events.emit(events.TURN_END)


@pytest.fixture
def collected():
    seen: list[events.Event] = []
    unsubscribe = events.bus.subscribe(seen.append)
    yield seen
    unsubscribe()


@pytest.fixture
def live(in_tmp_cwd):
    session = Session(StubAgent())
    with TestClient(create_app(session)) as client:
        yield client, session


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ─── replay mode: no agent behind the server ────────────────


def test_replay_mode_serves_views_but_refuses_actions(in_tmp_cwd):
    client = TestClient(create_app(Session()))
    state = client.get("/api/state").json()
    assert state["live"] is False
    assert client.get("/").status_code == 200
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 409
    assert client.post("/api/reset", json={}).status_code == 409


# ─── a live turn, including the permission round trip ───────


@pytest.mark.parametrize("decision", [True, False])
def test_a_turn_waits_on_the_browser_for_the_permission_decision(live, collected, decision):
    client, session = live
    assert client.post("/api/chat", json={"message": "clean up"}).json() == {"ok": True}

    assert _wait_for(lambda: any(e.type == events.CONFIRM_REQUEST for e in collected))
    request = next(e for e in collected if e.type == events.CONFIRM_REQUEST)
    assert request.data["message"] == "rm -rf build"
    assert client.get("/api/state").json()["busy"] is True       # the loop is parked on the gate
    assert client.post("/api/chat", json={"message": "again"}).status_code == 409

    assert client.post("/api/confirm", json={"request_id": request.data["request_id"], "allow": decision}).status_code == 200
    assert _wait_for(lambda: not session.busy)

    resolved = next(e for e in collected if e.type == events.CONFIRM_RESOLVED)
    assert resolved.data == {"request_id": request.data["request_id"], "allowed": decision}
    reply = next(e for e in collected if e.type == events.ASSISTANT_DELTA)
    assert reply.data["text"] == f"allowed={decision}"


def test_an_unknown_or_already_answered_confirmation_is_rejected(live):
    client, _ = live
    assert client.post("/api/confirm", json={"request_id": "nope", "allow": True}).status_code == 404


def test_a_crashing_turn_still_ends_the_turn_for_the_browser(in_tmp_cwd, collected):
    class Crashing(StubAgent):
        async def chat(self, message):
            raise RuntimeError("model endpoint unreachable")

    session = Session(Crashing())
    with TestClient(create_app(session)) as client:
        client.post("/api/chat", json={"message": "hi"})
        assert _wait_for(lambda: any(e.type == events.TURN_END for e in collected))
    notice = next(e for e in collected if e.type == events.NOTICE)
    assert (notice.data["level"], notice.data["message"]) == ("error", "model endpoint unreachable")
    assert session.busy is False


def test_reset_starts_a_new_stream_position(live):
    client, session = live
    events.emit(events.NOTICE, level="info", message="from the old session")
    assert client.post("/api/reset", json={}).status_code == 200
    assert session.agent.cleared is True
    # A page loaded after the reset must not be sent the old session's events.
    assert all(e.data.get("message") != "from the old session" for e in events.bus.history(session.since))


def test_empty_messages_are_refused(live):
    client, _ = live
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422


# ─── permission mode switching ──────────────────────────────


def test_the_console_cannot_switch_the_gate_off(live):
    client, session = live
    assert client.post("/api/mode", json={"mode": "bypassPermissions"}).status_code == 422
    assert session.agent.permission_mode == "default"
    assert client.post("/api/mode", json={"mode": "dontAsk"}).json()["permission_mode"] == "dontAsk"
    assert session.agent.permission_mode == "dontAsk"


# ─── same-origin guard ──────────────────────────────────────


def test_cross_origin_posts_are_refused(live):
    client, session = live
    response = client.post("/api/chat", json={"message": "x"}, headers={"origin": "http://evil.example"})
    assert response.status_code == 403
    assert session.busy is False


def test_form_posts_are_refused(live):
    # A plain HTML form can POST cross-site without a preflight; JSON cannot.
    client, _ = live
    response = client.post("/api/chat", content="message=x", headers={"content-type": "application/x-www-form-urlencoded"})
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


# ─── recorded sessions and read-only views ──────────────────


def test_a_live_session_is_recorded_and_listed(in_tmp_cwd, collected):
    recorder = TraceRecorder(in_tmp_cwd / ".keelson" / "traces" / "session.jsonl", meta={"model": "stub-model"})
    session = Session(StubAgent(), recorder=recorder)
    with TestClient(create_app(session)) as client:
        client.post("/api/chat", json={"message": "hello"})
        assert _wait_for(lambda: any(e.type == events.CONFIRM_REQUEST for e in collected))
        request_id = next(e for e in collected if e.type == events.CONFIRM_REQUEST).data["request_id"]
        client.post("/api/confirm", json={"request_id": request_id, "allow": True})
        assert _wait_for(lambda: not session.busy)

        listed = {row["id"]: row for row in client.get("/api/traces").json()}
        assert listed["session"]["title"] == "hello"
        trace = client.get("/api/traces/session").json()
        assert [e["type"] for e in trace["events"]][:2] == [events.USER_MESSAGE, events.CONFIRM_REQUEST]
        assert client.get("/api/traces/..%2Fsecret").status_code == 404
    session.close()


def test_benchmark_view_matches_the_committed_run_artifacts(in_tmp_cwd):
    bench = TestClient(create_app(Session())).get("/api/bench").json()
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

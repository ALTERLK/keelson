"""The event bus, and the agent loop publishing onto it."""

from __future__ import annotations

import pytest

from agents import events, ui
from agents.agent import Agent
from agents.events import EventBus


@pytest.fixture
def collected():
    """Every event published on the process-wide bus during the test."""
    seen: list[events.Event] = []
    unsubscribe = events.bus.subscribe(seen.append)
    yield seen
    unsubscribe()


# ─── the bus itself ─────────────────────────────────────────


def test_events_are_delivered_in_publish_order_with_increasing_seq():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    for i in range(5):
        bus.emit("tick", i=i)
    assert [e.data["i"] for e in seen] == [0, 1, 2, 3, 4]
    assert [e.seq for e in seen] == [1, 2, 3, 4, 5]


def test_a_failing_subscriber_does_not_break_the_producer_or_other_subscribers():
    bus = EventBus()
    seen = []

    def broken(event):
        raise RuntimeError("consumer went away")

    bus.subscribe(broken)
    bus.subscribe(seen.append)
    bus.emit("tick")  # must not raise
    assert len(seen) == 1


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen = []
    unsubscribe = bus.subscribe(seen.append)
    bus.emit("a")
    unsubscribe()
    bus.emit("b")
    assert [e.type for e in seen] == ["a"]


def test_history_since_returns_only_newer_events():
    bus = EventBus()
    for name in "abcd":
        bus.emit(name)
    assert [e.type for e in bus.history(since=2)] == ["c", "d"]
    assert bus.history(since=bus.last_seq) == []


def test_history_is_a_bounded_ring():
    bus = EventBus(history_size=3)
    for i in range(10):
        bus.emit("tick", i=i)
    assert [e.data["i"] for e in bus.history()] == [7, 8, 9]


def test_payloads_are_coerced_to_json_types():
    bus = EventBus()
    event = bus.emit("x", path=__import__("pathlib").Path("a/b"), items=("a", 1), nested={1: {2}})
    assert event.data == {"path": str(__import__("pathlib").Path("a/b")), "items": ["a", 1], "nested": {"1": [2]}}


# ─── the ui facade publishes instead of drawing ─────────────


def test_ui_facade_publishes_typed_events(collected):
    ui.print_tool_call("read_file", {"file_path": "a.py"}, "c1")
    ui.print_tool_result("read_file", "contents", "c1")
    ui.print_tool_result("run_shell", "Command failed (exit code 1)", "c2")
    ui.print_error("boom")

    call, ok_result, bad_result, notice = collected
    assert (call.type, call.data["id"], call.data["summary"]) == (events.TOOL_CALL, "c1", "a.py")
    assert ok_result.data["ok"] is True and ok_result.data["chars"] == 8
    assert bad_result.data["ok"] is False
    assert (notice.type, notice.data["level"]) == (events.NOTICE, "error")


def test_terminal_renderer_can_be_detached(capsys):
    ui.set_terminal_enabled(False)
    try:
        ui.print_error("should not be drawn")
        assert "should not be drawn" not in capsys.readouterr().out
    finally:
        ui.set_terminal_enabled(True)


# ─── the agent loop publishes the run ───────────────────────


def _tool_calls(*specs):
    return {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
            for call_id, name, args in specs
        ]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }


_FINAL = {"choices": [{"message": {"role": "assistant", "content": "done"}}],
          "usage": {"prompt_tokens": 120, "completion_tokens": 5}}


async def _noop(*args, **kwargs):
    return None


def _scripted_agent(monkeypatch, mode, responses):
    agent = Agent(permission_mode=mode, model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    queue = iter(responses)

    async def fake_model():
        return next(queue)

    async def fake_exec(name, inp):
        return f"result::{name}"

    monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
    monkeypatch.setattr(agent, "_execute_tool_call", fake_exec)
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", _noop)
    monkeypatch.setattr(agent, "_refresh_runtime_system_prompt", lambda: None)
    monkeypatch.setattr(agent, "_build_side_query", lambda **kwargs: None)
    monkeypatch.setattr(agent, "_auto_save", lambda: None)
    return agent


async def test_a_turn_is_published_as_an_ordered_event_sequence(monkeypatch, in_tmp_cwd, collected):
    agent = _scripted_agent(monkeypatch, "default", [
        _tool_calls(("c1", "read_file", '{"file_path": "a.py"}')),
        _FINAL,
    ])

    await agent.chat("go")

    types = [e.type for e in collected if e.type != events.THINKING]
    assert types == [
        events.USER_MESSAGE,
        events.USAGE,
        events.TOOL_CALL, events.PERMISSION, events.TOOL_RESULT,
        events.USAGE, events.USAGE,   # the model call, then the turn's closing cost line
        events.TURN_END,
    ]
    permission = next(e for e in collected if e.type == events.PERMISSION)
    assert permission.data == {"id": "c1", "name": "read_file", "action": "allow", "message": "", "speculative": False}
    usage = [e for e in collected if e.type == events.USAGE][1]
    assert usage.data["input_tokens"] == 220 and usage.data["context_window"] > 0


async def test_a_denied_call_publishes_the_verdict_and_never_reaches_a_tool(monkeypatch, in_tmp_cwd, collected):
    # dontAsk: anything that would need confirmation is denied by the gate itself.
    agent = _scripted_agent(monkeypatch, "dontAsk", [
        _tool_calls(("c1", "run_shell", '{"command": "rm -rf build"}')),
        _FINAL,
    ])

    await agent.chat("clean up")

    verdicts = [e.data["action"] for e in collected if e.type == events.PERMISSION]
    assert verdicts == ["deny"]
    assert not [e for e in collected if e.type == events.TOOL_RESULT]


async def test_a_confirmed_call_publishes_ask_then_the_users_decision(monkeypatch, in_tmp_cwd, collected):
    agent = _scripted_agent(monkeypatch, "default", [
        _tool_calls(("c1", "run_shell", '{"command": "rm -rf build"}')),
        _FINAL,
    ])

    async def deny(message):
        return False

    agent.set_confirm_fn(deny)
    await agent.chat("clean up")

    verdicts = [(e.data["action"], e.data["message"]) for e in collected if e.type == events.PERMISSION]
    assert verdicts == [("confirm", "rm -rf build"), ("deny", "denied by user")]

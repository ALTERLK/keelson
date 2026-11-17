"""On a turn-cap the OpenAI branch must (a) answer every dangling tool_call so
the history stays valid, and (b) make one tool-less call for a best-effort
answer instead of returning nothing."""

from __future__ import annotations

import pytest

from keelson.agent import Agent


def _tc_response(specs):
    return {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": tid, "type": "function", "function": {"name": n, "arguments": a}}
            for tid, n, a in specs
        ]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


async def _anop(*a, **k):
    return None


@pytest.fixture
def agent(monkeypatch):
    a = Agent(permission_mode="dontAsk", model="gpt-4o-mini", api_base="https://x/v1", api_key="k",
              max_turns=2)
    a._openai_messages = [{"role": "system", "content": "s"}]
    monkeypatch.setattr(a, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(a, "_check_and_compact", _anop)
    monkeypatch.setattr(a, "_refresh_runtime_system_prompt", lambda: None)

    async def fake_exec(name, inp):
        return f"result::{name}"
    monkeypatch.setattr(a, "_execute_tool_call", fake_exec)
    return a


def _wrap(v):
    async def _c():
        return v
    return _c()


async def test_dangling_tool_calls_answered_and_final_call_made(agent, monkeypatch):
    # every model turn asks for two tools -> we will hit max_turns=2
    stream = iter([_tc_response([("a", "t", "{}"), ("b", "t", "{}")])] * 5)
    monkeypatch.setattr(agent, "_call_openai_stream", lambda: _wrap(next(stream)))

    final_calls = []

    class _Msg:
        content = "ANSWER: 42"

    class _Resp:
        choices = [type("C", (), {"message": _Msg()})]
        usage = type("U", (), {"prompt_tokens": 3, "completion_tokens": 4})

    async def fake_create(**kwargs):
        final_calls.append(kwargs)
        return _Resp()

    monkeypatch.setattr(agent._openai_client.chat.completions, "create", fake_create)

    await agent._chat_openai("go")

    # no tool_call id left unanswered
    tool_ids = {m["tool_call_id"] for m in agent._openai_messages if m.get("role") == "tool"}
    assert {"a", "b"} <= tool_ids
    # exactly one best-effort finalize call, and it carried tool_choice="none"
    assert len(final_calls) == 1
    assert final_calls[0].get("tool_choice") == "none"
    # its answer was recorded
    assert any(m.get("role") == "assistant" and "ANSWER: 42" in str(m.get("content"))
               for m in agent._openai_messages)


async def test_finalize_falls_back_when_tool_choice_rejected(agent, monkeypatch):
    stream = iter([_tc_response([("a", "t", "{}")])] * 5)
    monkeypatch.setattr(agent, "_call_openai_stream", lambda: _wrap(next(stream)))

    seen = []

    async def fake_create(**kwargs):
        seen.append(kwargs)
        if "tool_choice" in kwargs:
            raise RuntimeError("400 tool_choice not supported")
        return type("R", (), {
            "choices": [type("C", (), {"message": type("M", (), {"content": "ANSWER: x"})()})],
            "usage": type("U", (), {"prompt_tokens": 1, "completion_tokens": 1}),
        })

    monkeypatch.setattr(agent._openai_client.chat.completions, "create", fake_create)
    await agent._chat_openai("go")

    assert len(seen) == 2                          # first with tool_choice (failed), then without
    assert "tool_choice" in seen[0] and "tool_choice" not in seen[1]


async def test_flag_off_skips_the_final_call(agent, monkeypatch):
    monkeypatch.setenv("KEELSON_FINAL_ANSWER_ON_BUDGET", "0")
    stream = iter([_tc_response([("a", "t", "{}")])] * 5)
    monkeypatch.setattr(agent, "_call_openai_stream", lambda: _wrap(next(stream)))

    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        return type("R", (), {"choices": [], "usage": None})

    monkeypatch.setattr(agent._openai_client.chat.completions, "create", fake_create)
    await agent._chat_openai("go")

    assert calls == []                             # no finalize attempt
    # but the dangling tool_call is still satisfied
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "a" for m in agent._openai_messages)

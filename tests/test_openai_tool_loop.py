"""Regression: the OpenAI branch must execute each tool call exactly once and
answer every tool_call_id exactly once (no O(N^2), no duplicate tool messages,
no unanswered call on a denied last item)."""

from __future__ import annotations

import pytest

from keelson.agent import Agent


def _msg_with_tool_calls(specs):
    return {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": tid, "type": "function", "function": {"name": name, "arguments": args}}
            for tid, name, args in specs
        ]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


_FINAL = {"choices": [{"message": {"role": "assistant", "content": "done"}}],
          "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def oai_agent(monkeypatch):
    agent = Agent(permission_mode="dontAsk", model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    agent._openai_messages = [{"role": "system", "content": "s"}]
    calls: list[tuple[str, dict]] = []

    async def fake_exec(name, inp):
        calls.append((name, inp))
        return f"result::{name}"

    monkeypatch.setattr(agent, "_execute_tool_call", fake_exec)
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", _anop)
    monkeypatch.setattr(agent, "_refresh_runtime_system_prompt", lambda: None)
    agent._exec_calls = calls
    return agent


async def _anop(*a, **k):
    return None


async def test_three_tool_calls_execute_once_each(oai_agent, monkeypatch):
    responses = iter([
        _msg_with_tool_calls([
            ("c1", "read_file", '{"file_path": "a"}'),
            ("c2", "list_files", '{"pattern": "*"}'),
            ("c3", "grep_search", '{"pattern": "x"}'),
        ]),
        _FINAL,
    ])
    monkeypatch.setattr(oai_agent, "_call_openai_stream", lambda: _wrap(next(responses)))

    await oai_agent._chat_openai("go")

    # each tool ran exactly once
    names = [n for n, _ in oai_agent._exec_calls]
    assert sorted(names) == ["grep_search", "list_files", "read_file"]

    # exactly one tool message per tool_call_id, no duplicates
    tool_msgs = [m for m in oai_agent._openai_messages if m.get("role") == "tool"]
    ids = [m["tool_call_id"] for m in tool_msgs]
    assert sorted(ids) == ["c1", "c2", "c3"]
    assert len(ids) == len(set(ids))


async def test_denied_last_call_is_still_answered(oai_agent, monkeypatch):
    # write_file to a new path in dontAsk -> denied; it must still get a tool message
    responses = iter([
        _msg_with_tool_calls([
            ("c1", "read_file", '{"file_path": "a"}'),
            ("c2", "write_file", '{"file_path": "brand_new_file.txt", "content": "x"}'),
        ]),
        _FINAL,
    ])
    monkeypatch.setattr(oai_agent, "_call_openai_stream", lambda: _wrap(next(responses)))

    await oai_agent._chat_openai("go")

    tool_msgs = {m["tool_call_id"]: m["content"] for m in oai_agent._openai_messages if m.get("role") == "tool"}
    assert set(tool_msgs) == {"c1", "c2"}
    assert "deni" in tool_msgs["c2"].lower()
    # the denied write never executed
    assert [n for n, _ in oai_agent._exec_calls] == ["read_file"]


def _wrap(value):
    async def _c():
        return value
    return _c()

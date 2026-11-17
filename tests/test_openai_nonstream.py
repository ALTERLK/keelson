"""_call_openai_nonstream is the fallback for gateways that drop streamed
tool-call deltas. It must retry transient errors like every other model call."""

from __future__ import annotations

import pytest

from keelson.agent import Agent


class _Boom(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


@pytest.fixture
def agent():
    a = Agent(permission_mode="dontAsk", model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    a._openai_messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    return a


def _resp(text="hi there"):
    return type("R", (), {
        "choices": [type("C", (), {
            "message": type("M", (), {"content": text, "tool_calls": None})(),
            "finish_reason": "stop",
        })],
        "usage": type("U", (), {"prompt_tokens": 2, "completion_tokens": 3}),
    })


async def test_retries_then_succeeds(agent, monkeypatch):
    monkeypatch.setattr("keelson.agent.asyncio.sleep", lambda *_a, **_k: _anop())
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom(429)
        return _resp()

    monkeypatch.setattr(agent._openai_client.chat.completions, "create", flaky)
    out = await agent._call_openai_nonstream()
    assert calls["n"] == 3
    assert out["choices"][0]["message"]["content"] == "hi there"
    assert out["usage"]["prompt_tokens"] == 2


async def test_non_retryable_propagates(agent, monkeypatch):
    async def bad(**kwargs):
        raise _Boom(400)

    monkeypatch.setattr(agent._openai_client.chat.completions, "create", bad)
    with pytest.raises(_Boom):
        await agent._call_openai_nonstream()


async def _anop(*a, **k):
    return None

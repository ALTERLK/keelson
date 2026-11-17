"""An empty model reply (no text, no tool call) is retried, not taken as "done".

Found by the case study: on more-itertools #1308 the model listed files once,
the next completion came back empty, and the turn ended as if the task were
finished.
"""

from __future__ import annotations

import pytest

from keelson import events
from keelson.agent import EMPTY_REPLY_NUDGE, EMPTY_REPLY_RETRIES, Agent


def _reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0}}


async def _anop(*a, **k):
    return None


@pytest.fixture
def agent(monkeypatch):
    a = Agent(permission_mode="dontAsk", model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    a._openai_messages = [{"role": "system", "content": "s"}]
    monkeypatch.setattr(a, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(a, "_check_and_compact", _anop)
    monkeypatch.setattr(a, "_refresh_runtime_system_prompt", lambda: None)
    monkeypatch.setattr(a, "_build_side_query", lambda **kwargs: None)
    return a


def _script(agent, monkeypatch, replies):
    calls = []
    queue = iter(replies)

    async def fake_model():
        calls.append([dict(m) for m in agent._openai_messages])
        return next(queue)

    monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
    return calls


@pytest.mark.parametrize("empty", [None, "", "  \n"])
async def test_empty_reply_is_asked_again(agent, monkeypatch, empty):
    calls = _script(agent, monkeypatch, [_reply(empty), _reply("The fix is in more.py.")])
    await agent._chat_openai("fix the bug")

    assert len(calls) == 2
    assert calls[1][-1] == {"role": "user", "content": EMPTY_REPLY_NUDGE}
    roles = [m["role"] for m in agent._openai_messages]
    assert roles == ["system", "user", "user", "assistant"]          # the empty message was not kept
    assert agent._openai_messages[-1]["content"] == "The fix is in more.py."


async def test_gives_up_after_the_retries_and_says_so(agent, monkeypatch):
    calls = _script(agent, monkeypatch, [_reply("")] * 10)
    notices = []
    bus = events.EventBus()
    bus.subscribe(lambda e: notices.append(e.data.get("message", "")) if e.type == events.NOTICE else None)
    with events.use_bus(bus):
        await agent._chat_openai("fix the bug")

    assert len(calls) == EMPTY_REPLY_RETRIES + 1                     # bounded: no endless retry
    assert any("ending the turn" in n for n in notices)


async def test_a_real_answer_is_not_retried(agent, monkeypatch):
    calls = _script(agent, monkeypatch, [_reply("Done."), _reply("never asked")])
    await agent._chat_openai("hi")
    assert len(calls) == 1
    assert agent._empty_replies == 0


async def test_chat_resets_the_counter(agent, monkeypatch):
    agent._empty_replies = EMPTY_REPLY_RETRIES
    monkeypatch.setattr(agent, "_chat_openai", _anop)
    monkeypatch.setattr(agent, "_augment_user_message_with_skill_context", lambda m: (m, None))
    monkeypatch.setattr(agent, "_pop_pending_skill_extraction_window", lambda m: None)
    await agent.chat("next question")
    assert agent._empty_replies == 0

"""Model registry and the protocol details DeepSeek (and Claude) insist on.

- max_tokens is always sent; DeepSeek's thinking switch is explicit.
- reasoning_content / thinking blocks are kept and sent back (DeepSeek returns
  400 for a tool conversation that drops them).
- A reply cut off by max_tokens never runs its half-written tool call.
- Cache hits are counted; sub-agents inherit the key and endpoint.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from keelson import models
from keelson.agent import TRUNCATION_RETRIES, Agent

# ─── registry ───────────────────────────────────────────────


def test_deepseek_models_plan_against_a_budget_not_the_full_million(monkeypatch):
    monkeypatch.delenv("KEELSON_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("KEELSON_MAX_OUTPUT_TOKENS", raising=False)
    assert models.info("deepseek-flash").context == 1_000_000
    assert models.context_window("deepseek-flash") == 256_000
    assert models.max_output_tokens("deepseek-v4-pro") == 65_536


def test_unknown_models_get_conservative_limits_and_env_overrides_win(monkeypatch):
    monkeypatch.delenv("KEELSON_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("KEELSON_MAX_OUTPUT_TOKENS", raising=False)
    assert models.context_window("some-local-model") == 128_000
    assert models.max_output_tokens("some-local-model") == 8_192
    monkeypatch.setenv("KEELSON_CONTEXT_WINDOW", "32000")
    monkeypatch.setenv("KEELSON_MAX_OUTPUT_TOKENS", "4096")
    assert models.context_window("deepseek-flash") == 32_000
    assert models.max_output_tokens("deepseek-flash") == 4_096


def test_retired_deepseek_names_say_what_to_use_instead():
    assert "deepseek-flash" in models.retirement_note("deepseek-chat")
    assert "deepseek-v4-pro" in models.retirement_note("deepseek-reasoner")
    assert models.retirement_note("deepseek-flash") == ""


def test_cost_uses_cache_hit_prices():
    all_hits = models.cost_usd("deepseek-flash", cache_hit=1_000_000, cache_miss=0, output=0)
    all_misses = models.cost_usd("deepseek-flash", cache_hit=0, cache_miss=1_000_000, output=0)
    assert all_hits < all_misses / 10
    assert models.cost_usd("some-local-model", cache_hit=1, cache_miss=1, output=1) is None


# ─── OpenAI-compatible path (DeepSeek's main API) ───────────


def _chunk(*, content=None, reasoning=None, tool=None, finish=None, usage=None):
    delta = NS(content=content, reasoning_content=reasoning, tool_calls=[tool] if tool else None)
    choices = [NS(delta=delta, finish_reason=finish)] if (content or reasoning or tool or finish) else []
    return NS(choices=choices, usage=usage)


def _tool(index, call_id=None, name=None, args=""):
    return NS(index=index, id=call_id, function=NS(name=name, arguments=args))


class _Stream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _usage(prompt=100, completion=10, hit=0):
    return NS(prompt_tokens=prompt, completion_tokens=completion, prompt_cache_hit_tokens=hit)


async def _anop(*a, **k):
    return None


@pytest.fixture
def deepseek(monkeypatch):
    agent = Agent(permission_mode="dontAsk", model="deepseek-flash", api_base="https://api.deepseek.com", api_key="k")
    agent._openai_messages = [{"role": "system", "content": "s"}]
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", _anop)
    monkeypatch.setattr(agent, "_refresh_runtime_system_prompt", lambda: None)
    executed = []

    async def fake_exec(name, inp):
        executed.append(name)
        return f"result::{name}"

    monkeypatch.setattr(agent, "_execute_tool_call", fake_exec)
    agent._executed = executed
    agent._requests = []
    return agent


def _serve(agent, monkeypatch, replies):
    """Answer each streamed request with the next list of chunks, recording what was sent."""
    replies = iter(replies)

    async def create(**kwargs):
        agent._requests.append(copy.deepcopy(kwargs))
        return _Stream(next(replies))

    monkeypatch.setattr(agent._openai_client.chat.completions, "create", create)


async def test_reasoning_content_is_kept_and_sent_back(deepseek, monkeypatch):
    _serve(deepseek, monkeypatch, [
        [_chunk(reasoning="I should read "), _chunk(reasoning="the file."),
         _chunk(tool=_tool(0, "c1", "read_file", '{"file_path": "a.txt"}')),
         _chunk(finish="tool_calls"), _chunk(usage=_usage(hit=60))],
        [_chunk(content="done"), _chunk(finish="stop"), _chunk(usage=_usage())],
    ])

    await deepseek._chat_openai("read a.txt")

    assert deepseek._executed == ["read_file"]
    second = deepseek._requests[1]["messages"]
    assistant = next(m for m in second if m.get("role") == "assistant")
    assert assistant["reasoning_content"] == "I should read the file."
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"
    assert deepseek.total_cache_hit_tokens == 60


async def test_every_request_sends_max_tokens_and_the_thinking_switch(deepseek, monkeypatch):
    _serve(deepseek, monkeypatch, [[_chunk(content="hi"), _chunk(finish="stop")]])
    await deepseek._chat_openai("hello")
    sent = deepseek._requests[0]
    assert sent["max_tokens"] == models.max_output_tokens("deepseek-flash")
    assert sent["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_thinking_on_is_sent_explicitly_too(monkeypatch):
    agent = Agent(permission_mode="dontAsk", model="deepseek-v4-pro", api_base="https://api.deepseek.com",
                  api_key="k", thinking=True)
    assert agent._openai_request_extras()["extra_body"] == {"thinking": {"type": "enabled"}}


def test_other_openai_endpoints_get_no_deepseek_fields():
    agent = Agent(permission_mode="dontAsk", model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    extras = agent._openai_request_extras()
    assert "extra_body" not in extras
    assert extras["max_tokens"] == 16_384


async def test_a_truncated_tool_call_is_not_run(deepseek, monkeypatch):
    _serve(deepseek, monkeypatch, [
        [_chunk(content="Writing the file now."),
         _chunk(tool=_tool(0, "c1", "write_file", '{"file_path": "big.py", "content": "def f(')),
         _chunk(finish="length")],
        [_chunk(content="I'll write it in parts."), _chunk(finish="stop")],
    ])

    await deepseek._chat_openai("write big.py")

    assert deepseek._executed == []
    second = deepseek._requests[1]["messages"]
    assert not any(m.get("tool_calls") for m in second)
    assert not any(m.get("role") == "tool" for m in second)
    assert "NOT executed" in second[-1]["content"]


async def test_repeated_truncation_ends_the_turn(deepseek, monkeypatch):
    cut = [_chunk(tool=_tool(0, "c1", "write_file", '{"file_path": "x", "content": "')), _chunk(finish="length")]
    _serve(deepseek, monkeypatch, [list(cut) for _ in range(TRUNCATION_RETRIES + 1)])

    await deepseek._chat_openai("write x")

    assert deepseek._executed == []
    assert len(deepseek._requests) == TRUNCATION_RETRIES + 1


# ─── Anthropic-compatible path ──────────────────────────────


def test_thinking_blocks_keep_their_signature():
    block = NS(type="thinking", thinking="step by step", signature="sig-123")
    assert Agent._block_to_dict(block) == {"type": "thinking", "thinking": "step by step", "signature": "sig-123"}
    redacted = NS(type="redacted_thinking", data="opaque")
    assert Agent._block_to_dict(redacted) == {"type": "redacted_thinking", "data": "opaque"}


def test_deepseek_anthropic_endpoint_gets_thinking_switched_off_explicitly():
    ds = Agent(permission_mode="dontAsk", model="deepseek-flash", anthropic_base_url="https://api.deepseek.com/anthropic",
               api_key="k")
    assert ds._anthropic_thinking_param(8192) == {"thinking": {"type": "disabled"}}
    claude = Agent(permission_mode="dontAsk", model="claude-sonnet-4-6", api_key="k")
    assert claude._anthropic_thinking_param(8192) == {}


def test_anthropic_usage_counts_cached_prompt_tokens():
    agent = Agent(permission_mode="dontAsk", model="claude-sonnet-4-6", api_key="k")
    agent._record_anthropic_usage(NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=90,
                                     cache_creation_input_tokens=0))
    assert agent.total_input_tokens == 100
    assert agent.total_cache_hit_tokens == 90
    assert agent.last_input_token_count == 100


# ─── sub-agents ─────────────────────────────────────────────


def test_sub_agents_inherit_key_and_endpoint():
    oai = Agent(permission_mode="dontAsk", model="deepseek-flash", api_base="https://api.deepseek.com", api_key="k1")
    kw = oai._sub_agent_kwargs()
    assert kw["api_key"] == "k1" and kw["api_base"] == "https://api.deepseek.com" and kw["anthropic_base_url"] is None

    ant = Agent(permission_mode="dontAsk", model="deepseek-flash", anthropic_base_url="https://h/anthropic", api_key="k2")
    kw = ant._sub_agent_kwargs()
    assert kw["api_key"] == "k2" and kw["anthropic_base_url"] == "https://h/anthropic" and kw["api_base"] is None
    sub = Agent(**kw, is_sub_agent=True, permission_mode="dontAsk")
    assert sub._anthropic_client.api_key == "k2"

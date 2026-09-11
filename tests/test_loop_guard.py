"""Loop guard: stop a model that hammers one tool with reworded args.

Windowed on tool NAME (the observed spiral fuzzes args, see
bench/spiral-diagnosis.md), and only when results have stopped diverging.
"""

from __future__ import annotations

import pytest

from agents.agent import Agent


@pytest.fixture
def agent():
    a = Agent(permission_mode="dontAsk", model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    a._openai_messages = [{"role": "system", "content": "s"}]
    return a


def _fill(agent, name, n, distinct_results=1):
    for i in range(n):
        agent._loop_window.append({"name": name, "result_key": f"r{i % max(1, distinct_results)}"})


def test_trips_on_same_tool_same_results(agent):
    _fill(agent, "genealogy_query", 6, distinct_results=1)
    msg = agent._loop_guard_check("genealogy_query")
    assert msg is not None and "Loop guard" in msg and "ANSWER:" in msg


def test_does_not_trip_while_results_still_diverge(agent):
    _fill(agent, "genealogy_query", 8, distinct_results=6)   # every call brought new info
    assert agent._loop_guard_check("genealogy_query") is None


def test_does_not_trip_below_threshold(agent):
    _fill(agent, "read_file", 4, distinct_results=1)
    assert agent._loop_guard_check("read_file") is None


def test_reading_six_distinct_files_is_fine(agent):
    # same tool, six different results -> legitimate, must not trip
    _fill(agent, "read_file", 6, distinct_results=6)
    assert agent._loop_guard_check("read_file") is None


def test_control_tools_are_exempt(agent):
    _fill(agent, "compact_context", 8, distinct_results=1)
    assert agent._loop_guard_check("compact_context") is None


def test_env_flag_disables_it(agent, monkeypatch):
    monkeypatch.setenv("KEELSON_LOOP_GUARD", "0")
    _fill(agent, "genealogy_query", 8, distinct_results=1)
    assert agent._loop_guard_check("genealogy_query") is None


def test_window_is_bounded(agent):
    # old calls to a now-abandoned tool must fall out of the window
    _fill(agent, "old_tool", 8, distinct_results=1)
    _fill(agent, "new_tool", 8, distinct_results=6)
    assert agent._loop_guard_check("old_tool") is None       # no longer in the last WINDOW


async def test_execute_tool_call_short_circuits_and_counts(agent, monkeypatch):
    ran = []

    async def fake_exec_tool(name, inp, state):
        ran.append(name)
        return "real result"

    monkeypatch.setattr("agents.agent.execute_tool", fake_exec_tool)
    _fill(agent, "genealogy_query", 6, distinct_results=1)

    out = await agent._execute_tool_call("genealogy_query", {"x": 1})
    assert "Loop guard" in out
    assert ran == []                       # the real tool never ran
    assert agent._loop_guard_hits == 1

    # a different tool still runs and is recorded
    out2 = await agent._execute_tool_call("other_tool", {"y": 2})
    assert out2 == "real result" and ran == ["other_tool"]
    assert agent._loop_window[-1]["name"] == "other_tool"


async def test_chat_start_resets_guard_state(agent, monkeypatch):
    agent._loop_window = [{"name": "x", "result_key": "r"}] * 5
    agent._loop_guard_hits = 3
    monkeypatch.setattr(agent, "_chat_openai", _anop)
    monkeypatch.setattr(agent, "_augment_user_message_with_skill_context", lambda m: (m, None))
    monkeypatch.setattr(agent, "_pop_pending_skill_extraction_window", lambda m: None)
    await agent.chat("new question")
    assert agent._loop_window == [] and agent._loop_guard_hits == 0


async def _anop(*a, **k):
    return None

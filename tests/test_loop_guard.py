"""Loop guard: stop a model that hammers one tool with reworded args.

Windowed on tool NAME (the observed spiral fuzzes args, see
bench/spiral-diagnosis.md), and only when results have stopped diverging.
"""

from __future__ import annotations

import pytest

from keelson.agent import Agent


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

    monkeypatch.setattr("keelson.agent.execute_tool", fake_exec_tool)
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


# ─── found by the case study (bench/casestudy, round 1) ─────


def _shell_window(agent, command, n, distinct_results=1):
    from keelson.agent import _guard_key
    _fill(agent, _guard_key("run_shell", {"command": command}), n, distinct_results)


def test_guard_key_is_the_program_for_the_shell_and_the_name_otherwise():
    from keelson.agent import _guard_key

    assert _guard_key("read_file", {"file_path": "a.py"}) == "read_file"
    assert _guard_key("run_shell", {"command": 'python -c "print(1)"'}) == "run_shell:python -c"
    assert _guard_key("run_shell", {"command": "python -m pytest tests/test_ttl.py"}) == "run_shell:python -m pytest"
    assert _guard_key("run_shell", {"command": "python -m pip install x"}) == "run_shell:python -m pip"
    assert _guard_key("run_shell", {"command": "GIT  diff --stat"}) == "run_shell:git diff"
    assert _guard_key("run_shell", {"command": ""}) == "run_shell"
    assert _guard_key("run_shell", None) == "run_shell"


def test_repeated_probes_do_not_block_the_test_run_that_follows(agent):
    # cachetools #440: six `python -c` probes with the same output, then pytest.
    _shell_window(agent, 'python -c "repro()"', 6)
    assert agent._loop_guard_check("run_shell", {"command": 'python -c "repro() again"'}) is not None
    assert agent._loop_guard_check("run_shell", {"command": "python -m pytest tests/test_ttl.py"}) is None


def test_a_fold_does_not_reset_the_guard(agent):
    # The model called compact_context "to reset" after the guard stopped it.
    _fill(agent, "genealogy_query", 6, distinct_results=1)
    agent._loop_guard_hits = 2
    agent._record_fold_event()
    assert agent._loop_guard_check("genealogy_query") is not None
    assert agent._loop_guard_hits == 2


def test_budget_is_exceeded_once_the_guard_has_tripped_enough(agent):
    from keelson.agent import LOOP_GUARD_MAX_HITS

    agent._loop_guard_hits = LOOP_GUARD_MAX_HITS - 1
    assert agent._check_budget()["exceeded"] is False
    agent._loop_guard_hits = LOOP_GUARD_MAX_HITS
    verdict = agent._check_budget()
    assert verdict["exceeded"] is True and "Loop guard" in verdict["reason"]
    assert "stopped" in agent._finalize_prompt()


async def test_a_model_that_ignores_the_guard_is_stopped_by_the_runtime(agent, monkeypatch):
    """Guard trips three times, the model asks for a tool again: no tool runs, one tool-less call ends the turn."""
    from keelson.agent import LOOP_GUARD_MAX_HITS

    def tool_call(i):
        return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "genealogy_query", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    replies = iter(tool_call(i) for i in range(50))

    async def fake_model():
        return next(replies)

    ran = []

    async def fake_exec_tool(name, inp, state):
        ran.append(name)
        return "same result"

    final_calls = []

    async def fake_create(**kwargs):
        final_calls.append(kwargs)
        message = type("M", (), {"content": "ANSWER: stopped"})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()], "usage": None})()

    async def anop(*a, **k):
        return None

    monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
    monkeypatch.setattr("keelson.agent.execute_tool", fake_exec_tool)
    monkeypatch.setattr("keelson.agent.check_permission", lambda *a, **k: {"action": "allow"})
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", anop)
    monkeypatch.setattr(agent, "_refresh_runtime_system_prompt", lambda: None)
    monkeypatch.setattr(agent, "_build_side_query", lambda **kwargs: None)
    monkeypatch.setattr(agent._openai_client.chat.completions, "create", fake_create)

    await agent._chat_openai("go")

    assert len(ran) == 6                                   # six real calls, then the guard answers instead
    assert agent._loop_guard_hits == LOOP_GUARD_MAX_HITS
    assert len(final_calls) == 1                           # the runtime's own closing call
    assert "being stopped" in final_calls[0]["messages"][-1]["content"]
    assert agent._openai_messages[-1] == {"role": "assistant", "content": "ANSWER: stopped"}

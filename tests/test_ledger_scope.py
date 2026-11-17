"""Phase 4: the task ledger, the scope guard, usage records, and a prompt prefix the provider can cache."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from keelson import events, prompt, usage
from keelson import ledger as ledger_mod
from keelson import scope as scope_mod
from keelson.agent import Agent

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


# ─── the ledger ─────────────────────────────────────────────


def test_rules_are_kept_word_for_word_in_english_and_chinese():
    text = ("Fix the date parser. Don't touch the public API. It should accept ISO dates; "
            "keep the old function names.\n不要改 tests 目录。顺便看看日志。只改 parser.py 就行")
    assert ledger_mod.rule_sentences(text) == [
        "Don't touch the public API.",
        "It should accept ISO dates;",
        "keep the old function names.",
        "不要改 tests 目录。",
        "只改 parser.py 就行",
    ]


def test_injected_tags_are_not_mistaken_for_the_users_words():
    text = "<git-status>\nGit status:\n M never.py\n</git-status>\n\nRename foo."
    assert ledger_mod.rule_sentences(text) == []
    ledger = ledger_mod.Ledger()
    ledger.note_user_message(text)
    assert ledger.goal == "Rename foo."


def test_the_goal_is_the_first_request_and_repeated_rules_are_counted():
    ledger = ledger_mod.Ledger()
    ledger.note_user_message("Port the CLI to click. Never add new dependencies.")
    ledger.note_user_message("ok, continue. never add new dependencies!")
    assert ledger.goal == "Port the CLI to click. Never add new dependencies."
    assert [c["count"] for c in ledger.constraints] == [2]
    assert ledger.repeated_constraints() == ["Never add new dependencies."]


def test_the_plan_is_validated_and_rendered_with_the_constraints_quoted():
    ledger = ledger_mod.Ledger()
    ledger.note_user_message("Speed up the importer. Do not change the file format.")
    summary = ledger.update([{"step": "profile", "status": "done"}, {"step": "cache lookups", "status": "in progress"},
                             "write a benchmark", {"status": "done"}], decision="use functools.lru_cache")
    assert summary.startswith("Task updated: 1/3 steps done")
    assert [s["status"] for s in ledger.plan] == ["done", "in_progress", "pending"]
    ledger.note_files(["importer.py", "(changed by shell commands)"])
    ledger.note_verify("passed", "pytest -q: 9 passed")

    text = ledger.render()
    assert '- "Do not change the file format."' in text
    assert "[x] profile" in text and "[~] cache lookups" in text and "[ ] write a benchmark" in text
    assert "use functools.lru_cache" in text and "importer.py" in text and "shell" not in text
    assert "Last verification" in text and text.startswith("<keelson-task-ledger>")
    assert ledger.kept_summary() == "the goal, 1 constraint(s) verbatim, the plan (1/3 done), 1 decision(s), 1 changed file(s)"


def test_the_ledger_survives_a_round_trip_to_disk(in_tmp_cwd):
    ledger = ledger_mod.Ledger()
    ledger.note_user_message("必须保持向后兼容。")
    ledger_mod.save(ledger, "s1")
    again = ledger_mod.load("s1")
    assert again is not None and again.constraints[0]["text"] == "必须保持向后兼容。"
    assert ledger_mod.load("nope") is None


def test_old_rules_said_once_make_room_before_repeated_ones():
    ledger = ledger_mod.Ledger()
    ledger.add_constraint("always rule zero")
    ledger.add_constraint("always rule zero")
    for i in range(1, ledger_mod.MAX_CONSTRAINTS + 1):
        ledger.add_constraint(f"never do thing {i}")
    texts = [c["text"] for c in ledger.constraints]
    assert "always rule zero" in texts and "never do thing 1" not in texts
    assert len(texts) == ledger_mod.MAX_CONSTRAINTS


def test_repeated_rules_go_into_keelson_md_under_rules(in_tmp_cwd):
    md = ledger_mod.append_to_keelson_md(["Use pathlib.", "No new dependencies."])
    assert md.read_text(encoding="utf-8").endswith("## Rules\n\n- Use pathlib.\n- No new dependencies.\n")

    md.write_text("# KEELSON.md\n\n## Rules\n\n- Old rule.\n\n## Verify\n\n- `pytest -q`\n", encoding="utf-8")
    ledger_mod.append_to_keelson_md(["Old rule.", "New rule."])
    assert md.read_text(encoding="utf-8") == ("# KEELSON.md\n\n## Rules\n\n- Old rule.\n- New rule.\n\n"
                                               "## Verify\n\n- `pytest -q`\n")


# ─── scope ──────────────────────────────────────────────────


def test_scope_matching_by_file_folder_and_glob(in_tmp_cwd):
    scope = ["src/parser.py", "docs/", "tests/test_*.py"]
    assert scope_mod.in_scope("src/parser.py", scope)
    assert scope_mod.in_scope(str(in_tmp_cwd / "src" / "parser.py"), scope)
    assert scope_mod.in_scope("./docs/guide/a.md", scope)
    assert scope_mod.in_scope("tests/test_parser.py", scope)
    assert not scope_mod.in_scope("src/lexer.py", scope)
    assert not scope_mod.in_scope("src/parser.py.bak", scope)
    assert not scope_mod.in_scope("docsx/a.md", scope)
    assert scope_mod.in_scope("anything.py", ["."])


def test_mentions_name_existing_files_only(in_tmp_cwd):
    (in_tmp_cwd / "src").mkdir()
    (in_tmp_cwd / "src" / "app.py").write_text("", encoding="utf-8")
    text = "Fix @src/app.py, see @src/ and ping @someone or mail me@example.com."
    assert scope_mod.mentioned_paths(text) == ["src/app.py", "src"]


def test_a_large_change_for_a_short_request_is_flagged():
    many = [f"f{i}.py" for i in range(9)]
    assert scope_mod.size_warning("rename foo to bar", many).startswith("9 files changed")
    assert scope_mod.size_warning("rename foo to bar", many[:3]) is None
    assert scope_mod.size_warning("x" * 500, many) is None


# ─── the agent ──────────────────────────────────────────────


def _tool_calls(*specs, prompt_tokens=10):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(specs)
    ]}}], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5, "cache_hit_tokens": 4}}


def _final(text="done"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cache_hit_tokens": 8}}


async def _noop(*args, **kwargs):
    return None


def _agent(monkeypatch, responses, *, mode="acceptEdits", custom_prompt=False):
    agent = Agent(permission_mode=mode, model="deepseek-v4-flash", api_base="https://x/v1", api_key="k",
                  custom_system_prompt="harness" if custom_prompt else None)
    queue = iter(responses)
    agent.requests = []

    async def fake_model():
        agent.requests.append(json.dumps(agent._openai_messages, ensure_ascii=False))
        return next(queue)

    async def fake_exec(name, inp):
        if name in ("update_task", "declare_scope"):
            return getattr(agent, "_" + name)(inp)
        if name == "write_file":
            Path(inp["file_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(inp["file_path"]).write_text(inp["content"], encoding="utf-8")
            return f"Wrote {inp['file_path']}"
        return f"result::{name}"

    monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
    monkeypatch.setattr(agent, "_execute_tool_call", fake_exec)
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", _noop)
    monkeypatch.setattr(agent, "_build_side_query", lambda **kwargs: None)
    return agent


@pytest.fixture
def collected():
    seen: list[events.Event] = []
    unsubscribe = events.bus.subscribe(seen.append)
    yield seen
    unsubscribe()


def test_writing_outside_a_declared_scope_asks_first(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [])
    agent._start_request("fix the parser")
    assert agent._gate("c0", "write_file", {"file_path": "src/lexer.py", "content": ""})["action"] == "allow"

    agent._declare_scope({"paths": ["src/parser.py", "tests/"], "reason": "the bug is in the parser"})
    assert agent._gate("c1", "write_file", {"file_path": "src/parser.py", "content": ""})["action"] == "allow"
    assert agent._gate("c2", "write_file", {"file_path": "tests/test_new.py", "content": ""})["action"] == "allow"
    perm = agent._gate("c3", "edit_file", {"file_path": "src/lexer.py", "old_string": "a", "new_string": "b"})
    assert perm["action"] == "confirm" and perm["rules"] == ["keelson(out-of-scope)"]
    assert "outside the scope" in perm["reason"] and "src/parser.py" in perm["reason"]

    agent._session_rules.append("keelson(out-of-scope)")
    assert agent._gate("c4", "write_file", {"file_path": "src/lexer.py", "content": ""})["action"] == "allow"


def test_the_scope_guard_denies_in_dont_ask_and_stands_aside_in_bypass(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [], mode="dontAsk")
    agent._start_request("x")
    agent._declare_scope({"paths": ["a.py"]})
    perm = agent._gate("c1", "write_file", {"file_path": "b.py", "content": ""})
    assert perm["action"] == "deny" and "declare_scope" in perm["message"]

    agent = _agent(monkeypatch, [], mode="bypassPermissions")
    agent._start_request("x")
    agent._declare_scope({"paths": ["a.py"]})
    assert agent._gate("c1", "write_file", {"file_path": "b.py", "content": ""})["action"] == "allow"

    agent = _agent(monkeypatch, [])
    agent._start_request("x")
    agent._declare_scope({"paths": ["a.py"]})
    monkeypatch.setenv("KEELSON_SCOPE_GUARD", "0")
    assert agent._gate("c1", "write_file", {"file_path": "b.py", "content": ""})["action"] == "allow"


def test_mentioned_files_are_the_scope_and_it_resets_per_request(monkeypatch, in_tmp_cwd, collected):
    (in_tmp_cwd / "app.py").write_text("", encoding="utf-8")
    agent = _agent(monkeypatch, [])
    agent._start_request("fix @app.py please")
    assert agent.ledger.scope == ["app.py"]
    assert agent._gate("c1", "write_file", {"file_path": "other.py", "content": ""})["action"] == "confirm"
    scope_events = [e.data for e in collected if e.type == events.SCOPE]
    assert scope_events == [{"paths": ["app.py"], "reason": "mentioned by you", "added": True}]

    agent._start_request("now something else")
    assert agent.ledger.scope == []


async def test_an_allowed_out_of_scope_change_is_reported_at_the_end(monkeypatch, in_tmp_cwd, collected):
    monkeypatch.setenv("KEELSON_VERIFY", "1")
    agent = _agent(monkeypatch, [
        _tool_calls(("declare_scope", {"paths": ["a.py"], "reason": "the bug is here"})),
        _tool_calls(("write_file", {"file_path": "a.py", "content": "x = 1\n"}),
                    ("write_file", {"file_path": "b.py", "content": "y = 1\n"})),
        _final("Fixed a.py and tidied b.py."),
    ])
    agent._verify_enabled = True

    async def approve(call_id, name, perm):
        return None
    monkeypatch.setattr(agent, "_ask_user", approve)

    await agent.chat("fix a.py")

    verdict = next(e.data for e in collected if e.type == events.VERIFY and e.data.get("phase") == "done")
    assert verdict["out_of_scope"] == ["b.py"]
    assert "b.py" in agent.ledger.scope and agent.ledger.files == ["a.py", "b.py"]


async def test_update_task_records_the_plan_and_shows_it(monkeypatch, in_tmp_cwd, collected):
    agent = _agent(monkeypatch, [
        _tool_calls(("update_task", {"plan": [{"step": "read", "status": "done"}, {"step": "fix"}],
                                     "decision": "keep the old API"})),
        _final(),
    ])
    await agent.chat("Refactor the loader. Keep the old API.")
    assert [s["step"] for s in agent.ledger.plan] == ["read", "fix"]
    task = next(e.data for e in collected if e.type == events.TASK)
    assert task["plan"][0] == {"step": "read", "status": "done"} and task["decision"] == "keep the old API"
    saved = ledger_mod.load(agent.session_id)
    assert saved is not None and saved.decisions == ["keep the old API"]
    assert saved.constraints[0]["text"] == "Keep the old API."


async def test_a_fold_puts_the_ledger_back_word_for_word(monkeypatch, in_tmp_cwd, collected):
    agent = _agent(monkeypatch, [_tool_calls(("update_task", {"plan": [{"step": "migrate", "status": "in_progress"}]})),
                                 _final("working on it")])
    await agent.chat("Migrate the config to TOML. 不要删除旧的 .ini 读取逻辑。")
    agent._openai_messages.append({"role": "user", "content": "padding so there is something to fold"})

    assert await agent._compact_conversation(trigger="manual")

    folded = agent._openai_messages[1]["content"]
    assert '"不要删除旧的 .ini 读取逻辑。"' in folded and "[~] migrate" in folded
    assert "Migrate the config to TOML." in folded
    fold = next(e.data for e in collected if e.type == events.CONTEXT_FOLD)
    assert "1 constraint(s) verbatim" in fold["kept"]


async def test_resume_brings_the_ledger_back(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [_final()])
    await agent.chat("Never use tabs.")
    saved = json.loads(json.dumps({"ledger": agent.ledger.to_dict(), "openaiMessages": agent._openai_messages}))

    other = _agent(monkeypatch, [])
    other.restore_session(saved)
    assert other.ledger.constraints[0]["text"] == "Never use tabs."


# ─── a prefix the provider can cache ────────────────────────


def _assert_each_request_extends_the_last(requests: list[str]) -> None:
    for before, after in zip(requests, requests[1:], strict=False):
        prev, cur = json.loads(before), json.loads(after)
        assert cur[:len(prev)] == prev, "a request changed an earlier message, so the cached prefix is lost"


@needs_git
async def test_requests_only_ever_append_so_the_cached_prefix_holds(monkeypatch, in_tmp_cwd):
    subprocess.run(["git", "init", "-q"], check=True)
    (in_tmp_cwd / "a.py").write_text("x = 0\n", encoding="utf-8")
    window = Agent(permission_mode="acceptEdits", model="deepseek-v4-flash", api_base="https://x/v1",
                   api_key="k").effective_window
    agent = _agent(monkeypatch, [
        _tool_calls(("read_file", {"file_path": "a.py"})),
        # The context crosses 60%: the fold hint must go on the newest message, not the system prompt.
        _tool_calls(("write_file", {"file_path": "a.py", "content": "x = 1\n"}), prompt_tokens=int(window * 0.7)),
        _final("changed"),
        _tool_calls(("write_file", {"file_path": "b.py", "content": "y = 1\n"})),
        _final("added b"),
    ])
    system_before = agent._openai_messages[0]["content"]

    await agent.chat("set x to 1")
    (in_tmp_cwd / "c.txt").write_text("", encoding="utf-8")   # git status changes between the turns
    await agent.chat("add b.py")

    assert len(agent.requests) == 5
    _assert_each_request_extends_the_last(agent.requests)
    assert agent._openai_messages[0]["content"] == system_before
    assert "Git status" not in system_before and "utilization" not in system_before
    users = [m["content"] for m in agent._openai_messages if m["role"] == "user"]
    assert users[0].startswith("<git-status>") and "?? a.py" in users[0]
    assert users[1].startswith("<git-status>") and "?? c.txt" in users[1] and "?? b.py" not in users[1]
    hints = [m for m in agent._openai_messages if "<keelson-fold-hint>" in str(m.get("content"))]
    assert len(hints) == 1 and hints[0]["role"] == "tool"


async def test_an_unchanged_git_status_is_not_sent_again(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [_final(), _final()])
    monkeypatch.setattr("keelson.agent.get_git_context", lambda: "Git branch: main")
    await agent.chat("one")
    await agent.chat("two")
    users = [m["content"] for m in agent._openai_messages if m["role"] == "user"]
    assert users[0].startswith("<git-status>") and users[1] == "two"


def test_outside_a_repository_there_is_no_git_note(in_tmp_cwd):
    assert prompt.get_git_context() == ""


def test_keelson_md_and_agents_md_are_read_from_the_project_and_above(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / "AGENTS.md").write_text("outer agents rule", encoding="utf-8")
    (inner / "KEELSON.md").write_text("inner keelson rule", encoding="utf-8")
    (inner / "CLAUDE.md").write_text("inner claude rule", encoding="utf-8")
    monkeypatch.chdir(inner)

    names = [(f.parent.name, f.name) for f in prompt.instruction_files()]
    assert names == [("outer", "AGENTS.md"), ("inner", "KEELSON.md"), ("inner", "CLAUDE.md")]
    text = prompt.load_claude_md()
    assert text.index("outer agents rule") < text.index("inner keelson rule") < text.index("inner claude rule")


# ─── usage ──────────────────────────────────────────────────


def test_usage_is_recorded_per_turn_and_summed(monkeypatch, in_tmp_cwd):
    usage.record(session_id="s1", model="deepseek-v4-flash", input_tokens=1000, output_tokens=50,
                 cache_hit_tokens=800, cost_usd=0.001)
    usage.record(session_id="s2", model="gemini", input_tokens=1000, output_tokens=50,
                 cache_hit_tokens=0, cost_usd=0.002, cwd=Path("/elsewhere"))
    today = usage.totals(days=1)
    assert (today.turns, today.input_tokens, round(today.cost_usd, 4)) == (2, 2000, 0.003)
    assert today.hit_rate == pytest.approx(0.4)
    assert usage.totals(days=1, session="s1").hit_rate == pytest.approx(0.8)
    assert usage.totals(days=1, project=str(Path.cwd())).turns == 1
    assert set(usage.by_model()) == {"deepseek-v4-flash", "gemini"}
    assert "cache hit 40%" in usage.fmt(today)


async def test_a_turn_writes_one_usage_record_and_the_daily_budget_stops_new_turns(monkeypatch, in_tmp_cwd, collected):
    agent = _agent(monkeypatch, [_final(), _final()])
    await agent.chat("hello")
    t = usage.totals(days=1, session=agent.session_id)
    assert (t.turns, t.input_tokens, t.cache_hit_tokens) == (1, 10, 8)

    monkeypatch.setenv("KEELSON_DAILY_BUDGET_USD", "0.0000001")
    before = len(agent.requests)
    await agent.chat("again")
    assert len(agent.requests) == before            # no model call
    assert any(e.type == events.NOTICE and "KEELSON_DAILY_BUDGET_USD" in e.data.get("message", "")
               for e in collected)

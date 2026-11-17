"""Phase 5: the terminal — language, commands, the input line, /diff, /add, -p, resume."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
from pathlib import Path

import pytest

from keelson import checkpoints, commands, i18n, main, terminal_input, ui
from keelson.agent import Agent

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


# ─── language ───────────────────────────────────────────────


def test_every_string_has_both_languages():
    missing = [k for k, v in i18n.STRINGS.items() if not v.get("en") or not v.get("zh")]
    assert missing == []


def test_language_follows_keelson_lang(monkeypatch):
    monkeypatch.setenv("KEELSON_LANG", "zh")
    assert i18n.t("goodbye") == "会话已保存，再见。"
    assert i18n.t("more_lines", n=3) == "… 还有 3 行"
    monkeypatch.setenv("KEELSON_LANG", "en")
    assert i18n.t("goodbye") == "Session saved. Bye."
    assert i18n.t("no-such-key") == "no-such-key"


def test_help_lists_every_command_in_the_chosen_language(monkeypatch):
    text = commands.help_text()
    for c in commands.COMMANDS:
        assert c.name in text
    assert "Put back what the last turn changed" in text
    monkeypatch.setenv("KEELSON_LANG", "zh")
    assert "撤销上一轮的全部改动" in commands.help_text()
    assert len(set(commands.names())) == len(commands.COMMANDS)


# ─── the input line ─────────────────────────────────────────


def test_slash_completion_offers_commands_and_skills():
    names = [n for n, _ in terminal_input.command_matches("/re")]
    assert {"/review", "/rewind", "/redo", "/revert", "/resume"} <= set(names)
    with_skills = terminal_input.command_matches("/co", ["commit", "compact"])
    assert ("/commit", "skill") in with_skills
    assert [n for n, _ in with_skills].count("/compact") == 1     # a built-in is never listed twice


def test_path_completion_lists_folders_first_and_skips_vendor_dirs(in_tmp_cwd):
    (in_tmp_cwd / "src" / "app").mkdir(parents=True)
    (in_tmp_cwd / "src" / "app" / "main.py").write_text("", encoding="utf-8")
    (in_tmp_cwd / "setup.py").write_text("", encoding="utf-8")
    (in_tmp_cwd / "node_modules").mkdir()
    assert terminal_input.path_matches("s") == ["src/", "setup.py"]
    assert terminal_input.path_matches("src/a") == ["src/app/"]
    assert terminal_input.path_matches("src\\app\\m") == ["src/app/main.py"]
    assert terminal_input.path_matches("n") == []
    # A bare name also finds files deeper in the project.
    assert terminal_input.path_matches("main", ["src/app/main.py", "docs/x.md"]) == ["src/app/main.py"]


def test_input_falls_back_to_plain_input_without_a_terminal(monkeypatch):
    agent = _agent(monkeypatch, [])
    term = terminal_input.TerminalInput(agent)
    assert not term.rich                      # conftest sets KEELSON_SIMPLE_INPUT=1
    monkeypatch.setattr("builtins.input", lambda *a: "hello")
    assert asyncio.run(term.read()) == "hello"

    def eof(*a):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert asyncio.run(term.read()) is None


def _typed(agent, *chunks: str) -> str:
    """Run the real prompt_toolkit session on scripted key presses; return what it submits.

    Chunks are typed a moment apart, as a person would: completion runs asynchronously.
    """
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    async def go():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            term = terminal_input.TerminalInput.__new__(terminal_input.TerminalInput)
            term.agent, term._ask_session = agent, None
            term.session = term._build()

            async def type_keys():
                for chunk in chunks:
                    pipe.send_text(chunk)
                    await asyncio.sleep(0.3)

            typing = asyncio.ensure_future(type_keys())
            try:
                return await term.read()
            finally:
                typing.cancel()

    return asyncio.run(go())


def test_enter_sends_alt_enter_adds_a_line_and_shift_tab_switches_mode(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [], mode="default")
    assert _typed(agent, "first line\x1b\rsecond line\r") == "first line\nsecond line"
    assert _typed(agent, "\x1b[Zok\r") == "ok"
    assert agent.permission_mode == "acceptEdits"
    (in_tmp_cwd / "calculator.py").write_text("", encoding="utf-8")
    assert _typed(agent, "/add calc\t", "\r") == "/add calculator.py"
    assert _typed(agent, "fix @calc\t", "\r") == "fix @calculator.py"
    assert _typed(agent, "/stat\t", "\r") == "/status"


def test_the_status_line_shows_model_mode_and_files(monkeypatch, in_tmp_cwd):
    (in_tmp_cwd / "a.py").write_text("x = 1\n", encoding="utf-8")
    agent = _agent(monkeypatch, [])
    agent.add_context_files(["a.py"])
    line = terminal_input.status_line(agent)
    assert line.startswith("deepseek-v4-flash │ accept edits")
    assert "+a.py" in line and "$0.0000" in line


def test_confirm_answers_parse_in_any_language():
    opts = ["once", "session", "project", "deny"]
    assert main.parse_confirm_answer("s", opts) == {"choice": "session", "reason": ""}
    assert main.parse_confirm_answer("n: use the venv", opts) == {"choice": "deny", "reason": "use the venv"}
    assert main.parse_confirm_answer("p", ["once", "deny"])["choice"] == "deny"
    assert main.confirm_prompt(["once", "deny"]).endswith("[y/n]: ")


# ─── modes, files in the conversation, answers ──────────────


def test_shift_tab_cycles_modes_and_plan_mode_reaches_the_model(monkeypatch):
    agent = _agent(monkeypatch, [], mode="default")
    agent._openai_messages = [{"role": "system", "content": agent._system_prompt}]
    assert agent.next_permission_mode() == "acceptEdits"
    assert agent.next_permission_mode() == "plan"
    assert agent._openai_messages[0]["content"] == agent._system_prompt
    assert "plan" in agent._openai_messages[0]["content"].lower()
    assert agent.next_permission_mode() == "default"
    assert agent._openai_messages[0]["content"] == agent._base_system_prompt
    # Leaving plan mode for another mode lands in that mode.
    agent.set_permission_mode("plan")
    assert agent.set_permission_mode("acceptEdits") == "acceptEdits"
    assert agent.set_permission_mode("nonsense") == "acceptEdits"


def test_added_files_are_sent_once_and_join_the_scope(monkeypatch, in_tmp_cwd):
    (in_tmp_cwd / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    agent = _agent(monkeypatch, [_final("ok"), _final("ok again")])
    added, missing = agent.add_context_files(["calc.py", "nope.py"])
    assert added == ["calc.py"] and missing == ["nope.py"]

    asyncio.run(agent.chat("fix add"))
    first = agent._openai_messages[1]["content"]
    assert '<file path="calc.py">' in first and "return a + b" in first
    assert agent.ledger.scope == ["calc.py"]

    asyncio.run(agent.chat("and now?"))
    second = [m for m in agent._openai_messages if m["role"] == "user"][-1]["content"]
    assert "calc.py" in second and "return a + b" not in second     # named, not resent

    assert agent.drop_context_files() == ["calc.py"]
    assert agent.context_files == []
    assert agent.last_answer() == "ok again"


def test_terminal_commands_add_drop_mode_and_unknown(monkeypatch, in_tmp_cwd, capsys):
    (in_tmp_cwd / "a.py").write_text("", encoding="utf-8")
    agent = _agent(monkeypatch, [], mode="default")
    assert main.handle_terminal_command(agent, "/add @a.py missing.py")
    assert agent.context_files == ["a.py"]
    assert main.handle_terminal_command(agent, "/drop a.py") and agent.context_files == []
    assert main.handle_terminal_command(agent, "/mode acceptedits") and agent.permission_mode == "acceptEdits"
    assert main.handle_terminal_command(agent, "/status")
    assert main.handle_terminal_command(agent, "/help")
    assert not main.handle_terminal_command(agent, "/undo")       # handled elsewhere
    assert not main.handle_terminal_command(agent, "fix the bug")


def test_init_writes_keelson_md_once(in_tmp_cwd):
    (in_tmp_cwd / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    assert main.handle_init_command("/init")
    text = (in_tmp_cwd / "KEELSON.md").read_text(encoding="utf-8")
    assert "## Rules" in text and "## Verify" in text and "pytest" in text
    assert main.handle_init_command("/init")                      # second time: left alone
    assert (in_tmp_cwd / "KEELSON.md").read_text(encoding="utf-8") == text


# ─── /diff ──────────────────────────────────────────────────


@needs_git
def test_diff_shows_shell_changes_of_the_last_turns(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("KEELSON_CHECKPOINTS", "1")
    checkpoints.reset()
    (work / "a.txt").write_text("one\n", encoding="utf-8")
    store = checkpoints.for_workspace(work)
    store.begin_turn("first")
    (work / "a.txt").write_text("two\n", encoding="utf-8")
    store.end_turn()
    store.begin_turn("second")
    (work / "b.txt").write_text("new\n", encoding="utf-8")
    (work / "img.bin").write_bytes(b"\x00\x01\x02binary")
    store.end_turn()

    text, files = store.diff(1)
    assert sorted(files) == ["b.txt", "img.bin"] and "+new" in text and "a.txt" not in text
    assert "Binary file changed" in text and "\x00" not in text
    text, files = store.diff(2)
    assert sorted(files) == ["a.txt", "b.txt", "img.bin"] and "-one" in text and "+two" in text
    assert store.diff(0) == ("", [])


# ─── resume ─────────────────────────────────────────────────


def test_resume_brings_back_messages_and_the_task_ledger(monkeypatch, in_tmp_cwd):
    first = _agent(monkeypatch, [_final("done")])
    asyncio.run(first.chat("Port the parser. Never touch the tests."))
    first._auto_save()

    rows = main.recent_sessions()
    assert rows[0]["id"] == first.session_id and rows[0]["title"].startswith("Port the parser")

    second = _agent(monkeypatch, [])
    assert second.resume(first.session_id)
    assert second.session_id == first.session_id
    assert second.ledger.constraints[0]["text"] == "Never touch the tests."
    assert second.last_answer() == "done"
    assert not second.resume("missing")


# ─── keelson -p ─────────────────────────────────────────────


def test_print_mode_prints_only_the_answer_and_denies_confirmations(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [_final("The answer.")])
    out = io.StringIO()
    try:
        code = asyncio.run(main.run_print(agent, "what is 2+2?", out=out))
    finally:
        ui.console.file = None
        ui.set_terminal_enabled(True)
    assert code == 0
    assert out.getvalue() == "The answer.\n"
    decision = asyncio.run(agent.confirm_fn("run rm?", {"options": ["once", "deny"]}))
    assert decision["choice"] == "deny" and "-p" in decision["reason"]


def test_print_mode_json_and_exit_codes(monkeypatch, in_tmp_cwd):
    agent = _agent(monkeypatch, [_final("Fixed it.")])

    class Failed:
        status = "failed"

        def event(self):
            return {"status": "failed", "detail": "pytest -q: 1 failed"}

    real_chat = agent.chat

    async def chat_then_fail(prompt):
        await real_chat(prompt)
        agent.last_outcome = Failed()

    monkeypatch.setattr(agent, "chat", chat_then_fail)
    out = io.StringIO()
    try:
        code = asyncio.run(main.run_print(agent, "fix it", "json", out=out))
    finally:
        ui.console.file = None
        ui.set_terminal_enabled(True)
    data = json.loads(out.getvalue())
    assert code == 1
    assert data["result"] == "Fixed it." and data["verify"]["status"] == "failed"
    assert data["usage"]["input_tokens"] == 10 and data["session_id"] == agent.session_id

    broken = _agent(monkeypatch, [])

    async def boom(prompt):
        raise RuntimeError("network down")

    monkeypatch.setattr(broken, "chat", boom)
    out = io.StringIO()
    try:
        code = asyncio.run(main.run_print(broken, "x", "json", out=out))
    finally:
        ui.console.file = None
        ui.set_terminal_enabled(True)
    assert code == 2 and json.loads(out.getvalue())["error"] == "network down"


# ─── streaming Markdown ─────────────────────────────────────


def test_markdown_stream_writes_raw_text_without_a_terminal(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(ui, "_safe_stdout_write", written.append)
    stream = ui._MarkdownStream()
    assert not stream.enabled()               # pytest's stdout is not a terminal
    stream.feed("# Title\n")
    stream.feed("**bold**")
    stream.finish()
    assert written == ["# Title\n", "**bold**"]
    assert not stream.active


def test_markdown_stream_renders_once_in_a_terminal(monkeypatch):
    monkeypatch.setattr(ui._MarkdownStream, "enabled", staticmethod(lambda: True))
    printed: list = []
    monkeypatch.setattr(ui.console, "print", lambda *a, **k: printed.append(a[0] if a else None))
    stream = ui._MarkdownStream()
    try:
        stream.feed("\n\n")                   # leading blank lines are dropped
        assert not stream.active
        stream.feed("Some *text*")
    finally:
        stream.finish()
    assert len(printed) == 1 and type(printed[0]).__name__ == "Markdown"
    assert not stream.active


# ─── helpers ────────────────────────────────────────────────


def _final(text="done"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cache_hit_tokens": 8}}


async def _noop(*args, **kwargs):
    return None


def _agent(monkeypatch, responses, *, mode="acceptEdits"):
    agent = Agent(permission_mode=mode, model="deepseek-v4-flash", api_base="https://x/v1", api_key="k")
    queue = iter(responses)

    async def fake_model():
        return next(queue)

    monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", _noop)
    monkeypatch.setattr(agent, "_build_side_query", lambda **kwargs: None)
    return agent


def test_welcome_hints_stop_after_a_few_sessions():
    shown = [ui._welcome_hint_due() for _ in range(ui._WELCOME_HINT_RUNS + 2)]
    assert shown == [True] * ui._WELCOME_HINT_RUNS + [False, False]
    assert Path(i18n.__file__).exists()

"""Verified completion: the runtime, not the model, decides whether a turn's change was checked."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from keelson import checkpoints, events
from keelson import verify as v
from keelson.agent import Agent

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


# ─── which commands check a project ─────────────────────────


def test_detect_finds_test_runners_from_project_files(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")

    assert v.detect(tmp_path) == ["python -m pytest -q", "pnpm test", "cargo test", "go test ./..."]


def test_detect_finds_test_files_beside_the_code(tmp_path):
    (tmp_path / "pager.py").write_text("def page(): pass\n", encoding="utf-8")
    assert v.detect(tmp_path) == []
    (tmp_path / "test_pager.py").write_text("def test_page(): pass\n", encoding="utf-8")
    assert v.detect(tmp_path) == ["python -m pytest -q"]


def test_detect_ignores_the_npm_placeholder_test_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}), encoding="utf-8")
    assert v.detect(tmp_path) == []


def test_keelson_md_verify_section_wins_over_detection_and_can_be_rewritten(tmp_path):
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    assert v.commands_for(tmp_path) == (["python -m pytest -q"], "detected")

    (tmp_path / "KEELSON.md").write_text(
        "# Notes\n\nKeep it short.\n\n## Verify\n\n- `ruff check .`\n- `pytest -q`\n\n## Style\n\n- `not a check`\n",
        encoding="utf-8")
    assert v.commands_for(tmp_path) == (["ruff check .", "pytest -q"], "KEELSON.md")

    v.write_verify_section(["npm test"], tmp_path)
    text = (tmp_path / "KEELSON.md").read_text(encoding="utf-8")
    assert v.parse_verify_section(text) == ["npm test"]
    assert "Keep it short." in text and "## Style" in text and "ruff check" not in text


def test_a_fenced_block_and_a_chinese_heading_are_understood():
    assert v.parse_verify_section("## 验证\n```\npytest -q\n# comment\ncargo test\n```\n") == ["pytest -q", "cargo test"]
    assert v.parse_verify_section("# Nothing here\n") is None
    assert v.parse_verify_section("## Verify\n\n(none yet)\n") == []


def test_write_verify_section_creates_keelson_md(tmp_path):
    v.write_verify_section(["pytest -q"], tmp_path)
    assert v.commands_for(tmp_path) == (["pytest -q"], "KEELSON.md")


# ─── what ran ────────────────────────────────────────────────


def test_exit_codes_come_from_the_shell_result_text():
    assert v.exit_code_of("3 passed in 0.1s") == 0
    assert v.exit_code_of("Command failed (exit code 2)\nboom") == 2
    assert v.exit_code_of("Command timed out after 300s") is None
    assert v.exit_code_of("Command not run: use delete_files") is None


@pytest.mark.parametrize("output, summary", [
    ("..\n===== 2 failed, 40 passed in 1.2s =====", "2 failed, 40 passed in 1.2s"),
    ("Tests:       1 failed, 9 passed, 10 total", "1 failed, 9 passed, 10 total"),
    ("test result: ok. 3 passed; 0 failed", "ok. 3 passed; 0 failed"),
    ("nothing recognisable", "exit code 1"),
])
def test_summaries_name_what_a_person_recognises(output, summary):
    assert v.summarize(output, 1) == summary


def test_evidence_counts_a_passing_run_only_if_it_came_after_the_last_change():
    ev = v.Evidence()
    ev.record("run_shell", {"command": "pytest -q"}, "5 passed")
    assert ev.passing_after_change().command == "pytest -q"

    ev.record("edit_file", {"file_path": "a.py"}, "Edited a.py")
    assert ev.passing_after_change() is None                       # the pass is stale now
    assert ev.tool_paths == ["a.py"]

    ev.record("run_shell", {"command": "pytest -q"}, "Command failed (exit code 1)\n1 failed")
    assert ev.passing_after_change() is None
    ev.record("run_shell", {"command": "python -m  pytest -q"}, "5 passed")
    assert ev.passing_after_change(["python -m pytest -q"]) is not None
    assert ev.passing_after_change(["npm test"]) is None


def test_a_shell_command_that_is_not_a_test_counts_as_a_change():
    ev = v.Evidence()
    ev.record("run_shell", {"command": "pytest"}, "1 passed")
    ev.record("run_shell", {"command": "sed -i s/a/b/ x.py"}, "")
    assert ev.shell_may_have_changed and ev.passing_after_change() is None

    ev = v.Evidence()
    ev.record("run_shell", {"command": "pytest"}, "1 passed")
    ev.record("run_shell", {"command": "git status"}, "clean", read_only_shell=True)
    assert ev.passing_after_change() is not None


def test_failed_tool_calls_are_not_changes():
    ev = v.Evidence()
    ev.record("write_file", {"file_path": "a.py"}, "Error: not allowed")
    assert ev.last_change == -1 and ev.tool_paths == []


# ─── which files count ──────────────────────────────────────


@pytest.mark.parametrize("path", ["tests/test_x.py", "pkg/x_test.go", "src/a.test.ts", "web/__tests__/b.js",
                                  "conftest.py", "src\\tests\\helpers.py", "spec/models/user_spec.rb"])
def test_test_files_are_recognised(path):
    assert v.is_test_file(path)


@pytest.mark.parametrize("path", ["src/app.py", "src/testing_utils_impl.go", "contest.py", "README.md"])
def test_other_files_are_not_test_files(path):
    assert not v.is_test_file(path)


def test_docs_are_told_apart_from_code():
    assert v.is_doc("README.md") and v.is_doc("docs/guide/setup.py") and v.is_doc("LICENSE")
    assert not v.is_doc("src/app.py") and not v.is_doc("package.json")


def test_whether_a_task_is_about_tests():
    assert v.mentions_tests("fix the failing test in test_api.py")
    assert v.mentions_tests("给登录模块补几个单测")
    assert not v.mentions_tests("rename the config loader")
    assert not v.mentions_tests("把按钮改成蓝色")


# ─── weakened tests ─────────────────────────────────────────

_OLD = """\
import pytest

def test_add():
    assert add(2, 2) == 4
    assert add(0, 0) == 0

def test_sub():
    assert sub(3, 1) == 2
"""


def test_an_untouched_or_strengthened_test_file_is_not_weakened():
    assert v.weakening("t.py", _OLD, _OLD) == []
    assert v.weakening("t.py", _OLD, _OLD + "\ndef test_mul():\n    assert mul(2, 3) == 6\n") == []
    assert v.weakening("t.py", None, _OLD) == []


@pytest.mark.parametrize("new, expected", [
    (_OLD.replace("def test_sub():\n    assert sub(3, 1) == 2\n", ""), "1 test(s) removed"),
    (_OLD.replace("def test_sub", "@pytest.mark.skip\ndef test_sub"), "skip/xfail/only marker(s) added"),
    (_OLD.replace("    assert add(0, 0) == 0\n", ""), "1 assertion(s) removed"),
    (_OLD.replace("assert sub(3, 1) == 2", "assert True"), "an assertion that always passes was added"),
    (_OLD.replace("assert add(2, 2) == 4", "assert add(2, 2) == 5"), "expected value changed for `add(2, 2)`: 4 → 5"),
])
def test_weakening_is_named(new, expected):
    assert any(expected in item for item in v.weakening("t.py", _OLD, new))


def test_deleting_a_test_file_is_weakening():
    assert v.weakening("t.py", _OLD, None) == ["t.py: test file deleted (2 tests)"]


def test_js_skips_and_changed_expectations():
    old = "it('adds', () => {\n  expect(add(1, 1)).toBe(2);\n});\n"
    new = "it.skip('adds', () => {\n  expect(add(1, 1)).toBe(3);\n});\n"
    found = " | ".join(v.weakening("a.test.js", old, new))
    assert "skip" in found and "expected value changed" in found


_NEEDS_GIT = '\ndef _needs_git():\n    """Local helper."""\n    if shutil.which("git") is None:\n        pytest.skip("git 没装")\n    return True\n'
_NEW_GIT_TEST = '\ndef test_clone():\n    if shutil.which("git") is None:\n        pytest.skip("git 没装")\n    assert clone()\n'


def test_a_skip_in_a_new_helper_or_a_new_test_is_not_weakening():
    """Only existing tests being skipped counts: new code skipping itself weakens nothing."""
    assert v.weakening("t.py", _OLD, _OLD.replace("import pytest\n", "import shutil\nimport pytest\n") + _NEEDS_GIT) == []
    assert v.weakening("t.py", _OLD, _OLD.replace("import pytest\n", "import shutil\nimport pytest\n") + _NEW_GIT_TEST) == []
    assert "skip" not in " | ".join(v.weakening("t.py", _OLD, _OLD + _NEEDS_GIT + _NEW_GIT_TEST))


@pytest.mark.parametrize("new", [
    _OLD + '\npytest.skip("git 没装", allow_module_level=True)\n',          # module-level: skips the whole file
    _OLD + '\npytestmark = pytest.mark.skip(reason="git 没装")\n',          # the same, written as a marker
    _OLD.replace("def test_sub", "@pytest.mark.skip(reason='git 没装')\ndef test_sub"),
    _OLD.replace("def test_sub", "@pytest.mark.skipif(not shutil.which('git'), reason='git 没装')\ndef test_sub"),
    _OLD.replace("    assert sub(3, 1) == 2\n",
                 "    if shutil.which('git') is None:\n        pytest.skip('git 没装')\n    assert sub(3, 1) == 2\n"),
], ids=["module-skip", "pytestmark", "decorator", "skipif", "inside-existing-test"])
def test_skipping_an_existing_test_is_weakening(new):
    assert any("skip/xfail/only marker(s) added" in item for item in v.weakening("t.py", _OLD, new))


def test_a_skip_in_a_new_test_method_is_not_weakening_but_on_an_existing_class_it_is():
    old = "import pytest\n\nclass TestMath:\n    def test_add(self):\n        assert 1 + 1 == 2\n"
    new_method = old + "\n    def test_clone(self):\n        pytest.skip('no git')\n"
    assert v.weakening("t.py", old, new_method) == []
    on_class = old.replace("class TestMath", "@pytest.mark.skip(reason='slow')\nclass TestMath")
    assert any("skip" in item for item in v.weakening("t.py", old, on_class))


def test_js_still_counts_every_added_skip():
    old = "it('adds', () => {\n  expect(add(1, 1)).toBe(2);\n});\n"
    assert any("skip" in item for item in v.weakening("a.test.js", old, old.replace("it('adds'", "it.skip('adds'")))


# ─── claims ─────────────────────────────────────────────────


def test_claims_are_found_and_negations_are_not_claims():
    text = ("I fixed the parser. All tests pass now. The build did not pass on Windows. "
            "测试全部通过。我没有运行测试，无法确认测试通过。")
    assert v.claims(text) == ["All tests pass now.", "测试全部通过。"]
    assert v.claims("Renamed the function.") == []


# ─── the agent: end of turn ─────────────────────────────────


def _tool_calls(*specs):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(specs)
    ]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _final(text="done"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


async def _noop(*args, **kwargs):
    return None


def _agent(monkeypatch, responses, *, mode="acceptEdits", shell=None):
    """An agent with a scripted model. Writes really happen; shell results come from ``shell``."""
    monkeypatch.setenv("KEELSON_VERIFY", "1")
    agent = Agent(permission_mode=mode, model="gpt-4o-mini", api_base="https://x/v1", api_key="k")
    queue = iter(responses)

    async def fake_model():
        return next(queue)

    async def fake_exec(name, inp):
        if name == "write_file":
            Path(inp["file_path"]).write_text(inp["content"], encoding="utf-8")
            return f"Wrote {inp['file_path']}"
        if name == "run_shell":
            return (shell or {}).get(inp["command"], "ok")
        return f"result::{name}"

    monkeypatch.setattr(agent, "_call_openai_stream", fake_model)
    monkeypatch.setattr(agent, "_execute_tool_call", fake_exec)
    monkeypatch.setattr(agent, "_run_compression_pipeline", lambda: None)
    monkeypatch.setattr(agent, "_check_and_compact", _noop)
    monkeypatch.setattr(agent, "_refresh_runtime_system_prompt", lambda: None)
    monkeypatch.setattr(agent, "_build_side_query", lambda **kwargs: None)
    monkeypatch.setattr(agent, "_auto_save", lambda: None)
    return agent


def _scripted_checks(monkeypatch, agent, results):
    """Replace the real check runner: each call takes the next list of (exit code, output)."""
    queue = iter(results)
    calls = []

    async def fake_run_checks(commands):
        calls.append(list(commands))
        outcome = next(queue)
        return [v.Run(c, code, 0, "12:00", v.summarize(out, code), out)
                for c, (code, out) in zip(commands, outcome, strict=True)]

    monkeypatch.setattr(agent, "run_checks", fake_run_checks)
    return calls


def _verdict(collected):
    done = [e for e in collected if e.type == events.VERIFY and e.data.get("phase") == "done"]
    return done[-1].data if done else None


@pytest.fixture
def collected():
    seen: list[events.Event] = []
    unsubscribe = events.bus.subscribe(seen.append)
    yield seen
    unsubscribe()


@pytest.fixture
def project(in_tmp_cwd):
    (in_tmp_cwd / "KEELSON.md").write_text("## Verify\n\n- `pytest -q`\n", encoding="utf-8")
    return in_tmp_cwd


def _user_followups(agent):
    return [m["content"] for m in agent._openai_messages if m.get("role") == "user"
            and isinstance(m.get("content"), str) and m["content"].startswith("[Keelson")]


async def test_a_question_that_changes_nothing_is_not_verified(monkeypatch, project, collected):
    agent = _agent(monkeypatch, [_final("The answer is 4.")])
    await agent.chat("what is 2+2?")
    assert _verdict(collected) is None and agent.last_outcome is None


async def test_failing_checks_go_back_to_the_model_until_they_pass(monkeypatch, project, collected):
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 1\n"})),
        _final("Fixed it. All tests pass."),
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _final("Fixed for real."),
    ])
    calls = _scripted_checks(monkeypatch, agent, [
        [(1, "FAILED test_app.py::test_x\n1 failed in 0.1s")],
        [(0, "1 passed in 0.1s")],
    ])

    await agent.chat("make x equal 2")

    assert calls == [["pytest -q"], ["pytest -q"]]
    followups = _user_followups(agent)
    assert len(followups) == 1 and "1 failed in 0.1s" in followups[0] and "exit code 1" in followups[0]
    verdict = _verdict(collected)
    assert verdict["status"] == v.PASSED and "1 passed" in verdict["detail"] and "run by Keelson" in verdict["detail"]
    assert verdict["source"] == "KEELSON.md" and verdict["changed"] == ["app.py"]
    assert verdict["unproven"] == []


async def test_checks_that_keep_failing_end_the_turn_as_failed(monkeypatch, project, collected):
    monkeypatch.setenv("KEELSON_VERIFY_RETRIES", "1")
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 1\n"})),
        _final("Done, tests pass."),
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 3\n"})),
        _final("I could not fix it."),
    ])
    calls = _scripted_checks(monkeypatch, agent, [[(1, "1 failed")], [(1, "1 failed")]])

    await agent.chat("make x equal 2")

    assert len(calls) == 2 and len(_user_followups(agent)) == 1      # one retry, then the verdict
    verdict = _verdict(collected)
    assert verdict["status"] == v.FAILED and "1 failed" in verdict["detail"]
    assert agent.last_outcome.status == v.FAILED


async def test_an_answer_that_changes_nothing_after_a_failure_is_not_checked_again(monkeypatch, project, collected):
    # The model explains the remaining failure is outside the task, without touching anything:
    # Keelson does not re-run the same checks nor push it again; the turn ends as failed.
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _final("Fixed x."),
        _final("test_mean still fails; it was failing before and is not part of this task."),
    ])
    calls = _scripted_checks(monkeypatch, agent, [[(1, "1 failed, 1 passed")]])

    await agent.chat("make x equal 2")

    assert len(calls) == 1 and len(_user_followups(agent)) == 1
    assert "do not widen the task" in _user_followups(agent)[0]
    assert _verdict(collected)["status"] == v.FAILED


async def test_an_answer_that_already_reports_the_failure_is_not_pushed_back(monkeypatch, project, collected):
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _final("Fixed add(). test_mean also fails, but that is a separate bug; I left it alone."),
    ])
    calls = _scripted_checks(monkeypatch, agent, [[(1, "1 failed, 1 passed")]])

    await agent.chat("fix add()")

    assert len(calls) == 1 and _user_followups(agent) == []
    assert _verdict(collected)["status"] == v.FAILED


def test_acknowledging_a_failure_is_told_apart_from_claiming_success():
    assert v.acknowledges_failure("test_mean still fails; I left it.")
    assert v.acknowledges_failure("还有一个测试没通过，与本任务无关。")
    assert v.acknowledges_failure("One failure is pre-existing and unrelated to this change.")
    assert not v.acknowledges_failure("Fixed the failing test. All tests pass now.")
    assert not v.acknowledges_failure("Fixed the failing test_add by correcting the operator.")
    assert not v.acknowledges_failure("修复了导致测试失败的 bug。")
    assert not v.acknowledges_failure("Fixed x.")


def test_the_same_command_spelled_two_ways_is_recognised():
    assert v.same_command(r'& "C:\py\python.exe" -m pytest  -q', "C:/py/python.exe -m pytest -q")
    assert not v.same_command("pytest -q", "pytest -x")


async def test_a_check_the_agent_already_passed_is_not_run_again(monkeypatch, project, collected):
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _tool_calls(("run_shell", {"command": "pytest -q"})),
        _final("Done. Tests pass."),
    ], shell={"pytest -q": "3 passed in 0.2s"}, mode="bypassPermissions")
    calls = _scripted_checks(monkeypatch, agent, [])

    await agent.chat("make x equal 2")

    assert calls == []
    verdict = _verdict(collected)
    assert verdict["status"] == v.PASSED and "run by the agent" in verdict["detail"] and verdict["unproven"] == []


async def test_a_claim_without_a_passing_run_is_flagged(monkeypatch, in_tmp_cwd, collected):
    # No KEELSON.md and nothing to detect: the change cannot be checked.
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _final("Changed x. All tests pass."),
    ])
    await agent.chat("make x equal 2")

    verdict = _verdict(collected)
    assert verdict["status"] == v.UNVERIFIED and "/verify init" in verdict["detail"]
    assert verdict["unproven"] == ["All tests pass."]


async def test_a_docs_only_change_is_marked_unverified_without_running_anything(monkeypatch, project, collected):
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "README.md", "content": "# hi\n"})),
        _final("Updated the readme."),
    ])
    calls = _scripted_checks(monkeypatch, agent, [])
    await agent.chat("fix the readme title")
    assert calls == [] and _verdict(collected)["detail"] == "only documentation changed"


async def test_plan_mode_and_the_off_switch_skip_verification(monkeypatch, project, collected):
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _final("done"),
    ])
    agent._verify_enabled = False
    await agent.chat("make x equal 2")
    assert _verdict(collected) is None


async def test_run_checks_runs_real_commands_and_refuses_unsafe_ones(monkeypatch, in_tmp_cwd, collected):
    if " " in sys.executable:
        pytest.skip("the interpreter path has a space")
    (in_tmp_cwd / "ok.py").write_text("print('7 passed in 0.01s')\n", encoding="utf-8")
    (in_tmp_cwd / "bad.py").write_text("import sys\nprint('1 failed')\nsys.exit(3)\n", encoding="utf-8")
    agent = _agent(monkeypatch, [], mode="bypassPermissions")

    runs = await agent.run_checks([f"{sys.executable} ok.py", f"{sys.executable} bad.py", "rm -rf build"])

    assert [r.exit_code for r in runs] == [0, 3, None]
    assert runs[0].summary == "7 passed in 0.01s" and runs[1].summary == "1 failed"
    assert runs[2].summary.startswith("not run by Keelson")
    assert [e.data["phase"] for e in collected if e.type == events.VERIFY] == ["running"]


async def test_a_check_the_gate_would_ask_about_is_reported_not_run(monkeypatch, project, collected):
    # Checkpoints are off in tests, so a command that may change files needs a person's OK:
    # Keelson does not run it behind their back, and the turn is not called verified.
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "app.py", "content": "x = 2\n"})),
        _final("Done."),
    ], mode="default")
    await agent.chat("make x equal 2")

    verdict = _verdict(collected)
    assert verdict["status"] == v.UNVERIFIED
    assert verdict["runs"][0]["summary"].startswith("not run by Keelson")


# ─── the agent: tests are guarded ───────────────────────────


@pytest.fixture
def with_test_file(project):
    (project / "tests").mkdir()
    (project / "tests" / "test_app.py").write_text(_OLD, encoding="utf-8")
    return project


def test_changing_an_existing_test_needs_an_ok_when_the_task_is_not_about_tests(monkeypatch, with_test_file):
    agent = _agent(monkeypatch, [])
    agent._task_about_tests = False
    target = str(Path("tests") / "test_app.py")

    perm = agent._gate("c1", "write_file", {"file_path": target, "content": "x"})
    assert perm["action"] == "confirm" and perm["rules"] == ["keelson(test-edits)"]
    assert "does not mention tests" in perm["reason"]

    # Adding lines only, a brand-new test file, and code files are fine.
    old = "def test_sub():"
    assert agent._gate("c2", "edit_file", {"file_path": target, "old_string": old,
                                           "new_string": old + "\n    pass"})["action"] == "allow"
    assert agent._gate("c3", "write_file", {"file_path": "tests/test_new.py", "content": "x"})["action"] == "allow"
    assert agent._gate("c4", "write_file", {"file_path": "app.py", "content": "x"})["action"] == "allow"

    # An OK for the session sticks; the task being about tests also lifts it.
    agent._session_rules.add("keelson(test-edits)") if isinstance(agent._session_rules, set) \
        else agent._session_rules.append("keelson(test-edits)")
    assert agent._gate("c5", "write_file", {"file_path": target, "content": "x"})["action"] == "allow"


def test_the_test_guard_denies_in_dont_ask_and_steps_aside_when_tests_are_the_task(monkeypatch, with_test_file):
    target = str(Path("tests") / "test_app.py")
    agent = _agent(monkeypatch, [], mode="dontAsk")
    agent._task_about_tests = False
    assert agent._gate("c1", "write_file", {"file_path": target, "content": "x"})["action"] == "deny"

    agent = _agent(monkeypatch, [])
    agent._task_about_tests = True
    assert agent._gate("c1", "write_file", {"file_path": target, "content": "x"})["action"] == "allow"

    monkeypatch.setenv("KEELSON_GUARD_TESTS", "0")
    agent._task_about_tests = False
    assert agent._gate("c1", "write_file", {"file_path": target, "content": "x"})["action"] == "allow"


@needs_git
async def test_weakened_tests_are_sent_back_once_then_reported(monkeypatch, with_test_file, collected):
    monkeypatch.setenv("KEELSON_CHECKPOINTS", "1")
    checkpoints.reset()
    weakened = _OLD.replace("assert add(2, 2) == 4", "assert add(2, 2) == 5")
    agent = _agent(monkeypatch, [
        _tool_calls(("write_file", {"file_path": "tests/test_app.py", "content": weakened})),
        _final("Done."),
        _final("I left it."),
    ], mode="bypassPermissions")          # past the guard, so the detector is what is tested
    _scripted_checks(monkeypatch, agent, [[(0, "2 passed")]])

    await agent.chat("make add() return 5 for 2+2")

    followups = _user_followups(agent)
    assert len(followups) == 1 and "expected value changed" in followups[0]
    verdict = _verdict(collected)
    assert verdict["status"] == v.PASSED
    assert any("expected value changed for `add(2, 2)`: 4 → 5" in w for w in verdict["weakened"])

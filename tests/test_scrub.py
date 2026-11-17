"""Traces are scrubbed of machine-specific paths before they are published."""

from __future__ import annotations

import json
from pathlib import Path

from keelson.web.scrub import BACKSLASH, leaks, scrub

WORKSPACE = "C:\\Temp\\keelson-demo-abc\\workspace"


def test_every_spelling_of_a_path_is_rewritten():
    payload = json.dumps({
        "native": WORKSPACE + "\\pager.py",                 # json.dumps doubles the backslashes
        "posix": "C:/Temp/keelson-demo-abc/workspace/pager.py",
        "lower": WORKSPACE.lower() + "\\x.py",
    })
    out = scrub(payload, [(WORKSPACE, "/workspace")])
    data = json.loads(out)  # still valid JSON
    assert data["native"].startswith("/workspace")
    assert data["posix"] == "/workspace/pager.py"
    assert data["lower"].startswith("/workspace")
    assert "keelson-demo-abc" not in out


def test_path_inside_json_inside_json_is_rewritten():
    inner = json.dumps({"file": WORKSPACE + "\\skill.md"})        # a tool result that is itself JSON
    out = scrub(json.dumps({"preview": inner}), [(WORKSPACE, "/workspace")])
    assert "keelson-demo-abc" not in out
    assert json.loads(json.loads(out)["preview"])["file"].startswith("/workspace")


def test_child_directory_is_replaced_before_its_parent():
    base = "C:\\Temp\\run"
    text = json.dumps({"a": base + "\\workspace\\f.py", "b": base + "\\home\\x"})
    out = json.loads(scrub(text, [(base + "\\workspace", "/workspace"), (base, "/tmp/run")]))
    assert out["a"] == "/workspace\\f.py"
    assert out["b"] == "/tmp/run\\home\\x"


def test_escapes_and_urls_are_not_mistaken_for_paths():
    assert leaks(json.dumps({"text": "Guideline:\n- Use bullet points.\nNote:\tdone"})) == []
    assert leaks(json.dumps({"url": "see https://example.org/a/b and file:///tmp/x"})) == []


def test_leaks_reports_what_is_left():
    assert leaks(json.dumps({"path": str(Path.home() / "project")}))
    assert leaks(json.dumps({"out": "D:\\projects\\thing\\a.py"}))
    assert leaks(json.dumps({"out": "D:/projects/thing/a.py"}))
    assert leaks(json.dumps({"env": "sk-" + "a" * 24}))
    assert leaks(json.dumps({"out": "/workspace/pager.py passed"})) == []


def test_username_is_rewritten_only_as_a_whole_word(monkeypatch):
    from keelson.web.scrub import scrub_username

    monkeypatch.setenv("USERNAME", "30802")
    text = "/tmp/pytest-of-30802/pytest-0 took 130802 ms; id 308021; owner=30802."
    assert scrub_username(text) == "/tmp/pytest-of-user/pytest-0 took 130802 ms; id 308021; owner=user."


def test_a_very_short_username_is_left_alone(monkeypatch):
    from keelson.web.scrub import scrub_username

    monkeypatch.setenv("USERNAME", "me")
    assert scrub_username("tell me a name") == "tell me a name"


def test_an_abbreviated_home_path_is_caught_and_rewritten():
    """pytest shortens long strings in assertion messages; inside a JSON tool result that is four backslashes."""
    import json

    from keelson.web.scrub import leaks, scrub_home_fragments

    shortened = "C:" + BACKSLASH * 2 + "Users" + BACKSLASH * 2 + "a...9" + BACKSLASH * 2 + "repo"   # as repr() prints it
    inner = json.dumps({"stdout": f"AssertionError: assert '{shortened}' == ''"})
    line = json.dumps({"type": "tool_result", "data": {"preview": inner}})
    assert BACKSLASH * 8 in line
    assert any("absolute Windows path" in item for item in leaks(line))

    cleaned = scrub_home_fragments(line)
    assert "Users" not in cleaned
    assert not any("absolute Windows path" in item for item in leaks(cleaned))
    json.loads(cleaned)


def test_home_fragments_leave_urls_alone():
    from keelson.web.scrub import scrub_home_fragments

    assert scrub_home_fragments("see https://example.com/Users/guide and C:/Users/alice/x") == \
        "see https://example.com/Users/guide and /home/user/x"


def test_source_code_inside_a_json_tool_result_is_not_a_drive_path():
    """`except E as e:` followed by an indented line, two JSON levels down, is not drive e:."""
    import json

    code = "try:\n    raise ValueError('x')\nexcept ValueError as e:\n            raise e from e   # self-cause\n\nprint(1)\n"
    line = json.dumps({"type": "tool_result", "data": {"preview": json.dumps({"stdout": code})}})
    assert "e:" + BACKSLASH * 2 + "n" in line
    assert not any("absolute Windows path" in item for item in leaks(line))

    # ...while a real path at that depth is still caught, spaces in a folder name included.
    for path in ("D:" + BACKSLASH + "work" + BACKSLASH + "repo", "C:" + BACKSLASH + "Program Files" + BACKSLASH + "Python312"):
        nested = json.dumps({"data": {"preview": json.dumps({"stdout": f"File {path}{BACKSLASH}a.py"})}})
        assert any("absolute Windows path" in item for item in leaks(nested)), path

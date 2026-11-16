"""Traces are scrubbed of machine-specific paths before they are published."""

from __future__ import annotations

import json
from pathlib import Path

from agents.web.scrub import leaks, scrub

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

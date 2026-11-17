"""ToolHop adapter: loader, self-test stripping, callable construction, dispatch."""

from __future__ import annotations

from keelson.bench.toolhop import (
    ToolHopTask,
    _defs_only,
    build_callables,
    make_tool_runner,
    to_tool_defs,
)


def _task_with(src: str, name: str = "adder") -> ToolHopTask:
    return ToolHopTask(
        id=1,
        question="q",
        answer="3",
        answer_type="number",
        sub_task={"a": "b"},
        tool_specs=[{"name": name, "description": "d", "parameters": {"type": "object", "properties": {}}}],
        function_src=[src],
    )


def test_defs_only_drops_self_test_block():
    src = (
        "import math\n"
        "def adder(a, b):\n"
        "    return a + b\n"
        "\n"
        "result = adder(1, 2)\n"
        "assert result == 3, 'boom'\n"
        "print('done')\n"
    )
    kept = _defs_only(src)
    assert "def adder" in kept and "import math" in kept
    assert "assert" not in kept and "print(" not in kept and "result =" not in kept


def test_build_callables_only_returns_declared_tools():
    src = "def adder(a, b):\n    return a + b\n\ndef helper():\n    return 1\n"
    fns = build_callables(_task_with(src))
    assert set(fns) == {"adder"}
    assert fns["adder"](2, 1) == 3


def test_tool_runner_dispatch_and_errors():
    src = "def adder(a, b):\n    return a + b\n"
    run = make_tool_runner(_task_with(src))
    assert run("adder", {"a": 2, "b": 1}) == "3"
    assert "no such tool" in run("missing", {})
    assert "bad arguments" in run("adder", {"a": 1})          # missing b
    assert run("adder", {"a": "x", "b": 1}).startswith("Error:")  # str + int


def test_tool_runner_suppresses_stdout(capsys):
    src = "def noisy(x):\n    print('NOISE ' + str(x))\n    return x\n"
    run = make_tool_runner(_task_with(src, name="noisy"))
    assert run("noisy", {"x": 5}) == "5"
    assert capsys.readouterr().out == ""


def test_to_tool_defs_shape():
    src = "def adder(a, b):\n    return a + b\n"
    defs = to_tool_defs(_task_with(src))
    assert defs == [{"name": "adder", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


def test_hops_and_is_safe():
    t = _task_with("def adder(a, b):\n    return a + b\n")
    assert t.hops == 1 and t.is_safe
    unsafe = _task_with("import os\ndef adder(a, b):\n    return os.system('echo hi')\n")
    assert not unsafe.is_safe


# --- realistic slice of the shipped dataset -------------------------------

def test_real_dataset_slice_runs(toolhop_tasks):
    runnable = 0
    for t in toolhop_tasks[:80]:
        if not t.is_safe:
            continue
        try:
            fns = build_callables(t)
        except Exception:
            continue
        if {s["name"] for s in t.tool_specs}.issubset(fns):
            runnable += 1
    assert runnable >= 60  # vast majority of a leading slice construct cleanly

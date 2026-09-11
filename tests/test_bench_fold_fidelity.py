"""Fold-fidelity probe extraction + grading (model-free parts)."""

from __future__ import annotations

from agents.bench.fold_fidelity import Probe, extract_probes, grade, score

_MSGS = [
    {"role": "user", "content": "Fix the off-by-one in pagination and add a test"},
    {"role": "assistant", "content": [
        {"type": "tool_use", "name": "edit_file", "input": {"file_path": "src/pager.py"}},
    ]},
    {"role": "user", "content": [{"type": "tool_result", "content": "Error: file not found: src/pager.py"}]},
    {"role": "assistant", "content": "The path was wrong. Next I will search for the real pager module."},
    {"role": "assistant", "content": [
        {"type": "tool_use", "name": "run_shell", "input": {"command": "pytest tests/test_pager.py -q"}},
    ]},
]


def test_probes_cover_each_memory_class():
    probes = extract_probes(_MSGS)
    classes = {p.memory_class for p in probes}
    assert classes == {"episode", "tool", "working"}
    assert any("pager.py" in p.answer for p in probes if p.memory_class == "tool")


def test_openai_style_tool_error_also_extracted():
    msgs = [
        {"role": "user", "content": "run the build"},
        {"role": "tool", "content": "Command failed (exit code 1): traceback ..."},
    ]
    probes = extract_probes(msgs)
    assert any("fail" in p.question.lower() for p in probes)


def test_grade_containment_and_token_overlap():
    assert grade(Probe("q", "src/pager.py", "tool"), "the agent edited src/pager.py") is True
    assert grade(Probe("q", "src/pager.py", "tool"), "it changed the config file") is False
    assert grade(Probe("q", "pytest tests/test_pager.py -q", "tool"),
                 "ran pytest on tests/test_pager.py") is True


def test_score_perfect_vs_empty():
    probes = extract_probes(_MSGS)
    assert score(probes, [p.answer for p in probes])["retention"] == 1.0
    empty = score(probes, [""] * len(probes))
    assert empty["retention"] == 0.0
    assert set(empty["by_class"]) == {"episode", "tool", "working"}

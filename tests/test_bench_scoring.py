"""ToolHop answer scoring, per answer_type."""

from __future__ import annotations

import pytest

from keelson.bench.toolhop import ToolHopTask, score


def _t(answer: str, answer_type: str) -> ToolHopTask:
    return ToolHopTask(id=0, question="q", answer=answer, answer_type=answer_type, sub_task={})


@pytest.mark.parametrize("gold,pred,ok", [
    ("656", "The answer is 656.", True),
    ("656", "656", True),
    ("656", "657", False),
    ("1,024", "1024", True),
    ("3.0", "3", True),
    ("-137", "the result is -137", True),
])
def test_number(gold, pred, ok):
    assert score(_t(gold, "number"), pred) is ok


@pytest.mark.parametrize("gold,pred,ok", [
    ("2024-05-01", "2024-05-01", True),
    ("2024-05-01", "May 1, 2024", True),
    ("2024-05-01", "01/05/2024", False),   # ambiguous m/d vs d/m -> not equal
    ("1959-01-01", "1959", False),
])
def test_date(gold, pred, ok):
    assert score(_t(gold, "date"), pred) is ok


@pytest.mark.parametrize("gold,pred,ok", [
    ("Thomas", "The first name is Thomas.", True),
    ("Thomas", "thomas", True),
    ("Thomas", "Thompson", False),
    ("New York", "he lives in new york", True),
])
def test_string(gold, pred, ok):
    assert score(_t(gold, "string"), pred) is ok


@pytest.mark.parametrize("gold,pred,ok", [
    ("k", "K", True),
    ("k", "the letter is k", True),
    ("k", "q", False),
])
def test_letter(gold, pred, ok):
    assert score(_t(gold, "letter"), pred) is ok


def test_none_prediction_is_wrong():
    assert score(_t("5", "number"), None) is False

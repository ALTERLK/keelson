"""Span tracing sink + summary."""

from __future__ import annotations

import time

import pytest

from agents import trace


@pytest.fixture(autouse=True)
def _trace_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("KEELSON_TRACE", "1")
    monkeypatch.setenv("KEELSON_TRACE_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.delenv("KEELSON_SPECULATIVE", raising=False)
    trace.reset_for_tests()
    yield
    trace.reset_for_tests()


def test_disabled_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("KEELSON_TRACE", "0")
    trace.reset_for_tests()
    with trace.span("x"):
        pass
    assert not (tmp_path / "t.jsonl").exists()


def test_span_and_summary(tmp_path):
    with trace.span("model_stream", turn=1):
        time.sleep(0.01)
    with trace.span("model_stream", turn=2):
        time.sleep(0.005)
    trace.emit("tool_exec", 3.0, tool="read_file")

    rep = trace.summarize(tmp_path / "t.jsonl")
    assert rep["model_stream"]["count"] == 2
    assert rep["model_stream"]["total_ms"] >= 14
    assert rep["tool_exec"]["count"] == 1
    # sorted by total time desc
    assert list(rep)[0] == "model_stream"


def test_speculative_flag():
    assert trace.speculative_enabled() is True


def test_speculative_flag_off(monkeypatch):
    monkeypatch.setenv("KEELSON_SPECULATIVE", "0")
    assert trace.speculative_enabled() is False

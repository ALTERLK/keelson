"""Trace recording and loading."""

from __future__ import annotations

import json

from keelson import events
from keelson.events import EventBus
from keelson.web.recorder import TraceRecorder, describe_trace, find_trace, list_traces, load_trace


def _record(path, emit_fn, **meta):
    bus = EventBus()
    recorder = TraceRecorder(path, meta=meta)
    bus.subscribe(recorder)
    emit_fn(bus)
    recorder.close()
    return recorder


def test_a_recorded_run_round_trips(tmp_path):
    path = tmp_path / "run.jsonl"

    def run(bus):
        bus.emit(events.USER_MESSAGE, text="fix the bug")
        bus.emit(events.TOOL_CALL, id="c1", name="read_file", input={"file_path": "a.py"}, summary="a.py")
        bus.emit(events.TOOL_RESULT, id="c1", name="read_file", ok=True, preview="x", chars=1)
        bus.emit(events.TURN_END)

    _record(path, run, title="demo", model="m")

    trace = load_trace(path)
    assert trace["meta"]["title"] == "demo"
    assert [e["type"] for e in trace["events"]] == [events.USER_MESSAGE, events.TOOL_CALL, events.TOOL_RESULT, events.TURN_END]
    assert trace["events"][0]["t"] == 0.0
    assert all(a["t"] <= b["t"] for a, b in zip(trace["events"], trace["events"][1:], strict=False))

    summary = describe_trace(path)
    assert (summary["id"], summary["title"], summary["events"], summary["tool_calls"]) == ("run", "demo", 4, 1)


def test_terminal_only_prompts_are_not_recorded(tmp_path):
    path = tmp_path / "run.jsonl"
    _record(path, lambda bus: (bus.emit(events.CONFIRM_PROMPT, message="rm x"), bus.emit(events.TURN_END)))
    assert [e["type"] for e in load_trace(path)["events"]] == [events.TURN_END]


def test_a_session_with_no_events_leaves_no_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    _record(path, lambda bus: None)
    assert not path.exists()


def test_title_falls_back_to_the_first_prompt(tmp_path):
    path = tmp_path / "run.jsonl"
    _record(path, lambda bus: bus.emit(events.USER_MESSAGE, text="explain the loop"))
    assert describe_trace(path)["title"] == "explain the loop"


def test_a_torn_last_line_is_tolerated(tmp_path):
    path = tmp_path / "run.jsonl"
    _record(path, lambda bus: bus.emit(events.USER_MESSAGE, text="hi"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"t": 1.0, "type": "tool_c')  # process died mid-write
    assert len(load_trace(path)["events"]) == 1


def test_listing_prefers_earlier_directories_and_skips_missing_ones(tmp_path):
    shipped, local = tmp_path / "shipped", tmp_path / "local"
    shipped.mkdir()
    local.mkdir()
    for directory, title in ((shipped, "shipped copy"), (local, "local copy")):
        _record(directory / "same.jsonl", lambda bus: bus.emit(events.TURN_END), title=title)
    _record(local / "only-local.jsonl", lambda bus: bus.emit(events.TURN_END))

    rows = list_traces([shipped, tmp_path / "does-not-exist", local])
    assert {row["id"]: row["title"] for row in rows} == {"same": "shipped copy", "only-local": "only-local"}


def test_trace_ids_are_matched_against_real_files_not_joined_into_paths(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    (tmp_path / "secret.jsonl").write_text(json.dumps({"type": "x"}), encoding="utf-8")
    _record(traces / "ok.jsonl", lambda bus: bus.emit(events.TURN_END))

    assert find_trace([traces], "ok") == traces / "ok.jsonl"
    assert find_trace([traces], "../secret") is None
    assert find_trace([traces], "..\\secret") is None

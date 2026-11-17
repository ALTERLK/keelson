"""The static (replay-only) export of the web console."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agents.web.scrub import _DRIVE_PATH

pytest.importorskip("fastapi")

ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_pages", ROOT / "scripts" / "build_pages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_build_is_self_contained(tmp_path):
    builder = _load_builder()
    if not (ROOT / "demo" / "traces").is_dir():
        pytest.skip("demo traces not present in this install")
    out = tmp_path / "site"
    counts = builder.build(out)

    # The page is told there is no server behind it...
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'name="keelson-mode" content="static"' in index
    # ...and everything it would have asked the API for is on disk instead.
    traces = json.loads((out / "data" / "traces.json").read_text(encoding="utf-8"))
    assert counts["traces"] == len(traces) >= 1
    for row in traces:
        trace = json.loads((out / "data" / "traces" / f"{row['id']}.json").read_text(encoding="utf-8"))
        assert len(trace["events"]) == row["events"]
    assert json.loads((out / "data" / "bench.json").read_text(encoding="utf-8"))["available"] is True
    assert len(json.loads((out / "data" / "evolution.json").read_text(encoding="utf-8"))) == 2
    # Pages that only exist when their data does still get a file to read.
    assert "available" in json.loads((out / "data" / "casestudy.json").read_text(encoding="utf-8"))
    perf = json.loads((out / "data" / "perf.json").read_text(encoding="utf-8"))
    assert perf["available"] and "model_stream" in perf["spans"]
    # The fonts ship with the page: no CDN to reach.
    assert (out / "fonts" / "InterVariable.woff2").is_file()
    assert "fonts.googleapis.com" not in index and "cdn." not in index

    # Asset URLs must be relative: the site is served from /<repo>/, not /.
    assert 'src="/' not in index and 'href="/' not in index
    for script in (out / "js").rglob("*.js"):
        assert 'fetch("/' not in script.read_text(encoding="utf-8"), script.name


def test_shipped_traces_carry_no_machine_specific_paths():
    traces = ROOT / "demo" / "traces"
    if not traces.is_dir():
        pytest.skip("demo traces not present in this install")
    for path in traces.glob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        # The drive-letter check is the scrubber's own: a bare `:\\` also matches Python
        # source quoted inside a tool result ("def f():" followed by a line break).
        assert "Users" not in text and not _DRIVE_PATH.search(text), path.name
        for line in text.splitlines():
            json.loads(line)

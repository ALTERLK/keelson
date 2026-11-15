"""Export the web console as a static, replay-only site (for GitHub Pages).

The console reads everything through ``getJSON(name)``. Served by
``keelson web`` that is ``/api/<name>``; here the same JSON is written to
``data/<name>.json`` next to a copy of the static files, and ``index.html`` is
marked ``static`` so the front end reads from there. No server is involved, so
the hosted page can only replay recorded traces.

    python scripts/build_pages.py            # writes ./site
    python scripts/build_pages.py --out dist
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.web import data  # noqa: E402
from agents.web.recorder import list_traces, load_trace  # noqa: E402
from agents.web.server import STATIC_DIR  # noqa: E402

LIVE_META = '<meta name="keelson-mode" content="live">'
STATIC_META = '<meta name="keelson-mode" content="static">'


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8", newline="\n")


def build(out: Path) -> dict[str, int]:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC_DIR, out, ignore=shutil.ignore_patterns("__pycache__"))

    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    if LIVE_META not in html:
        raise SystemExit("index.html no longer carries the keelson-mode meta tag")
    index.write_text(html.replace(LIVE_META, STATIC_META), encoding="utf-8", newline="\n")

    traces = list_traces([data.DEMO_TRACES_DIR])
    if not traces:
        raise SystemExit("no traces under demo/traces — the hosted demo would be empty")
    _write_json(out / "data" / "traces.json", traces)
    for row in traces:
        _write_json(out / "data" / "traces" / f"{row['id']}.json", load_trace(data.DEMO_TRACES_DIR / f"{row['id']}.jsonl"))

    bench = data.bench_overview()
    lineages = data.evolution_lineages()
    _write_json(out / "data" / "bench.json", bench)
    _write_json(out / "data" / "evolution.json", lineages)

    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as they are
    return {"traces": len(traces), "lineages": len(lineages), "bench": int(bool(bench.get("available")))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default="site")
    args = parser.parse_args()
    out = (ROOT / args.out).resolve()
    counts = build(out)
    files = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"built {out.relative_to(ROOT)}: {files} files, {counts['traces']} traces, {counts['lineages']} lineages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

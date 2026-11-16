"""Where the time goes: summarise the span events in recorded traces.

Every step of the loop publishes a ``span`` event with its duration — the model
call, the permission check, the tool, skill retrieval, context folding, and the
wait for a person to answer a permission prompt. This script adds them up over
a set of traces and prints one table.

    python scripts/perf_report.py                      # demo/traces
    python scripts/perf_report.py .keelson/traces      # your own sessions
    python scripts/perf_report.py --markdown           # table for the docs

The interesting number is the runtime's own overhead: everything except the
model and the tools themselves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.web import data  # noqa: E402

ORDER = ["model_stream", "tool", "permission", "skill_retrieval", "fold", "confirm_wait"]
LABELS = {
    "model_stream": "model call",
    "tool": "tool execution",
    "permission": "permission check",
    "skill_retrieval": "skill retrieval",
    "fold": "context folding",
    "confirm_wait": "waiting for the user",
}


def _ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    return f"{value:.0f} ms" if value >= 10 else f"{value:.2f} ms"


def render(summary: dict, *, markdown: bool) -> str:
    spans = summary["spans"]
    rows = [(LABELS.get(name, name), spans[name]) for name in ORDER if name in spans]
    rows += [(name, span) for name, span in spans.items() if name not in ORDER]
    header = ["step", "count", "p50", "p95", "total", "share"]
    table = [[label, str(s["count"]), _ms(s["p50_ms"]), _ms(s["p95_ms"]), _ms(s["total_ms"]),
              "—" if label == LABELS["confirm_wait"] else f"{s['share'] * 100:.2f}%"] for label, s in rows]

    if markdown:
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] + ["---:"] * (len(header) - 1)) + "|"]
        lines += ["| " + " | ".join(row) + " |" for row in table]
    else:
        widths = [max(len(row[i]) for row in [header, *table]) for i in range(len(header))]
        fmt = lambda row: "  ".join(cell.ljust(w) if i == 0 else cell.rjust(w) for i, (cell, w) in enumerate(zip(row, widths, strict=True)))  # noqa: E731
        lines = [fmt(header), "  ".join("-" * w for w in widths), *map(fmt, table)]

    overhead = sum(spans[name]["total_ms"] for name in ("permission", "skill_retrieval") if name in spans)
    accounted = sum(s["total_ms"] for name, s in spans.items() if name != "confirm_wait")
    lines.append("")
    lines.append(f"{summary['traces']} trace(s). Runtime overhead (permission checks + skill retrieval): "
                 f"{_ms(overhead)} of {_ms(accounted)} = {overhead / accounted * 100:.3f}%." if accounted else "no spans")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("directories", nargs="*", default=[str(data.DEMO_TRACES_DIR)])
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    summary = data.perf_summary([Path(directory) for directory in args.directories])
    if not summary["available"]:
        print("no span events in those traces (they were recorded before v0.3.0)")
        return 1
    print(render(summary, markdown=args.markdown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

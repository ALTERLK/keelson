#!/usr/bin/env python3
"""Fetch benchmark datasets into ./data/.

Only ToolHop is wired up (it is all the offline bench runner needs). Everything
else is listed in data/README.md with its upstream location.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

TOOLHOP_URL = (
    "https://huggingface.co/datasets/bytedance-research/ToolHop/resolve/main/data/ToolHop.json"
)


def fetch_toolhop() -> None:
    dest = Path("data/ToolHop/ToolHop.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"{dest} already present ({dest.stat().st_size / 1e6:.1f} MB)")
        return
    print(f"downloading ToolHop -> {dest}")
    urllib.request.urlretrieve(TOOLHOP_URL, dest)  # noqa: S310 - known https host
    print(f"done ({dest.stat().st_size / 1e6:.1f} MB)")


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "toolhop"
    if which == "toolhop":
        fetch_toolhop()
        return 0
    print(f"unknown dataset {which!r}; see data/README.md", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

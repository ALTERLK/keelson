
#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents import paths


def _session_dir() -> Path:
    # Per-project so --resume never resurrects another project's conversation.
    return paths.user_sessions_dir() / paths.project_hash()



def _ensure_dir() -> None:
    _session_dir().mkdir(parents=True, exist_ok=True)


def get_project_session_dir() -> Path:
    d = paths.project_sessions_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(session_id: str, data: dict[str, Any]) -> None:
    _ensure_dir()
    (_session_dir() / f"{session_id}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
        newline="\n",
    )


def save_folded_session_memory(session_id: str, record: dict[str, Any]) -> None:
    d = get_project_session_dir()
    line = json.dumps(record, ensure_ascii=False, default=str)
    with (d / f"{session_id}.folded-memory.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    (d / f"{session_id}.folded-memory.latest.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def load_session(session_id: str) -> dict[str, Any] | None:
    path = _session_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions() -> list[dict[str, Any]]:
    _ensure_dir()
    results = []
    for f in _session_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "metadata" in data:
                results.append(data["metadata"])
        except Exception:
            pass
    return results


def get_latest_session_id() -> str | None:
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
    return sessions[0].get("id")

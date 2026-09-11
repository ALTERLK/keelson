"""Read + roll back the SKILL.md evolution history.

``evolve_skill_file`` already snapshots the *entire* pre-edit ``SKILL.md`` into
``<evolution_dir>/history/<slug>.jsonl`` before every change. This module is the
missing read side: inspect versions, diff the current file against the last
snapshot, and restore a previous version (itself snapshotted first, so a
rollback is reversible).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from agents.frontmatter import format_frontmatter, parse_frontmatter
from agents.skill_evolution import (
    HISTORY_DIR,
    USAGE_LOG,
    _append_jsonl,
    _bump_patch,
    _safe_skill_slug,
    _utc_now,
    get_evolution_dir,
    resolve_skill_file,
)


@dataclass
class Snapshot:
    index: int
    time: str
    version: str
    actor: str
    lesson: str
    content: str


def _history_path(skill_name: str):
    return get_evolution_dir() / HISTORY_DIR / f"{_safe_skill_slug(skill_name)}.jsonl"


def list_history(skill_name: str) -> list[Snapshot]:
    """Snapshots for a skill, oldest first. Empty if it was never evolved."""
    path = _history_path(skill_name)
    if not path.is_file():
        return []
    out: list[Snapshot] = []
    import json

    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(
            Snapshot(
                index=i,
                time=str(row.get("time", "")),
                version=str(row.get("version", "")),
                actor=str(row.get("actor", "")),
                lesson=str(row.get("lesson", "")),
                content=str(row.get("content", "")),
            )
        )
    return out


def _pick(snaps: list[Snapshot], version: str | None) -> Snapshot | None:
    if not snaps:
        return None
    if version is None:
        return snaps[-1]
    for s in reversed(snaps):
        if s.version == version:
            return s
    return None


def diff_current_vs_snapshot(skill_name: str, version: str | None = None) -> dict[str, Any]:
    """Unified diff: the snapshot (old) -> the live SKILL.md (new)."""
    skill_file = resolve_skill_file(skill_name)
    if not skill_file:
        return {"ok": False, "error": f"Skill not found: {skill_name}"}
    snaps = list_history(skill_name)
    snap = _pick(snaps, version)
    if snap is None:
        return {"ok": False, "error": f"No history snapshot for {skill_name}"
                + (f" at version {version}" if version else "")}
    current = skill_file.read_text(encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            snap.content.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"{skill_name}@{snap.version} ({snap.time})",
            tofile=f"{skill_name}@current",
        )
    )
    return {
        "ok": True,
        "skill": skill_name,
        "from_version": snap.version,
        "to_version": parse_frontmatter(current).meta.get("version", ""),
        "diff": diff or "(identical)",
        "snapshots": len(snaps),
    }


def rollback(skill_name: str, version: str | None = None, *, actor: str = "user") -> dict[str, Any]:
    """Restore ``skill_name`` to a previous snapshot's body.

    The current file is snapshotted first (so the rollback can itself be rolled
    back), the version patch is bumped, and an ``event: "rollback"`` row is
    written to ``usage.jsonl``.
    """
    skill_file = resolve_skill_file(skill_name)
    if not skill_file:
        return {"ok": False, "error": f"Skill not found: {skill_name}"}
    snaps = list_history(skill_name)
    target = _pick(snaps, version)
    if target is None:
        return {"ok": False, "error": f"No history snapshot to roll back to for {skill_name}"
                + (f" at version {version}" if version else "")}

    current_raw = skill_file.read_text(encoding="utf-8")
    cur_meta = dict(parse_frontmatter(current_raw).meta)
    resolved_name = cur_meta.get("name") or skill_file.parent.name

    # snapshot the state we are about to overwrite
    _append_jsonl(
        _history_path(resolved_name),
        {
            "time": _utc_now(),
            "event": "snapshot",
            "actor": actor,
            "skill": resolved_name,
            "file": str(skill_file),
            "version": cur_meta.get("version", "0.1.0"),
            "lesson": f"pre-rollback snapshot (rolling back to {target.version})",
            "rationale": "",
            "content": current_raw,
        },
    )

    # rebuild from the target snapshot's body, keep a moving version + provenance
    old = parse_frontmatter(target.content)
    new_meta = dict(old.meta)
    new_meta["name"] = resolved_name
    new_meta["version"] = _bump_patch(cur_meta.get("version"))
    new_meta["last-evolved"] = _utc_now()
    new_meta["rolled-back-from"] = cur_meta.get("version", "")
    new_meta["rolled-back-to"] = target.version
    skill_file.write_text(format_frontmatter(new_meta, old.body), encoding="utf-8")

    event = {
        "event": "rollback",
        "time": _utc_now(),
        "actor": actor,
        "skill": resolved_name,
        "file": str(skill_file),
        "from_version": cur_meta.get("version", ""),
        "to_version": new_meta["version"],
        "restored_from": target.version,
    }
    _append_jsonl(get_evolution_dir() / USAGE_LOG, event)
    return {"ok": True, **event}


def format_history(skill_name: str) -> str:
    snaps = list_history(skill_name)
    if not snaps:
        return f"No evolution history for {skill_name!r}."
    lines = [f"Evolution history for {skill_name} ({len(snaps)} snapshot(s), oldest first):"]
    for s in snaps:
        lesson = (s.lesson[:80] + "…") if len(s.lesson) > 80 else s.lesson
        lines.append(f"  [{s.index}] v{s.version or '?':<7} {s.time or '?':<21} {s.actor or '?':<8} {lesson}")
    lines.append("Roll back with:  /skill-rollback <skill> [version]   (default: latest snapshot)")
    return "\n".join(lines)

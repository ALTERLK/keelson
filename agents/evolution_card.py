"""Evolution Card — a human-readable record of one Skill self-evolution event.

Every online add/merge/quarantine writes one card to
``<evolution_dir>/cards/<slug>-<version>.md`` so the chain

    source conversation evidence
      -> Extractor candidate
      -> Maintainer decision (add | merge | discard + rationale + similar skills)
      -> SKILL.md unified diff
      -> non-inferiority gate result
      -> final version (or: rejected, live file rolled back)

is auditable without reading five JSONL files. This is the "self-evolution is
not a black box" artifact — a rejected candidate is exactly as auditable as an
accepted one, not silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from agents.skill_evolution import _safe_skill_slug, _utc_now, get_evolution_dir


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return dict(obj)
    return {}


def _fmt_messages(messages: list[dict[str, Any]] | None, limit: int = 6) -> str:
    msgs = [m for m in (messages or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not msgs:
        return "_(no conversation window recorded)_"
    lines = []
    for m in msgs[-limit:]:
        role = m.get("role", "?")
        content = str(m.get("content") or "").strip().replace("\n", " ")
        if len(content) > 400:
            content = content[:400] + " …"
        lines.append(f"- **{role}**: {content}")
    return "\n".join(lines)


def render_card(
    *,
    candidate: Any,
    decision: dict[str, Any] | None,
    result: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
    diff: str = "",
) -> str:
    cand = _as_dict(candidate)
    dec = _as_dict(decision) or _as_dict(result.get("decision"))
    action = str(result.get("action") or "?")
    rejected = action == "merge_quarantined"
    skill = str(result.get("skill") or cand.get("name") or "?")
    version = str(result.get("version") or ("rejected" if rejected else "?"))
    gate = _as_dict(result.get("gate"))

    similar = dec.get("similar_skills") or dec.get("candidates") or []
    similar_lines = "\n".join(
        f"  - {s}" for s in (similar if isinstance(similar, list) else [])
    ) or "  - _(none retrieved)_"

    title = f"# Evolution Card — {skill} @ {version}" + (" — REJECTED" if rejected else "")
    parts = [
        title,
        "",
        f"- **When:** {_utc_now()}",
        f"- **Action:** `{action}`",
        f"- **Gate:** `{gate.get('action', 'n/a')}` — {gate.get('reason', '—')}",
        "",
        "## 1. Source conversation evidence",
        "",
        _fmt_messages(messages),
        "",
        f"> Extractor evidence quote: {cand.get('evidence') or '_(none)_'}",
        "",
        "## 2. Extractor candidate",
        "",
        f"- **name:** {cand.get('name', '')}",
        f"- **when_to_use:** {cand.get('when_to_use', '')}",
        f"- **description:** {cand.get('description', '')}",
        "",
        "```",
        str(cand.get("instructions", "")).strip() or "(empty)",
        "```",
        "",
        "## 3. Maintainer decision",
        "",
        f"- **verdict:** {dec.get('action', action)}",
        f"- **rationale:** {dec.get('reason') or dec.get('rationale') or '—'}",
        "- **similar skills considered:**",
        similar_lines,
        "",
        "## 4. SKILL.md change",
        "",
        "```diff",
        (diff.strip() or (
            "(candidate rejected by the gate before it reached the live file — "
            "see the quarantine file below for the rejected text)"
            if rejected else "(new skill — no prior version)"
        )),
        "```",
        "",
        "## 5. Non-inferiority gate",
        "",
        "```json",
        json.dumps(gate or {"action": "n/a"}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    if rejected:
        parts += [
            "## 6. Result → **REJECTED, live file unchanged**",
            "",
            f"- reason: {result.get('error', '')}",
            f"- rejected candidate saved at: `{result.get('quarantine_file', '')}`",
            "- the live SKILL.md was rolled back to its pre-candidate version",
            "",
        ]
    else:
        parts += [
            f"## 6. Result → **{skill} @ {version}**",
            "",
            f"- file: `{result.get('file', '')}`",
            f"- ok: {result.get('ok')}",
            "",
        ]
    return "\n".join(parts)


def write_card(
    *,
    candidate: Any,
    decision: dict[str, Any] | None,
    result: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
) -> Path | None:
    """Render + persist a card for an add/merge/quarantined-merge result. Returns
    the path, or None. A quarantined candidate gets a card too — it's rejected,
    not undocumented."""
    action = str(result.get("action") or "")
    if action not in ("add", "merge", "merge_quarantined"):
        return None
    if action in ("add", "merge") and not result.get("ok"):
        return None

    skill = str(result.get("skill") or "")
    if action == "merge_quarantined":
        version = str(result.get("version") or "rejected-" + _utc_now().replace(":", "").replace("-", ""))
    else:
        version = str(result.get("version") or "0")
    diff = ""
    if action == "merge" and skill:
        try:
            from agents.skill_history import diff_current_vs_snapshot

            d = diff_current_vs_snapshot(skill)
            if d.get("ok"):
                diff = d["diff"]
        except Exception:
            diff = ""

    # thread the resolved version through so the card title and the filename
    # never disagree (result itself usually has no "version" on a rejection)
    card = render_card(
        candidate=candidate, decision=decision, result={**result, "version": version},
        messages=messages, diff=diff,
    )
    root = get_evolution_dir() / "cards"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe_skill_slug(skill)}-{version}.md"
    path.write_text(card, encoding="utf-8", newline="\n")
    return path

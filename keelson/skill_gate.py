"""Non-inferiority gate for Skill evolution.

Before an evolved ``SKILL.md`` replaces the live one, check that its rules do not
regress the skill's own historical behaviour: score the recorded
``latest_assistant`` replies against the rules compiled from the *old* text and
from the *candidate* text, and admit the candidate only if

    candidate.avg_score     >= old.avg_score - epsilon    AND
    candidate.hard_failures  <= old.hard_failures

Otherwise the candidate is written to ``<evolution_dir>/quarantine/`` and the
live file is left untouched. With too few replay samples the gate returns
``ungated`` (the caller decides; the online evolution loop admits + flags it).

Programmatic rules run with no model. LLM-judge rules would need a ``side_query``
and an event loop; the offline gate uses the programmatic subset, which is
exactly the "hard" contract worth gating on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keelson.frontmatter import parse_frontmatter
from keelson.online_skill_eval import (
    _compile_eval_rules,
    _evaluate_rule,
    _lineage_id_for_skill,
    _online_eval_root,
)
from keelson.skill_evolution import _safe_skill_slug, _utc_now, get_evolution_dir

SideQuery = Callable[..., Any]

DEFAULT_EPSILON = 0.0
DEFAULT_MIN_SAMPLES = 1
_HARD_PASS, _SOFT_PASS = 2.0, 1.0


@dataclass
class TextScore:
    n: int
    avg_score: float
    hard_failures: int
    pass_rate: float
    rule_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "avg_score": round(self.avg_score, 4),
            "hard_failures": self.hard_failures,
            "pass_rate": round(self.pass_rate, 4),
            "rule_count": self.rule_count,
        }


# ---------------------------------------------------------------- samples ---

def load_replay_samples(skill_name: str, *, split: str | None = None) -> list[dict[str, Any]]:
    """Recorded conversation windows for a skill (frozen replay pool, else provenance)."""
    lineage = _lineage_id_for_skill(skill_name)
    pool = _online_eval_root() / "datasets" / lineage / "replay_pool.jsonl"
    out: list[dict[str, Any]] = []
    if pool.is_file():
        for line in pool.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if split and row.get("split") != split:
                continue
            if str(row.get("latest_assistant") or "").strip():
                out.append(row)
    if out:
        return out

    prov = get_evolution_dir() / "online_provenance.jsonl"
    if prov.is_file():
        for line in prov.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("skill") != skill_name:
                continue
            msgs = row.get("messages") or []
            latest = ""
            for m in reversed(msgs):
                if isinstance(m, dict) and m.get("role") == "assistant" and str(m.get("content") or "").strip():
                    latest = str(m["content"])
                    break
            if latest:
                out.append({"latest_assistant": latest, "split": "provenance"})
    return out


# ------------------------------------------------------------- evaluation ---

def _skill_dict_from_text(text: str) -> dict[str, Any]:
    parsed = parse_frontmatter(text)
    meta = parsed.meta
    tags = [t.strip() for t in str(meta.get("tags") or "").split(",") if t.strip()]
    return {
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "when_to_use": meta.get("when-to-use") or meta.get("when_to_use", ""),
        "instructions": parsed.body,
        "tags": tags,
    }


def score_text(text: str, samples: list[dict[str, Any]], *, side_query: SideQuery | None = None) -> TextScore:
    skill = _skill_dict_from_text(text)
    rules = _compile_eval_rules(skill, include_llm_rules=False)
    prog_rules = [r for r in rules if str(r.get("kind") or "programmatic") == "programmatic"]
    if not samples or not prog_rules:
        return TextScore(n=len(samples), avg_score=0.0, hard_failures=0, pass_rate=0.0, rule_count=len(prog_rules))

    per_sample_scores: list[float] = []
    hard_failures = 0
    passed = total = 0
    for s in samples:
        response = str(s.get("latest_assistant") or "")
        sample_score = 0.0
        for rule in prog_rules:
            outcome = _evaluate_rule(rule, response)
            total += 1
            if outcome["passed"]:
                passed += 1
                sample_score += _HARD_PASS if outcome["hard"] else _SOFT_PASS
            elif outcome["hard"]:
                hard_failures += 1
        per_sample_scores.append(sample_score)

    avg = sum(per_sample_scores) / len(per_sample_scores)
    return TextScore(
        n=len(samples),
        avg_score=avg,
        hard_failures=hard_failures,
        pass_rate=(passed / total) if total else 0.0,
        rule_count=len(prog_rules),
    )


# -------------------------------------------------------------------- gate ---

def gate_evolution(
    skill_name: str,
    old_text: str,
    new_text: str,
    *,
    epsilon: float = DEFAULT_EPSILON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    prefer_split: str | None = "promotion_test",
    side_query: SideQuery | None = None,
) -> dict[str, Any]:
    """Decide whether ``new_text`` may replace ``old_text`` for ``skill_name``."""
    samples = load_replay_samples(skill_name, split=prefer_split)
    if len(samples) < min_samples:
        samples = load_replay_samples(skill_name)  # any split

    old = score_text(old_text, samples, side_query=side_query)
    new = score_text(new_text, samples, side_query=side_query)

    if len(samples) < min_samples:
        return {
            "action": "ungated",
            "reason": f"only {len(samples)} replay sample(s) (< {min_samples}); admitted but unverified",
            "skill": skill_name,
            "samples": len(samples),
            "old": old.as_dict(),
            "new": new.as_dict(),
        }

    score_ok = new.avg_score >= old.avg_score - epsilon
    hard_ok = new.hard_failures <= old.hard_failures
    if score_ok and hard_ok:
        return {
            "action": "admit",
            "reason": "non-inferior on replay",
            "skill": skill_name,
            "samples": len(samples),
            "delta_score": round(new.avg_score - old.avg_score, 4),
            "old": old.as_dict(),
            "new": new.as_dict(),
        }

    reasons = []
    if not score_ok:
        reasons.append(f"avg_score {new.avg_score:.3f} < {old.avg_score:.3f} - {epsilon}")
    if not hard_ok:
        reasons.append(f"hard_failures {new.hard_failures} > {old.hard_failures}")
    return {
        "action": "quarantine",
        "reason": "; ".join(reasons),
        "skill": skill_name,
        "samples": len(samples),
        "delta_score": round(new.avg_score - old.avg_score, 4),
        "old": old.as_dict(),
        "new": new.as_dict(),
    }


def quarantine_candidate(skill_name: str, new_text: str, decision: dict[str, Any]) -> Path:
    """Persist a rejected candidate + the gate decision, without touching the live file."""
    root = get_evolution_dir() / "quarantine"
    root.mkdir(parents=True, exist_ok=True)
    slug = _safe_skill_slug(skill_name)
    ts = _utc_now().replace(":", "").replace("-", "")
    md = root / f"{slug}-{ts}.md"
    meta = root / f"{slug}-{ts}.json"
    md.write_text(new_text, encoding="utf-8", newline="\n")
    meta.write_text(
        json.dumps({"skill": skill_name, "time": _utc_now(), "decision": decision}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return md

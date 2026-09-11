"""Live demonstration: the online self-evolution pipeline really promotes a
skill and really quarantines one — not just unit-tested (tests/test_skill_gate.py
proves the mechanism deterministically), run for real end to end.

Every skill in this project's actual history has `promoted: true` exactly zero
times (grep .keelson/skill-evolution/ — nothing). That is because the gate only
fires on a skill's *second* evolution (its first evolution has no replay
sample yet, so gate_evolution() returns "ungated" — see skill_gate.py). This
script drives two independent skill lineages through two rounds of real
feedback each, so the second round has a real replay sample and the gate
actually decides:

  lineage A: round 2 doesn't add any new hard rule the round-1 reply violates
             -> intended outcome: admit
  lineage B: round 2 explicitly asks for source citations; the round-1 reply
             never cited one -> intended outcome: quarantine

Both rounds go through the real online_ingest() -> Extractor -> Maintainer ->
evolve_skill(gate=True) pipeline with a real model as side_query. The Maintainer's
exact decision has some LLM variance, so after round 2 each lineage checks
whether it landed on its intended gate outcome; if not, it falls back to a
direct skills.evolve_skill(gate=True, instructions=<fixed text>) call on the
same skill (same mechanism proven in test_skill_gate.py, just with the
candidate text authored here instead of by the Maintainer) so the demo always
produces one real admit and one real quarantine artifact.

All produced artifacts (SKILL.md before/after, Evolution Cards, the quarantine
file, the gate decision) are copied into docs/self-evolution-demo/ — that's
committed evidence, not scratch.

Run:
  python scripts/demo_self_evolution.py --dry-run   # no API key, no model calls,
                                                      # exercises only the artifact
                                                      # plumbing via the fallback path
  python scripts/demo_self_evolution.py              # the real thing, well under 1 yuan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT_ROOT = _REPO_ROOT / "docs" / "self-evolution-demo"

# ---------------------------------------------------------------- scenarios ---

LINEAGE_A = {
    "label": "lineage-a-admit",
    "intent": "admit",
    "hint": "user feedback about how to format PR-change summaries",
    "round1": [
        {"role": "user", "content": "帮我总结一下这次 PR 的改动。"},
        {"role": "assistant", "content": (
            "- 修改了 auth.py 里的登录校验逻辑\n"
            "- 新增了对应的单元测试\n"
            "- 更新了 README 里的接口说明"
        )},
        {"role": "user", "content": "以后总结 PR 改动都用要点列表的形式，保持简洁，不要写大段文字。"},
    ],
    "round2": [
        {"role": "user", "content": "再总结一下这次的改动。"},
        {"role": "assistant", "content": (
            "- 重构了 payment 模块的错误处理\n"
            "- 补充了边界条件的测试"
        )},
        {"role": "user", "content": "每个要点最好用动词开头，比如'修复了'、'新增了'，这样读起来更清楚。"},
    ],
    # used only if the live Maintainer merge doesn't land on "admit" — same
    # mechanism (skills.evolve_skill(gate=True)), hand-authored text instead of
    # LLM-synthesized. Deliberately introduces no new _compile_eval_rules
    # trigger, so it scores >= the round-1 reply on every rule that exists.
    "fallback_instructions": (
        "Summarize PR changes as a bullet list. Keep it concise, no long "
        "paragraphs. Start each bullet with a past-tense verb."
    ),
}

LINEAGE_B = {
    "label": "lineage-b-quarantine",
    "intent": "quarantine",
    "hint": "user feedback about how to format release notes",
    "round1": [
        {"role": "user", "content": "帮我写一下这次发布的 release notes。"},
        {"role": "assistant", "content": (
            "- 修复了分页边界的 bug\n"
            "- 新增了导出 CSV 的功能\n"
            "- 优化了首页加载速度"
        )},
        {"role": "user", "content": "以后写 release notes 用要点列表，保持简洁。"},
    ],
    "round2": [
        {"role": "user", "content": "这次的 release notes 呢？"},
        {"role": "assistant", "content": (
            "- 修复了登录超时的问题\n"
            "- 新增了深色模式"
        )},
        {"role": "user", "content": "以后每一条 release notes 都要标注来源 issue/PR 链接，方便追溯，必须注明来源。"},
    ],
    # deliberately triggers must_cite_sources (a hard, programmatic rule —
    # _compile_eval_rules keys on the literal substring "cite sources", among
    # others) which the round-1 replay reply never satisfies -> the gate must
    # quarantine this. (Must be the literal phrase, not a paraphrase — the
    # rule compiler does substring matching, not semantic matching.)
    "fallback_instructions": (
        "Write release notes as a bullet list. Keep it concise. Every entry "
        "must cite sources — link the originating issue or PR."
    ),
}


# ------------------------------------------------------------------- helpers ---

async def _yes(_summary: str) -> bool:
    return True


def _resolve_api() -> tuple[str | None, str | None, bool]:
    from agents.main import _resolve_api_config

    return _resolve_api_config(None)


def _build_side_query(model: str, api_base: str | None, api_key: str, use_openai: bool):
    from agents.agent import Agent

    agent = Agent(
        permission_mode="bypassPermissions",
        model=model,
        api_base=api_base if use_openai else None,
        anthropic_base_url=api_base if not use_openai else None,
        api_key=api_key,
    )
    return agent._build_side_query(max_tokens=2200)


def _copy_evolution_dir_for(skill_name: str, dest: Path) -> None:
    """Curate: the live SKILL.md, its history snapshots, the evolution cards,
    and any quarantine files that mention this skill, into dest/."""
    from agents.skill_evolution import _safe_skill_slug, get_evolution_dir, resolve_skill_file
    from agents.skill_history import list_history

    dest.mkdir(parents=True, exist_ok=True)
    slug = _safe_skill_slug(skill_name)

    live = resolve_skill_file(skill_name)
    if live and live.is_file():
        shutil.copy2(live, dest / "SKILL.md")

    snaps = list_history(skill_name)
    for i, snap in enumerate(snaps):
        (dest / f"history-{i:02d}-v{snap.version}.md").write_text(
            snap.content, encoding="utf-8", newline="\n"
        )

    evo_dir = get_evolution_dir()
    cards_dir = evo_dir / "cards"
    if cards_dir.is_dir():
        for card in cards_dir.glob(f"{slug}-*"):
            shutil.copy2(card, dest / card.name)

    q_dir = evo_dir / "quarantine"
    if q_dir.is_dir():
        for f in q_dir.glob(f"{slug}-*"):
            shutil.copy2(f, dest / f.name)

    prov = evo_dir / "online_provenance.jsonl"
    if prov.is_file():
        rows = [
            json.loads(line) for line in prov.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("skill") == skill_name
        ]
        (dest / "provenance.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )


# ------------------------------------------------------------------ lineage ---

async def run_lineage(spec: dict, *, side_query, dry_run: bool) -> dict:
    from agents import skills as skills_mod
    from agents.evolution_card import write_card
    from agents.online_skill_evolution import online_ingest

    log: dict = {"label": spec["label"], "intent": spec["intent"], "steps": []}

    if dry_run:
        created = skills_mod.create_skill(
            name=spec["label"], description="dry-run seed skill",
            instructions="Placeholder instructions for --dry-run artifact plumbing.",
        )
        log["steps"].append({"step": "round1 (dry-run seed, no model call)", "result": created})
        skill_name = created.get("skill") or spec["label"]

        # seed a replay sample the same way a real online_ingest() round1 would
        # (record_online_provenance is exactly what it calls internally), so the
        # gate isn't "ungated" when the fallback path runs below.
        skills_mod.record_online_provenance(
            action="add", skill_name=skill_name, result=created, messages=spec["round1"],
        )
        fb = skills_mod.evolve_skill(
            skill_name, lesson=f"dry-run fallback for {spec['label']}",
            rationale="dry-run: no model calls, exercising the fallback path only",
            instructions=spec["fallback_instructions"], gate=True,
        )
        log["steps"].append({"step": "dry-run fallback evolve_skill(gate=True)", "result": fb})
        log["path"] = "dry-run (fallback path only, no model calls)"
        log["final_result"] = fb
        log["gate"] = fb.get("gate")
        if str(fb.get("action")) in ("add", "merge", "merge_quarantined"):
            card_path = write_card(
                candidate={"name": skill_name, "instructions": spec["fallback_instructions"]},
                decision=fb.get("gate"), result=fb, messages=spec["round1"],
            )
            log["fallback_card"] = str(card_path) if card_path else None
    else:
        r1 = await online_ingest(
            messages=spec["round1"], side_query=side_query, confirm_write=_yes, hint=spec["hint"],
        )
        log["steps"].append({"step": "round1 online_ingest", "result": r1})
        if not r1.get("ok") or r1.get("action") not in ("add", "merge"):
            raise RuntimeError(f"{spec['label']}: round1 did not create/merge a skill: {r1}")
        skill_name = r1.get("skill", "")

        r2 = await online_ingest(
            messages=spec["round2"], side_query=side_query, confirm_write=_yes, hint=spec["hint"],
        )
        log["steps"].append({"step": "round2 online_ingest", "result": r2})
        gate_action = (r2.get("gate") or {}).get("action")
        if gate_action == spec["intent"]:
            log["path"] = "natural (Maintainer merge landed on the intended gate outcome)"
            log["final_result"] = r2
            log["gate"] = r2.get("gate")
        else:
            log["path"] = (
                f"fallback (round2 gate action was {gate_action!r}, not {spec['intent']!r}; "
                "driving skills.evolve_skill(gate=True) directly with fixed text)"
            )
            fb = skills_mod.evolve_skill(
                skill_name,
                lesson=f"demo fallback for {spec['label']}",
                rationale="natural Maintainer merge did not land on the intended gate outcome",
                instructions=spec["fallback_instructions"],
                gate=True,
            )
            log["steps"].append({"step": "fallback evolve_skill(gate=True)", "result": fb})
            log["final_result"] = fb
            log["gate"] = fb.get("gate")
            if str(fb.get("action")) in ("add", "merge", "merge_quarantined"):
                card_path = write_card(
                    candidate={"name": skill_name, "instructions": spec["fallback_instructions"]},
                    decision=fb.get("gate"), result=fb, messages=spec["round2"],
                )
                log["fallback_card"] = str(card_path) if card_path else None

    log["skill_name"] = skill_name
    return log


async def run(dry_run: bool) -> int:
    os.environ.setdefault("KEELSON_QUIET", "1")
    os.environ.setdefault("KEELSON_OPENAI_STREAM", "0")
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True) or None)
        load_dotenv(str(_REPO_ROOT / ".env"))
    except Exception:
        pass

    side_query = None
    if not dry_run:
        api_base, api_key, use_openai = _resolve_api()
        if not api_key:
            print("ERROR: no API key in environment (use --dry-run to test plumbing without one)")
            return 2
        model = os.environ.get("MODEL") or "gemini-3.8-flash"
        side_query = _build_side_query(model, api_base, api_key, use_openai)
        print(f"model: {model}")

    prev_cwd = Path.cwd()
    os.environ["KEELSON_AUTO_SKILL_EVOLUTION"] = "0"

    results = []
    for spec in (LINEAGE_A, LINEAGE_B):
        print(f"\n=== {spec['label']} (intent: {spec['intent']}) ===")

        # each lineage gets its OWN scratch KEELSON_HOME + cwd. Sharing one
        # across lineages let the Maintainer's similarity retrieval find
        # lineage A's freshly-created skill and merge lineage B's unrelated
        # round-1 feedback into it instead of creating a second skill --
        # observed once while building this script; isolating per lineage is
        # the fix, matching agents/bench/runner.py::_setup_arm_env's pattern.
        home = tempfile.mkdtemp(prefix=f"keelson-demo-{spec['label']}-home-")
        work = tempfile.mkdtemp(prefix=f"keelson-demo-{spec['label']}-cwd-")
        os.environ["KEELSON_HOME"] = home
        os.chdir(work)
        try:
            from agents.skills import reset_skill_cache
            from agents.tools import reset_permission_cache

            reset_skill_cache()
            reset_permission_cache()

            log = await run_lineage(spec, side_query=side_query, dry_run=dry_run)
            print(f"  skill: {log['skill_name']}")
            print(f"  path: {log.get('path', 'dry-run')}")
            print(f"  gate: {json.dumps(log.get('gate'), ensure_ascii=False)}")
            results.append(log)

            dest = _OUT_ROOT / spec["label"]
            if dest.exists():
                shutil.rmtree(dest)
            _copy_evolution_dir_for(log["skill_name"], dest)
            (dest / "run_log.json").write_text(
                json.dumps(log, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8", newline="\n",
            )
        finally:
            os.chdir(prev_cwd)
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)

    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="keelson-demo-self-evolution")
    ap.add_argument("--dry-run", action="store_true", help="no API key, no model calls; tests artifact plumbing only")
    args = ap.parse_args(argv)
    return asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())

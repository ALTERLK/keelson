"""Probe: can the online skill-evolution path grow a skill from ToolHop?

The benchmark's ``skill_evolved`` arm is meant to run "a skill grown on the
*train* split". This probe checks whether that is even possible on ToolHop by
driving the **real** evolution entry point over real train-task transcripts:

1. run the harness (same Agent config as the ``no_skill`` arm) on N train tasks;
2. hand each transcript to ``extract_online_skill_candidate`` — the first stage
   of ``online_ingest`` — with a model-backed ``side_query``;
3. for any candidate that survives, run ``maintain_online_skill_candidate``
   against a throwaway ``KEELSON_HOME`` and record add / merge / discard.

Expected result: ~0 candidates. ``extract_online_skill_candidate`` keys on
user-feedback turns ("USER turns are the primary evidence … extract only durable
workflow, correction, or repeated constraint"); a ToolHop task is a single
question turn with no feedback, so there is nothing for it to learn. If that
holds, ``skill_evolved`` is byte-identical to ``no_skill`` on this dataset and
the report should say so rather than leave the cell blank.

Run:  python -m agents.bench.skill_evolved_probe --n 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from agents.bench.runner import (
    _HARNESS_SYSTEM_PROMPT,
    _CountingToolRunner,
    _question_prompt,
    _resolve_api,
)
from agents.bench.splits import build_split, load_split
from agents.bench.toolhop import (
    ToolHopTask,
    default_dataset_path,
    load_toolhop,
    to_tool_defs,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _norm_transcript(openai_messages: list[dict]) -> list[dict]:
    """Flatten the agent's OpenAI-shaped history to [{role, content:str}]."""
    out: list[dict] = []
    for m in openai_messages:
        role = m.get("role", "")
        if role == "system":
            continue
        content = m.get("content", "")
        if isinstance(content, list):  # tool-result / multi-part blocks
            parts = []
            for b in content:
                if isinstance(b, dict):
                    parts.append(str(b.get("text") or b.get("content") or b))
                else:
                    parts.append(str(b))
            content = "\n".join(parts)
        elif not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tool_calls = m.get("tool_calls")
        if tool_calls:
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tool_calls)
            content = (content + f"\n[tool_calls: {names}]").strip()
        if content:
            out.append({"role": role, "content": content})
    return out


async def _run_one(task: ToolHopTask, model: str, api_base: str, api_key: str, use_openai: bool):
    from agents.agent import Agent

    tool_runner = _CountingToolRunner(task)
    tool_defs = to_tool_defs(task)
    agent = Agent(
        permission_mode="bypassPermissions",
        model=model,
        max_turns=14,
        api_base=api_base if use_openai else None,
        anthropic_base_url=api_base if not use_openai else None,
        api_key=api_key,
        custom_tools=tool_defs,
        external_tools={d["name"]: tool_runner for d in tool_defs},
        custom_system_prompt=_HARNESS_SYSTEM_PROMPT,
    )
    try:
        await agent.run_once(_question_prompt(task))
    except Exception as e:  # keep the batch alive
        print(f"  task {task.id}: run error {type(e).__name__}: {e}", file=sys.stderr)
    raw = agent._openai_messages if getattr(agent, "use_openai", True) else agent._anthropic_messages
    transcript = _norm_transcript(list(raw))
    side_query = agent._build_side_query(max_tokens=2200)
    return transcript, side_query


async def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("KEELSON_QUIET", "1")
    os.environ.setdefault("KEELSON_OPENAI_STREAM", "0")
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True) or None)
        load_dotenv(str(_REPO_ROOT / ".env"))
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="keelson-skill-evolved-probe")
    ap.add_argument("--n", type=int, default=12, help="train tasks to drive")
    ap.add_argument("--split-file", default="bench/splits.json")
    ap.add_argument("--salt", default="keelson-toolhop-v1")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default="bench/runs")
    args = ap.parse_args(argv)

    from agents.online_skill_evolution import (
        extract_online_skill_candidate,
        maintain_online_skill_candidate,
    )

    dataset = str(default_dataset_path())
    tasks = load_toolhop(dataset)
    if args.split_file and Path(args.split_file).exists():
        split = load_split(args.split_file)
    else:
        split = build_split(tasks, n_train=60, n_test=60, salt=args.salt)
    train_ids = split["train"][: args.n]
    by_id = {t.id: t for t in tasks if t.id in set(train_ids)}

    api_base, api_key, use_openai = _resolve_api()
    if not api_key:
        print("ERROR: no API key in environment")
        return 2
    model = args.model or os.environ.get("MODEL") or "gemini-3.8-flash"

    print(json.dumps({
        "probe": "skill_evolved",
        "model": model,
        "train_ids": train_ids,
        "n": len(train_ids),
    }, indent=2, ensure_ascii=False))

    candidates = []
    per_task = []
    last_side_query = None
    for tid in train_ids:
        task = by_id[tid]
        transcript, side_query = await _run_one(task, model, api_base, api_key, use_openai)
        last_side_query = side_query
        try:
            cand = await extract_online_skill_candidate(
                messages=transcript,
                side_query=side_query,
                hint="offline benchmark: multi-hop tool-calling train task",
            )
        except Exception as e:
            print(f"  task {tid}: extract error {type(e).__name__}: {e}", file=sys.stderr)
            cand = None
        per_task.append({
            "task_id": tid,
            "hops": task.hops,
            "transcript_turns": len(transcript),
            "candidate": None if cand is None else {
                "name": cand.name,
                "description": cand.description,
                "when_to_use": cand.when_to_use,
            },
        })
        if cand is not None:
            candidates.append(cand)
        print(f"  task {tid}: turns={len(transcript):2d}  candidate={'YES: ' + cand.name if cand else 'none'}")

    decisions = []
    if candidates:
        home = tempfile.mkdtemp(prefix="keelson-probe-home-")
        os.environ["KEELSON_HOME"] = home
        (Path(home) / "skills").mkdir(parents=True, exist_ok=True)
        for cand in candidates:
            try:
                res = await maintain_online_skill_candidate(
                    candidate=cand,
                    side_query=last_side_query,
                    confirm_write=_yes,
                    target="project",
                )
                decisions.append({"candidate": cand.name, "action": res.get("action"), "ok": res.get("ok")})
            except Exception as e:
                decisions.append({"candidate": cand.name, "error": f"{type(e).__name__}: {e}"})
        print("\nmaintainer decisions:", json.dumps(decisions, ensure_ascii=False))

    result = {
        "probe": "skill_evolved",
        "model": model,
        "n_train_tasks": len(train_ids),
        "n_candidates": len(candidates),
        "candidates": [c.name for c in candidates],
        "maintainer_decisions": decisions,
        "per_task": per_task,
        "conclusion": (
            "0 candidates extracted -> skill_evolved == no_skill on ToolHop"
            if not candidates
            else f"{len(candidates)} candidate(s); see maintainer_decisions"
        ),
    }
    out_dir = _REPO_ROOT / args.out / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-skill_evolved_probe")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"\n{result['conclusion']}")
    print(f"artifacts: {out_dir}")
    return 0


async def _yes(_summary: str) -> bool:
    return True


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

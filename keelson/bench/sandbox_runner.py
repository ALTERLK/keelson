"""Sandbox coding-benchmark driver.

Plugs a real Agent into keelson/bench/sandbox.py's pure grader. Two arms, kept
as close to identical as possible so the only systematic difference is "does
the harness wrap this or not" (the lesson from bench/spiral-diagnosis.md: a
prompt or tool-set mismatch between arms confounds the comparison):

    harness   real Agent, bypassPermissions, real build_system_prompt() (so
              fold guidance renders), loop guard, graceful budget exhaustion,
              read-before-edit gate — restricted to 7 core tools.
    raw       bare tool-calling loop over the *same* 7 tools and the *same*
              build_system_prompt() text, no permission layer / gate / guard.

Both arms run in-process, one task at a time (--concurrency is not exposed:
real file tools share the process cwd, so concurrent tasks would race). Each
task gets its own scratch KEELSON_HOME + cwd, mirroring
keelson/bench/runner.py's isolation. A soft per-task timeout (asyncio.wait_for)
keeps one hung task from stalling the whole batch.

--validate needs no API key: proves every task's starter code fails its own
tests and <task>/solution/ makes them pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from keelson.bench.sandbox import (
    SandboxTask,
    _snapshot,
    grade_workdir,
    load_tasks,
    materialize,
    summarize,
    validate_task,
)
from keelson.tools import execute_tool, tool_definitions

_REPO_ROOT = Path(__file__).resolve().parents[2]

ARMS = ("harness", "raw")

# read/write/edit/list/grep/shell + compact_context. Deliberately excludes
# `agent` (sub-agent construction drops api_key/anthropic_base_url — round 2,
# found not fixed — it would hit the wrong endpoint) and skill*/tool_search/
# plan-mode tools (irrelevant here, skill* would trigger extra model calls).
SANDBOX_TOOL_NAMES = (
    "read_file", "write_file", "edit_file", "list_files", "grep_search", "run_shell", "compact_context",
)


def _sandbox_tool_defs() -> list[dict]:
    by_name = {t["name"]: t for t in tool_definitions}
    return [by_name[n] for n in SANDBOX_TOOL_NAMES if n in by_name]


# --------------------------------------------------------------------- config ---

@dataclass
class SandboxRunConfig:
    arm: str
    model: str
    api_base: str | None
    use_openai: bool
    max_turns: int = 20
    timeout_s: int = 120
    git_commit: str = ""
    run_dir: str = ""
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def public(self) -> dict:
        d = asdict(self)
        d.pop("run_dir", None)
        return d


def _resolve_api() -> tuple[str | None, str | None, bool]:
    from keelson.main import _resolve_api_config

    return _resolve_api_config(None)


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


# -------------------------------------------------------------- arm: harness ---

async def _run_harness_agent(task: SandboxTask, cfg: SandboxRunConfig, api_key: str) -> dict:
    from keelson.agent import Agent

    agent = Agent(
        permission_mode="bypassPermissions",  # measures the harness's editing behaviour, not the gate
        model=cfg.model,
        max_turns=cfg.max_turns,
        api_base=cfg.api_base if cfg.use_openai else None,
        anthropic_base_url=cfg.api_base if not cfg.use_openai else None,
        api_key=api_key,
        custom_tools=_sandbox_tool_defs(),
        # no custom_system_prompt -> real build_system_prompt(), fold guidance included
    )
    out = await agent.run_once(task.instruction)
    turns = getattr(agent, "current_turns", 0)
    toks = out.get("tokens", {}) if isinstance(out, dict) else {}
    return {
        "turns": turns,
        "hit_cap": turns >= cfg.max_turns,
        "loop_guard_hits": getattr(agent, "_loop_guard_hits", 0),
        "input_tokens": toks.get("input", 0),
        "output_tokens": toks.get("output", 0),
        "final_text": (out.get("text") or "")[:500] if isinstance(out, dict) else "",
    }


# ------------------------------------------------------------------ arm: raw ---

async def _run_raw_agent(task: SandboxTask, cfg: SandboxRunConfig, api_key: str) -> dict:
    from keelson.prompt import build_system_prompt

    system_prompt = build_system_prompt()
    tool_defs = _sandbox_tool_defs()
    if cfg.use_openai:
        return await _raw_openai_loop(task, tool_defs, system_prompt, cfg, api_key)
    return await _raw_anthropic_loop(task, tool_defs, system_prompt, cfg, api_key)


async def _raw_openai_loop(task, tool_defs, system_prompt, cfg, api_key) -> dict:
    import openai

    client = openai.AsyncOpenAI(api_key=api_key, base_url=cfg.api_base or None)
    oai_tools = [
        {"type": "function", "function": {"name": d["name"], "description": d.get("description", ""), "parameters": d["input_schema"]}}
        for d in tool_defs
    ]
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task.instruction}]
    tin = tout = turns = 0
    final_text = ""
    for _ in range(cfg.max_turns):
        turns += 1
        resp = await client.chat.completions.create(model=cfg.model, tools=oai_tools, messages=messages)
        tin += resp.usage.prompt_tokens
        tout += resp.usage.completion_tokens
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            final_text = msg.content or ""
            return _raw_result(turns, cfg.max_turns, tin, tout, final_text)
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await execute_tool(tc.function.name, args, None)  # None: no read-before-edit gate
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return _raw_result(turns, cfg.max_turns, tin, tout, final_text, hit_cap=True)


async def _raw_anthropic_loop(task, tool_defs, system_prompt, cfg, api_key) -> dict:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, base_url=cfg.api_base or None)
    messages = [{"role": "user", "content": task.instruction}]
    tin = tout = turns = 0
    final_text = ""
    for _ in range(cfg.max_turns):
        turns += 1
        resp = await client.messages.create(
            model=cfg.model, max_tokens=4096, system=system_prompt, tools=tool_defs, messages=messages
        )
        tin += resp.usage.input_tokens
        tout += resp.usage.output_tokens
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            final_text = "".join(b.text for b in resp.content if b.type == "text")
            return _raw_result(turns, cfg.max_turns, tin, tout, final_text)
        results = []
        for tu in tool_uses:
            result = await execute_tool(tu.name, tu.input or {}, None)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})
        messages.append({"role": "user", "content": results})
    return _raw_result(turns, cfg.max_turns, tin, tout, final_text, hit_cap=True)


def _raw_result(turns, max_turns, tin, tout, final_text, hit_cap: bool = False) -> dict:
    return {
        "turns": turns,
        "hit_cap": hit_cap or turns >= max_turns,
        "loop_guard_hits": 0,  # raw has no guard by construction
        "input_tokens": tin,
        "output_tokens": tout,
        "final_text": final_text[:500],
    }


# ----------------------------------------------------------------- one task ---

async def run_one(task: SandboxTask, cfg: SandboxRunConfig, api_key: str, rep: int = 0) -> dict:
    home = tempfile.mkdtemp(prefix=f"keelson-sbx-{cfg.arm}-home-")
    Path(home, "skills").mkdir(parents=True, exist_ok=True)
    prev_cwd = Path.cwd()
    work = materialize(task)
    t0 = time.time()
    err = ""
    meta: dict = {}
    try:
        os.environ["KEELSON_HOME"] = home
        os.environ["KEELSON_AUTO_SKILL_EVOLUTION"] = "0"
        os.environ.setdefault("KEELSON_SPECULATIVE", "0")
        from keelson.skills import reset_skill_cache
        from keelson.tools import reset_activated_tools, reset_permission_cache

        reset_skill_cache()
        reset_permission_cache()
        reset_activated_tools()

        before = _snapshot(work)
        os.chdir(work)
        try:
            runner = _run_harness_agent if cfg.arm == "harness" else _run_raw_agent
            meta = await asyncio.wait_for(runner(task, cfg, api_key), timeout=cfg.timeout_s)
        except TimeoutError:
            err = f"timeout after {cfg.timeout_s}s"
        except Exception as e:  # keep the batch alive
            err = f"{type(e).__name__}: {e}"
        finally:
            os.chdir(prev_cwd)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    grade = grade_workdir(task, work, before)
    shutil.rmtree(work, ignore_errors=True)
    dt = time.time() - t0
    return {
        "task_id": task.id,
        "arm": cfg.arm,
        "model": cfg.model,
        "repeat": rep,
        "wall_time_s": round(dt, 2),
        "error": err,
        **meta,
        **grade,
    }


async def run_batch(tasks: list[SandboxTask], cfg: SandboxRunConfig, api_key: str, repeats: int) -> list[dict]:
    # concurrency=1 by construction: sequential, one task (and one repeat) at a time
    results = []
    for rep in range(repeats):
        for task in tasks:
            results.append(await run_one(task, cfg, api_key, rep))
    return results


# -------------------------------------------------------------------- CLI ---

def make_run_dir(cfg: SandboxRunConfig, out_root: Path) -> Path:
    safe_model = cfg.model.replace("/", "_")
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-sandbox-{cfg.arm}-{safe_model}"
    run_dir = (out_root / "runs" / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_artifacts(results: list[dict], cfg: SandboxRunConfig, out_root: Path) -> Path:
    run_dir = Path(cfg.run_dir) if cfg.run_dir else make_run_dir(cfg, out_root)
    with (run_dir / "results.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "config": cfg.public(),
        "grader": summarize(results),
        "hit_cap_rate": round(sum(1 for r in results if r.get("hit_cap")) / max(1, len(results)), 3),
        "loop_guard_hits": sum(r.get("loop_guard_hits", 0) for r in results),
        "avg_turns": round(sum(r.get("turns", 0) for r in results) / max(1, len(results)), 2),
        "errors": [r for r in results if r.get("error")][:10],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (run_dir / "config.json").write_text(
        json.dumps(cfg.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return run_dir


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("KEELSON_QUIET", "1")
    os.environ.setdefault("KEELSON_OPENAI_STREAM", "0")
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True) or None)
        load_dotenv(str(_REPO_ROOT / ".env"))
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="keelson-sandbox-bench", description="Sandbox coding-task benchmark runner")
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--model", default=None)
    ap.add_argument("--task-ids", default="", help="comma-separated task ids; default = all")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--timeout-s", type=int, default=120)
    ap.add_argument("--out", default="bench")
    ap.add_argument("--validate", action="store_true", help="no API: check every task's starter fails, solution/ passes")
    args = ap.parse_args(argv)

    all_tasks = load_tasks()

    if args.validate:
        ok = True
        for t in all_tasks:
            v = validate_task(t)
            status = "OK" if v["ok"] else "FAIL"
            print(f"  [{status}] {t.id}: {v}")
            ok = ok and v["ok"]
        print(f"\nvalidate: {'all tasks OK' if ok else 'SOME TASKS FAILED'} ({len(all_tasks)} tasks)")
        return 0 if ok else 1

    if not args.arm:
        print("ERROR: --arm is required unless --validate")
        return 2

    tasks = all_tasks
    if args.task_ids:
        wanted = {x for x in args.task_ids.replace(" ", "").split(",") if x}
        tasks = [t for t in all_tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        if missing:
            print(f"ERROR: unknown task ids: {sorted(missing)}")
            return 2

    api_base, api_key, use_openai = _resolve_api()
    if not api_key:
        print("ERROR: no API key in environment (APIKEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)")
        return 2

    cfg = SandboxRunConfig(
        arm=args.arm,
        model=args.model or os.environ.get("MODEL") or "deepseek-flash",
        api_base=api_base,
        use_openai=use_openai,
        max_turns=args.max_turns,
        timeout_s=args.timeout_s,
        git_commit=_git_commit(),
    )
    run_dir = make_run_dir(cfg, Path(args.out))
    cfg.run_dir = str(run_dir)

    print(json.dumps({**cfg.public(), "n_tasks": len(tasks), "repeats": args.repeats}, indent=2, ensure_ascii=False))
    results = asyncio.run(run_batch(tasks, cfg, api_key, args.repeats))
    write_artifacts(results, cfg, Path(args.out))
    s = summarize(results)
    print(
        f"\n{cfg.arm}/{cfg.model}: solve_rate={s.get('solve_rate', 0):.1%}  "
        f"test_file_modified_rate={s.get('test_file_modified_rate', 0):.1%}  "
        f"out_of_scope_edit_rate={s.get('out_of_scope_edit_rate', 0):.1%}"
    )
    print(f"artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

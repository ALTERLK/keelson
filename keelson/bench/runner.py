"""Offline benchmark runner.

Runs a ToolHop split through one of four arms and writes reproducible artifacts:

    raw_api        bare model function-calling loop, no harness
    no_skill       full harness, self-evolution off, no skills
    skill_v1       full harness + one hand-written multi-hop skill
    skill_evolved  full harness + a skill grown on the *train* split

Every arm executes the task's own Python tool bodies locally (no network), so
scoring against the gold answer is deterministic. Model calls need an API key in
the environment (same resolution as the CLI); ``--dry-run`` builds everything
and prints the plan without calling a model.
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

from keelson.bench import metrics
from keelson.bench.splits import build_split, load_split
from keelson.bench.toolhop import (
    ToolHopTask,
    default_dataset_path,
    load_toolhop,
    make_tool_runner,
    score,
    to_tool_defs,
)

ARMS = ("raw_api", "no_skill", "skill_v1", "skill_evolved")

_ANSWER_INSTRUCTION = (
    "Use the provided tools to answer. Each tool's output feeds the next call. "
    "When you have the answer, reply with ONLY the final answer on the last line, "
    "prefixed exactly with 'ANSWER: '."
)

# The harness arms keep the full Runtime (loop, permission gate, tool dispatch,
# context folding, per-turn skill retrieval) but NOT the default coding-agent
# system prompt — on a general-knowledge multi-hop benchmark that prompt makes a
# small model ignore the injected tools and answer from memory. This neutral
# prompt frames the task; skill retrieval still injects <retrieved_skills> into
# the user message on the skill arms.
#
# v1 (see bench/spiral-diagnosis.md) forbade finalising — "call one tool per hop
# ... output only: ANSWER:" — which made gemini-3.8-flash keep retrying with
# reworded arguments on any ambiguous tool result until the turn cap, on ~12% of
# tasks. v2 keeps "use the tools, not memory" but explicitly permits stopping.
_HARNESS_SYSTEM_PROMPT = (
    "Answer the question using the provided tools rather than your own knowledge. "
    "Break it into sub-questions and call tools to resolve them, feeding each "
    "result into the next call. "
    "When a tool result is ambiguous, or you already have enough to answer, stop "
    "calling tools and answer — do not keep retrying the same tool with reworded "
    "arguments. Finish with a line: ANSWER: <final answer>"
)


# --------------------------------------------------------------------- config ---

@dataclass
class RunConfig:
    dataset_path: str
    arm: str
    split_name: str
    split_path: str
    task_ids: list[int]
    model: str
    api_base: str | None
    use_openai: bool
    concurrency: int = 4
    max_turns: int = 20
    seed: int = 0
    git_commit: str = ""
    evolved_skill_dir: str = ""
    repeats: int = 1
    variant: str = "none"          # none | no_sysprompt  (diagnostic: strip the harness system message)
    save_transcripts: bool = False
    dump_payloads: bool = False
    run_dir: str = ""              # resolved in main(); artifacts land here
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def public(self) -> dict:
        d = asdict(self)
        d.pop("task_ids")
        d.pop("run_dir", None)
        d["n_tasks"] = len(self.task_ids)
        return d


# ------------------------------------------------------------------- helpers ---

def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


def _extract_answer(text: str) -> str | None:
    if not text:
        return None
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        low = line.lower()
        if low.startswith("answer:"):
            return line.split(":", 1)[1].strip()
    # fall back to the last non-empty line
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _question_prompt(task: ToolHopTask) -> str:
    return f"{task.question}\n\n{_ANSWER_INSTRUCTION}"


_BUDGET_FINALIZE_PROMPT = (
    "You have reached the step limit. Do not call any more tools. Give your best "
    "final answer from the information you already have, on a line starting with "
    "'ANSWER:'."
)


def _finalize_on_budget_enabled() -> bool:
    return os.environ.get("KEELSON_FINAL_ANSWER_ON_BUDGET", "1") not in ("0", "false", "False")


# ------------------------------------------------------- diagnostics (Day 1) ---

def _artifact_subdir(cfg: RunConfig, name: str) -> Path | None:
    if not cfg.run_dir:
        return None
    d = Path(cfg.run_dir) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dump_json(cfg: RunConfig, subdir: str, task_id: int, rep: int, payload) -> None:
    d = _artifact_subdir(cfg, subdir)
    if d is None:
        return
    (d / f"{task_id}-r{rep}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8", newline="\n",
    )


def _snap(obj):
    """Deep, JSON-safe snapshot — the raw loops mutate `messages` in place."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _install_payload_capture(agent) -> list:
    """Wrap the agent's OpenAI client so every model call records
    {request kwargs, full response.model_dump()}.  Runs with KEELSON_OPENAI_STREAM=0
    so the response is always a plain object.
    """
    captured: list = []
    client = getattr(agent, "_openai_client", None)
    if client is None:
        return captured
    orig = client.chat.completions.create

    async def _wrapped(*args, **kwargs):
        resp = await orig(*args, **kwargs)
        try:
            captured.append({
                "request": {k: v for k, v in kwargs.items() if k != "stream_options"},
                "response": resp.model_dump(),
            })
        except Exception as e:  # never let capture break a run
            captured.append({"request": "<unserializable>", "capture_error": str(e)})
        return resp

    client.chat.completions.create = _wrapped
    return captured


def _harness_model_calls(agent) -> int:
    msgs = getattr(agent, "_openai_messages", None) or getattr(agent, "_anthropic_messages", []) or []
    n = 0
    for m in msgs:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "assistant":
            n += 1
    return n


def _harness_transcript(agent) -> list:
    msgs = getattr(agent, "_openai_messages", None) or getattr(agent, "_anthropic_messages", []) or []
    return [m if isinstance(m, dict) else getattr(m, "model_dump", lambda: str(m))() for m in msgs]


class _CountingToolRunner:
    """Wraps a task's tool dispatcher and tallies calls / invalid names."""

    def __init__(self, task: ToolHopTask):
        self._inner = make_tool_runner(task)
        self._valid = {s["name"] for s in task.tool_specs}
        self.calls = 0
        self.invalid = 0

    def __call__(self, name: str, inp: dict) -> str:
        self.calls += 1
        if name not in self._valid:
            self.invalid += 1
        return self._inner(name, inp)


# ------------------------------------------------------------- arm: harness ---

async def _run_harness_task(task: ToolHopTask, cfg: RunConfig, api_key: str, rep: int = 0) -> dict:
    from keelson.agent import Agent

    tool_runner = _CountingToolRunner(task)
    tool_defs = to_tool_defs(task)
    agent = Agent(
        permission_mode="bypassPermissions",  # benchmark measures accuracy, not the gate (unit-tested separately)
        model=cfg.model,
        max_turns=cfg.max_turns,
        api_base=cfg.api_base if cfg.use_openai else None,
        anthropic_base_url=cfg.api_base if not cfg.use_openai else None,
        api_key=api_key,
        custom_tools=tool_defs,
        external_tools={d["name"]: tool_runner for d in tool_defs},
        custom_system_prompt=_HARNESS_SYSTEM_PROMPT,
        verify=False,  # the bench grades the answer itself; no end-of-turn checks
    )
    if cfg.variant == "no_sysprompt":
        # H2 probe: drop the harness system message entirely. Safe because
        # _refresh_runtime_system_prompt() early-returns on a custom prompt, so
        # nothing re-injects it (agent.py:517-519).
        if getattr(agent, "_openai_messages", None):
            agent._openai_messages = [m for m in agent._openai_messages if m.get("role") != "system"]

    captured = _install_payload_capture(agent) if cfg.dump_payloads else None
    t0 = time.time()
    try:
        out = await agent.run_once(_question_prompt(task))
        text = out.get("text", "")
        toks = out.get("tokens", {})
        err = ""
    except Exception as e:  # keep the batch alive
        text, toks, err = "", {}, f"{type(e).__name__}: {e}"
    dt = time.time() - t0

    turns = getattr(agent, "current_turns", 0)
    if cfg.save_transcripts:
        _dump_json(cfg, "transcripts", task.id, rep, _harness_transcript(agent))
    if captured is not None:
        _dump_json(cfg, "payloads", task.id, rep, captured)

    pred = _extract_answer(text)
    return {
        "task_id": task.id,
        "arm": cfg.arm,
        "repeat": rep,
        "hops": task.hops,
        "answer_type": task.answer_type,
        "gold": task.answer,
        "prediction": pred,
        "correct": score(task, pred),
        "turns": turns,
        "model_calls": _harness_model_calls(agent),
        "hit_cap": turns >= cfg.max_turns,
        "loop_guard_hits": getattr(agent, "_loop_guard_hits", 0),
        "tool_calls": tool_runner.calls,
        "invalid_tool_calls": tool_runner.invalid,
        "input_tokens": toks.get("input", 0),
        "output_tokens": toks.get("output", 0),
        "wall_time_s": round(dt, 2),
        "error": err,
    }


# ------------------------------------------------------------- arm: raw_api ---

async def _run_raw_api_task(task: ToolHopTask, cfg: RunConfig, api_key: str, rep: int = 0) -> dict:
    """Bare model function-calling loop — no permission layer, retrieval or folding."""
    tool_runner = _CountingToolRunner(task)
    tool_defs = to_tool_defs(task)
    captured: list = [] if cfg.dump_payloads else None
    t0 = time.time()
    try:
        loop = _raw_openai_loop if cfg.use_openai else _raw_anthropic_loop
        text, tin, tout, turns, hit_cap, messages = await loop(
            task, tool_defs, tool_runner, cfg, api_key, captured
        )
        err = ""
    except Exception as e:
        text, tin, tout, turns, hit_cap, messages = "", 0, 0, 0, False, []
        err = f"{type(e).__name__}: {e}"
    dt = time.time() - t0

    if cfg.save_transcripts:
        _dump_json(cfg, "transcripts", task.id, rep, messages)
    if captured is not None:
        _dump_json(cfg, "payloads", task.id, rep, captured)

    pred = _extract_answer(text)
    return {
        "task_id": task.id,
        "arm": "raw_api",
        "repeat": rep,
        "hops": task.hops,
        "answer_type": task.answer_type,
        "gold": task.answer,
        "prediction": pred,
        "correct": score(task, pred),
        "turns": turns,
        "model_calls": turns,      # raw loop: one model call per iteration
        "hit_cap": hit_cap,
        "tool_calls": tool_runner.calls,
        "invalid_tool_calls": tool_runner.invalid,
        "input_tokens": tin,
        "output_tokens": tout,
        "wall_time_s": round(dt, 2),
        "error": err,
    }


async def _raw_anthropic_loop(task, tool_defs, tool_runner, cfg, api_key, captured=None):
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, base_url=cfg.api_base or None)
    messages = [{"role": "user", "content": _question_prompt(task)}]
    tin = tout = turns = 0
    for _ in range(cfg.max_turns):
        turns += 1
        resp = await client.messages.create(
            model=cfg.model, max_tokens=1024, tools=tool_defs, messages=messages
        )
        if captured is not None:
            captured.append({"request": _snap({"messages": messages, "tools": tool_defs}), "response": resp.model_dump()})
        tin += resp.usage.input_tokens
        tout += resp.usage.output_tokens
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text, tin, tout, turns, False, messages
        results = []
        for tu in tool_uses:
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": tool_runner(tu.name, tu.input or {})}
            )
        messages.append({"role": "user", "content": results})
    # turn cap: one tool-less call for a best-effort answer (parity with the harness)
    text = ""
    if _finalize_on_budget_enabled():
        messages.append({"role": "user", "content": _BUDGET_FINALIZE_PROMPT})
        try:
            resp = await client.messages.create(
                model=cfg.model, max_tokens=1024, messages=messages
            )
            tin += resp.usage.input_tokens
            tout += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
        except Exception:
            pass
    return text, tin, tout, turns, True, messages


async def _raw_openai_loop(task, tool_defs, tool_runner, cfg, api_key, captured=None):
    import openai

    client = openai.AsyncOpenAI(api_key=api_key, base_url=cfg.api_base or None)
    oai_tools = [
        {"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": d["input_schema"]}}
        for d in tool_defs
    ]
    messages = [{"role": "user", "content": _question_prompt(task)}]
    tin = tout = turns = 0
    for _ in range(cfg.max_turns):
        turns += 1
        resp = await client.chat.completions.create(model=cfg.model, tools=oai_tools, messages=messages)
        if captured is not None:
            captured.append({"request": _snap({"messages": messages, "tools": oai_tools}), "response": resp.model_dump()})
        tin += resp.usage.prompt_tokens
        tout += resp.usage.completion_tokens
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or "", tin, tout, turns, False, messages
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": tool_runner(tc.function.name, args)}
            )
    # turn cap: one tool-less call for a best-effort answer (parity with the harness)
    text = ""
    if _finalize_on_budget_enabled():
        messages.append({"role": "user", "content": _BUDGET_FINALIZE_PROMPT})
        try:
            resp = await client.chat.completions.create(model=cfg.model, messages=messages)
            tin += resp.usage.prompt_tokens
            tout += resp.usage.completion_tokens
            text = resp.choices[0].message.content or ""
        except Exception:
            pass
    return text, tin, tout, turns, True, messages


# ----------------------------------------------------------------- driver ---

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_V1_SRC = _REPO_ROOT / "bench" / "skills" / "multihop-tool-chaining"


def _setup_arm_env(cfg: RunConfig) -> tuple[str, str]:
    """Isolate each arm: its own empty KEELSON_HOME and cwd so repo skills / the
    developer's ~/.keelson never leak in. Returns (home, cwd) to clean up later.

    raw_api / no_skill -> no skills, evolution off
    skill_v1           -> the hand-written multi-hop skill
    skill_evolved      -> the skill grown on the train split (cfg.evolved_skill_dir)
    """
    home = tempfile.mkdtemp(prefix=f"keelson-bench-{cfg.arm}-home-")
    work = tempfile.mkdtemp(prefix=f"keelson-bench-{cfg.arm}-cwd-")
    os.environ["KEELSON_HOME"] = home
    os.environ["KEELSON_AUTO_SKILL_EVOLUTION"] = "0"
    skills_dir = Path(home) / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    if cfg.arm == "skill_v1" and _SKILL_V1_SRC.is_dir():
        shutil.copytree(_SKILL_V1_SRC, skills_dir / _SKILL_V1_SRC.name)
    elif cfg.arm == "skill_evolved" and cfg.evolved_skill_dir:
        src = Path(cfg.evolved_skill_dir)
        if src.is_dir():
            shutil.copytree(src, skills_dir / src.name)

    os.chdir(work)
    _reset_skill_state()
    return home, work


def _reset_skill_state() -> None:
    try:
        from keelson.skills import reset_skill_cache

        reset_skill_cache()
    except Exception:
        pass
    try:
        from keelson.tools import reset_permission_cache

        reset_permission_cache()
    except Exception:
        pass


def _teardown_arm_env(home: str, work: str) -> None:
    os.chdir(_REPO_ROOT)
    for d in (home, work):
        shutil.rmtree(d, ignore_errors=True)


async def run_arm(tasks_by_id: dict[int, ToolHopTask], cfg: RunConfig, api_key: str) -> list[dict]:
    sem = asyncio.Semaphore(cfg.concurrency)
    runner = _run_raw_api_task if cfg.arm == "raw_api" else _run_harness_task

    async def _one(tid: int, rep: int) -> dict:
        async with sem:
            return await runner(tasks_by_id[tid], cfg, api_key, rep)

    jobs = [(tid, rep) for rep in range(cfg.repeats) for tid in cfg.task_ids]
    home, work = _setup_arm_env(cfg)
    try:
        return await asyncio.gather(*(_one(tid, rep) for tid, rep in jobs))
    finally:
        _teardown_arm_env(home, work)


def make_run_dir(cfg: RunConfig, out_root: Path) -> Path:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{cfg.arm}"
    if cfg.variant != "none":
        run_id += f"-{cfg.variant}"
    # absolute: each arm os.chdir()s into a scratch cwd, so a relative path would
    # write transcripts/payloads into the tempdir that teardown then deletes.
    run_dir = (out_root / "runs" / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_artifacts(results: list[dict], cfg: RunConfig, out_root: Path) -> Path:
    run_dir = Path(cfg.run_dir) if cfg.run_dir else make_run_dir(cfg, out_root)

    with (run_dir / "results.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    task_hops = {r["task_id"]: r["hops"] for r in results}
    summary = {
        "config": cfg.public(),
        "arm": asdict(metrics.summarize_arm(cfg.arm, results)),
        "pooled": metrics.pooled_arm_accuracy(results),
        "hit_cap_rate": round(sum(1 for r in results if r.get("hit_cap")) / max(1, len(results)), 3),
        "avg_model_calls": round(sum(r.get("model_calls", 0) for r in results) / max(1, len(results)), 2),
        "by_hops": metrics.stratify_by_hops(results, task_hops),
        "errors": [r for r in results if r.get("error")][:10],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (run_dir / "config.json").write_text(
        json.dumps(cfg.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return run_dir


# -------------------------------------------------------------------- CLI ---

def _resolve_api() -> tuple[str | None, str | None, bool]:
    from keelson.main import _resolve_api_config

    return _resolve_api_config(None)


def build_config(args: argparse.Namespace) -> RunConfig:
    dataset_path = args.dataset or str(default_dataset_path())
    tasks = load_toolhop(dataset_path)

    if args.split_file and Path(args.split_file).exists():
        split = load_split(args.split_file)
    else:
        split = build_split(tasks, n_train=args.n, n_test=args.n, salt=args.salt)
        if args.split_file:
            from keelson.bench.splits import save_split

            save_split(split, args.split_file)

    if args.task_ids:
        ids = [int(x) for x in str(args.task_ids).replace(" ", "").split(",") if x]
        split_name = f"task-ids({len(ids)})"
    else:
        ids = split[args.split][: args.n] if args.n else split[args.split]
        split_name = args.split
    api_base, _key, use_openai = _resolve_api()
    return RunConfig(
        dataset_path=dataset_path,
        arm=args.arm,
        split_name=split_name,
        split_path=args.split_file or "(in-memory)",
        task_ids=list(ids),
        model=args.model or os.environ.get("MODEL") or "deepseek-flash",
        api_base=api_base,
        use_openai=use_openai,
        concurrency=args.concurrency,
        max_turns=args.max_turns,
        seed=args.seed,
        git_commit=_git_commit(),
        repeats=max(1, args.repeats),
        variant=args.variant,
        save_transcripts=args.save_transcripts,
        dump_payloads=args.dump_payloads,
    )


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("KEELSON_QUIET", "1")
    os.environ.setdefault("KEELSON_OPENAI_STREAM", "0")  # gemini proxy drops streamed tool calls
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
    ap = argparse.ArgumentParser(prog="keelson-bench", description="Offline ToolHop benchmark runner")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--split", choices=("train", "test"), default="test")
    ap.add_argument("--split-file", default="bench/splits.json")
    ap.add_argument("--n", type=int, default=60, help="tasks per split (0 = all in split file)")
    ap.add_argument("--salt", default="keelson-toolhop-v1")
    ap.add_argument("--model", default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="bench")
    ap.add_argument("--dry-run", action="store_true", help="build split + tools, print plan, no model calls")
    # --- diagnostics (Day 1) ---
    ap.add_argument("--task-ids", default="", help="comma-separated task ids; overrides --split/--n")
    ap.add_argument("--repeats", type=int, default=1, help="run each task N times (sampling noise band)")
    ap.add_argument("--variant", choices=("none", "no_sysprompt"), default="none",
                    help="no_sysprompt: strip the harness system message (H2 probe)")
    ap.add_argument("--save-transcripts", action="store_true", help="dump per-task message history")
    ap.add_argument("--dump-payloads", action="store_true",
                    help="dump every model request + full response.model_dump()")
    args = ap.parse_args(argv)

    cfg = build_config(args)
    tasks_by_id = {t.id: t for t in load_toolhop(cfg.dataset_path) if t.id in set(cfg.task_ids)}
    missing = sorted(set(cfg.task_ids) - set(tasks_by_id))
    if missing:
        print(f"ERROR: task ids not in dataset: {missing}")
        return 2

    print(json.dumps(cfg.public(), indent=2, ensure_ascii=False))
    if args.dry_run:
        # exercise tool construction for every task so a broken task shows up now
        broken = []
        for tid in cfg.task_ids:
            try:
                make_tool_runner(tasks_by_id[tid])
            except Exception as e:
                broken.append((tid, f"{type(e).__name__}: {e}"))
        print(f"dry-run: {len(cfg.task_ids)} tasks, {len(broken)} fail tool construction")
        for tid, err in broken[:10]:
            print(f"  task {tid}: {err}")
        return 0

    _, api_key, _ = _resolve_api()
    if not api_key:
        print("ERROR: no API key in environment (APIKEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)")
        return 2

    # Resolve the run dir up front so transcripts / payloads land inside it.
    run_dir = make_run_dir(cfg, Path(args.out))
    cfg.run_dir = str(run_dir)

    results = asyncio.run(run_arm(tasks_by_id, cfg, api_key))
    write_artifacts(results, cfg, Path(args.out))
    s = metrics.summarize_arm(cfg.arm, results)
    n_cap = sum(1 for r in results if r.get("hit_cap"))
    avg_mc = sum(r.get("model_calls", 0) for r in results) / max(1, len(results))
    print(
        f"\n{cfg.arm}: acc={s.accuracy:.1%} ({s.correct}/{s.n})  "
        f"model_calls={avg_mc:.1f}  hit_cap={n_cap}/{len(results)}  "
        f"invalid_tool_rate={s.invalid_tool_call_rate:.1%}  cost≈${s.est_cost_usd:.2f}"
    )
    print(f"artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

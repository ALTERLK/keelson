"""CLI entry point: argument parsing, the interactive REPL, and the ``web`` subcommand."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from . import models, paths
from .agent import Agent
from .i18n import t
from .memory import list_memories
from .online_skill_eval import format_online_skill_eval_async
from .session import get_latest_session_id
from .skill_history import diff_current_vs_snapshot, format_history
from .skill_history import rollback as rollback_skill
from .skills import (
    create_skill,
    discover_skills,
    evolve_skill,
    execute_skill,
    get_skill_by_name,
    record_feedback,
    reset_skill_cache,
    skill_stats,
)
from .ui import (
    print_diff,
    print_error,
    print_goodbye,
    print_info,
    print_interrupted,
    print_memory_entries,
    print_plan_approval_options,
    print_plan_for_approval,
    print_review,
    print_rows,
    print_skill_entries,
    print_user_prompt,
    print_warning,
    print_welcome,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="keelson",
        description="Keelson — a self-evolving agent harness",
        add_help=False,
    )
    parser.add_argument("prompt", nargs="*", help="One-shot prompt")
    parser.add_argument("--yolo", "-y", action="store_true", help="Skip all confirmation prompts")
    parser.add_argument("--plan", action="store_true", help="Plan mode: read-only")
    parser.add_argument("--accept-edits", action="store_true", help="Auto-approve file edits")
    parser.add_argument("--dont-ask", action="store_true", help="Auto-deny confirmations (for CI)")
    parser.add_argument("--thinking", action="store_true", help="Enable extended thinking")
    parser.add_argument("--model", "-m", default=None, help="Model to use")
    parser.add_argument("--api-base", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--print", "-p", dest="print_mode", action="store_true",
                        help="Answer one request without asking anything and print only the result")
    parser.add_argument("--output-format", choices=("text", "json"), default="text",
                        help="With -p: plain text or one JSON object")
    parser.add_argument("--max-cost", type=float, default=None, help="Max USD spend")
    parser.add_argument("--max-turns", type=int, default=None, help="Max agentic turns")
    parser.add_argument("--help", "-h", action="store_true", help="Show help")
    return parser.parse_args()


def _resolve_permission_mode(args: argparse.Namespace) -> str:
    if args.yolo:
        return "bypassPermissions"
    if args.plan:
        return "plan"
    if args.accept_edits:
        return "acceptEdits"
    if args.dont_ask:
        return "dontAsk"
    return "default"


def _clean_env(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


_KEY_VARS = ("APIKEY", "KEELSON_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _load_env_file() -> list[Path]:
    """Load model settings. Earlier sources win; nothing overrides real env vars.

    1. environment variables
    2. the active profile, ``~/.keelson/profiles/<name>.env`` (see profiles.py)
    3. ``<project>/.keelson/config.env``
    4. ``~/.keelson/config.env`` (written by ``keelson init``)
    5. ``./.env`` in the current folder only — the pre-0.4 location. It is not
       searched for in parent folders any more: walking up could pick up some
       other application's ``OPENAI_API_KEY``.
    """
    from . import profiles

    loaded: list[Path] = []
    name = profiles.active_profile()
    if name:
        chosen = profiles.profile_file(name)
        if chosen.is_file():
            load_dotenv(chosen, override=False)
            loaded.append(chosen)
        else:
            print(f"warning: profile {name!r} does not exist ({chosen}); `keelson use` lists the ones that do.",
                  file=sys.stderr)
    for candidate in (paths.project_config_file(), paths.user_config_file()):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            loaded.append(candidate)
    legacy = Path.cwd() / ".env"
    if legacy.is_file():
        had_key = any(_clean_env(os.environ.get(name)) for name in _KEY_VARS)
        load_dotenv(legacy, override=False)
        loaded.append(legacy)
        if not had_key and any(_clean_env(os.environ.get(name)) for name in _KEY_VARS):
            print("note: using the API key from ./.env; `keelson init` can save it to "
                  f"{paths.user_config_file()} so every project can use it.", file=sys.stderr)
    return loaded


def _is_anthropic_compatible_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host == "anthropic.com" or host.endswith(".anthropic.com"):
        return True
    path = (parsed.path or "").lower().rstrip("/")
    return path.endswith("/anthropic") or "/anthropic/" in path


def _resolve_api_config(cli_api_base: str | None) -> tuple[str | None, str | None, bool]:
    generic_api_key = _clean_env(os.environ.get("APIKEY")) or _clean_env(os.environ.get("KEELSON_API_KEY"))
    openai_api_key = _clean_env(os.environ.get("OPENAI_API_KEY"))
    anthropic_api_key = _clean_env(os.environ.get("ANTHROPIC_API_KEY"))

    generic_api_base = _clean_env(os.environ.get("API")) or _clean_env(os.environ.get("KEELSON_API_BASE"))
    openai_api_base = _clean_env(os.environ.get("OPENAI_BASE_URL"))
    anthropic_api_base = _clean_env(os.environ.get("ANTHROPIC_BASE_URL"))

    resolved_api_base = _clean_env(cli_api_base) or generic_api_base or openai_api_base or anthropic_api_base

    # A base URL that only came from ANTHROPIC_BASE_URL speaks the Anthropic protocol.
    only_anthropic_base = bool(anthropic_api_base) and resolved_api_base == anthropic_api_base and not (
        _clean_env(cli_api_base) or generic_api_base or openai_api_base)
    if resolved_api_base:
        if only_anthropic_base or _is_anthropic_compatible_base_url(resolved_api_base):
            return resolved_api_base, generic_api_key or anthropic_api_key or openai_api_key, False
        return resolved_api_base, generic_api_key or openai_api_key or anthropic_api_key, True

    if anthropic_api_key or anthropic_api_base:
        return anthropic_api_base, generic_api_key or anthropic_api_key or openai_api_key, False

    if openai_api_key or openai_api_base:
        return openai_api_base, generic_api_key or openai_api_key or anthropic_api_key, True

    if generic_api_key:
        return None, generic_api_key, False

    return None, None, False


_CONFIRM_KEYS = {"once": "y", "session": "s", "project": "p", "deny": "n"}


def confirm_prompt(options: list[str]) -> str:
    return t("confirm_ask", keys="/".join(_CONFIRM_KEYS[o] for o in options if o in _CONFIRM_KEYS))


def read_confirm_answer(options: list[str], read=input) -> dict:
    """Read one answer to a permission prompt: y / s / p / n, or `n: reason`."""
    try:
        answer = read(confirm_prompt(options))
    except EOFError:
        return {"choice": "deny"}
    return parse_confirm_answer(answer, options)


def parse_confirm_answer(answer: str, options: list[str]) -> dict:
    head, _, reason = answer.strip().partition(":")
    key = head.strip().lower()[:1]
    choice = {"y": "once", "s": "session", "p": "project"}.get(key, "deny")
    if choice not in options:
        choice = "deny"
    return {"choice": choice, "reason": reason.strip() if choice == "deny" else ""}


def handle_checkpoint_command(agent: Agent, inp: str) -> bool:
    """/undo [N] [--force], /rewind N, /redo, /revert FILE [N], /checkpoints. True if it was one."""
    words = inp.split()
    if not words or words[0] not in ("/undo", "/rewind", "/redo", "/revert", "/checkpoints"):
        return False
    force = "--force" in words
    rest = [w for w in words[1:] if w != "--force"]
    numbers = [int(w) for w in rest if w.isdigit()]
    if words[0] == "/checkpoints":
        store = agent.checkpoint_store()
        rows = store.describe() if store else []
        if store is not None and store.reason:
            print_warning(store.reason)
        if not rows:
            print_info("No turns to undo yet.")
        for row in rows[:20]:
            files = ", ".join(row["files"][:6]) + (" ..." if len(row["files"]) > 6 else "")
            print_info(f"  /undo {row['steps']:<3} {row['time']}  {row['label']}\n      {files or '(no files)'}")
        return True
    if words[0] == "/redo":
        report = agent.redo()
    elif words[0] == "/revert":
        files = [w for w in rest if not w.isdigit()]
        if not files:
            print_warning("Usage: /revert <file> [N]  — put one file back as it was N turns ago (default 1).")
            return True
        report = agent.revert(" ".join(files), numbers[0] if numbers else 1)
    else:
        if words[0] == "/rewind" and not numbers:
            print_warning("Usage: /rewind N  — undo the last N turns (see /checkpoints).")
            return True
        report = agent.undo(numbers[0] if numbers else 1, force=force)
    (print_info if report.ok else print_warning)(report.summary())
    return True


async def handle_verify_command(agent: Agent, inp: str) -> bool:
    """/verify, /verify init, /verify run. True if it was one."""
    words = inp.split()
    if not words or words[0] != "/verify":
        return False
    from . import verify as verify_mod

    sub = words[1] if len(words) > 1 else ""
    commands, source = verify_mod.commands_for()
    if sub == "init":
        detected = verify_mod.detect()
        if not detected:
            print_warning("Found no test command to put in KEELSON.md. Add a `## Verify` section by hand, "
                          "one command per line in backticks.")
            return True
        path = verify_mod.write_verify_section(detected)
        print_info(f"Wrote {len(detected)} check(s) to {path}:\n  " + "\n  ".join(detected)
                   + "\nEdit that section to add lint or type checks.")
        return True
    if sub == "run":
        if not commands:
            print_warning("No check commands. `/verify init` writes the detected ones to KEELSON.md.")
            return True
        runs = await agent.run_checks(commands)
        for run in runs:
            ok = run.exit_code == 0
            (print_info if ok else print_warning)(f"{'✓' if ok else '✗'} {run.command}: {run.summary}")
            if not ok and run.output:
                print_info(run.output)
        return True
    if sub:
        print_warning("Usage: /verify [init | run]")
        return True
    if commands:
        where = "KEELSON.md" if source == "KEELSON.md" else "detected (not saved; `/verify init` saves them)"
        print_info(f"Checks run after every turn that changes code — {where}:\n  " + "\n  ".join(commands))
    else:
        print_info("No check commands for this project, so turns that change code end as 'Not verified'.\n"
                   "  `/verify init` detects them, or add a `## Verify` section to KEELSON.md.")
    if not verify_mod.enabled():
        print_warning("End-of-turn checks are off (KEELSON_VERIFY=0).")
    return True


def handle_task_command(agent: Agent, inp: str) -> bool:
    """/task, /task goal TEXT, /task add RULE, /task drop N, /task clear. True if it was one."""
    words = inp.split(maxsplit=2)
    if not words or words[0] != "/task":
        return False
    ledger = agent.ledger
    sub = words[1] if len(words) > 1 else ""
    arg = words[2].strip() if len(words) > 2 else ""
    if sub == "goal" and arg:
        ledger.goal = arg
    elif sub == "add" and arg:
        ledger.add_constraint(arg)
    elif sub == "drop" and arg.isdigit():
        if not ledger.drop_constraint(int(arg) - 1):
            print_warning(f"No constraint {arg}.")
            return True
    elif sub == "clear":
        agent.reset_ledger()
        print_info("Task ledger cleared.")
        return True
    elif sub:
        print_warning("Usage: /task [goal TEXT | add RULE | drop N | clear]")
        return True
    if ledger.is_empty():
        print_info("The task ledger is empty. It fills in as you work: your goal, your rules word for word, "
                   "the plan, the files changed.")
        return True
    lines = []
    if ledger.goal:
        lines.append("Goal: " + ledger.goal.splitlines()[0][:200])
    if ledger.constraints:
        lines.append("Constraints (kept word for word; /task drop N removes one):")
        lines += [f"  {i}. {c['text']}" + (f"  (said {c['count']}×)" if c["count"] > 1 else "")
                  for i, c in enumerate(ledger.constraints, 1)]
    if ledger.plan:
        marks = {"done": "✓", "in_progress": "▸", "blocked": "!", "pending": "○"}
        lines.append("Plan:")
        lines += [f"  {marks.get(s['status'], '○')} {s['step']}" for s in ledger.plan]
    if ledger.decisions:
        lines.append("Decisions:")
        lines += [f"  - {d}" for d in ledger.decisions[-10:]]
    if ledger.files:
        lines.append(f"Files changed ({len(ledger.files)}): " + ", ".join(ledger.files[-15:]))
    if ledger.last_verify:
        v = ledger.last_verify
        lines.append(f"Last verification ({v['at']}): {v['status']} — {v['detail']}")
    if ledger.scope:
        lines.append("Scope of the current request: " + ", ".join(ledger.scope))
    print_info("\n".join(lines))
    return True


def handle_usage_command(agent: Agent, inp: str) -> bool:
    if inp.strip() != "/usage":
        return False
    from . import usage

    session = usage.totals(days=1, session=agent.session_id)
    lines = [
        "This session:      " + usage.fmt(session),
        "This project today: " + usage.fmt(usage.totals(days=1, project=str(Path.cwd()))),
        "All projects today: " + usage.fmt(usage.totals(days=1)),
        "Last 7 days:        " + usage.fmt(usage.totals(days=7)),
    ]
    models_used = usage.by_model(7)
    if models_used:
        lines.append("By model (7 days):")
        lines += [f"  {name}: {usage.fmt(t)}" for name, t in sorted(models_used.items(), key=lambda kv: -kv[1].cost_usd)]
    budget = usage.daily_budget()
    lines.append(f"Daily budget: ${budget:g} (KEELSON_DAILY_BUDGET_USD)" if budget
                 else "No daily budget (set KEELSON_DAILY_BUDGET_USD).")
    lines.append("Costs are estimates from list prices; cache hits are what the provider reported.")
    print_info("\n".join(lines))
    return True


def offer_rules_for_keelson_md(agent: Agent, read=input) -> None:
    """At the end of a session: rules the user repeated may belong in KEELSON.md. Written only on a yes."""
    from . import ledger as ledger_mod

    md = Path.cwd() / "KEELSON.md"
    existing = md.read_text(encoding="utf-8") if md.exists() else ""
    repeated = [c for c in agent.ledger.repeated_constraints() if c not in existing]
    if not repeated:
        return
    print_info("You said these more than once this session:\n" + "\n".join(f"  - {c}" for c in repeated)
               + "\nSave them to KEELSON.md so every session follows them?")
    try:
        answer = read("  Save? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if answer.startswith("y"):
        path = ledger_mod.append_to_keelson_md(repeated)
        print_info(f"Added to {path} under ## Rules.")


def _confirm_trust() -> None:
    """Before the first turn: a project that would switch on allow rules or MCP servers must be trusted."""
    from . import trust

    pending = trust.needs_decision()
    if not pending:
        return
    print_warning("This folder has settings that would run with your permissions:\n  "
                  + "\n  ".join(pending)
                  + "\nThey stay off until you trust the folder. Its deny rules apply either way.")
    try:
        answer = input(t("trust_q")).strip().lower()
    except EOFError:
        answer = ""
    if answer.startswith("y"):
        trust.trust()
        from .tools import reset_permission_cache

        reset_permission_cache()
        print_info("Trusted. (`keelson trust --revoke` takes it back.)")


def handle_help_command(inp: str) -> bool:
    if inp.strip() not in ("/help", "/?", "help"):
        return False
    from . import commands

    print_info(commands.help_text())
    return True


def handle_status_command(agent: Agent, inp: str) -> bool:
    if inp.strip() != "/status":
        return False
    from . import profiles
    from . import verify as verify_mod

    profile = profiles.active_profile()
    checks, source = verify_mod.commands_for()
    window = agent.effective_window or 1
    used = agent.last_input_token_count
    rows = [
        (t("model"), agent.model + (f"  ({profile})" if profile else "")),
        (t("mode"), t(f"mode.{agent.permission_mode}")),
        (t("workspace"), os.getcwd()),
        (t("status.session"), agent.session_id),
        (t("status.context"), f"{used:,} / {window:,} ({used / window:.0%})"),
        (t("status.cost"), f"${agent._get_current_cost_usd():.4f} · "
         + t("turn.tokens", tin=f"{agent.total_input_tokens:,}", tout=f"{agent.total_output_tokens:,}")
         + (" · " + t("turn.cache", rate=min(agent.total_cache_hit_tokens, agent.total_input_tokens)
                      / agent.total_input_tokens) if agent.total_input_tokens else "")),
        (t("checks"), (" · ".join(checks) + f"  ({source})") if checks else t("checks_none")),
    ]
    if agent.context_files:
        rows.append((t("status.files"), ", ".join(agent.context_files)))
    store = agent.checkpoint_store()
    if store is not None:
        n = len(store.describe())
        rows.append((t("status.undo"), t("status.undo_full", n=n) if store.covers_shell
                     else t("status.undo_partial", n=n, reason=store.reason or "")))
    print_rows(rows)
    return True


def handle_mode_command(agent: Agent, inp: str) -> bool:
    words = inp.split()
    if not words or words[0] != "/mode":
        return False
    choices = ("default", "acceptEdits", "plan", "dontAsk", "bypassPermissions")
    if len(words) > 1:
        wanted = {c.lower(): c for c in choices}.get(words[1].lower())
        if wanted is None:
            print_warning(t("mode.bad", mode=words[1], choices=" / ".join(choices)))
            return True
        agent.set_permission_mode(wanted)
    print_info(t("mode_now", mode=t(f"mode.{agent.permission_mode}")))
    return True


def handle_diff_command(agent: Agent, inp: str) -> bool:
    words = inp.split()
    if not words or words[0] != "/diff":
        return False
    steps = int(words[1]) if len(words) > 1 and words[1].isdigit() else 1
    store = agent.checkpoint_store()
    if store is None:
        print_warning(t("diff.off"))
        return True
    text, files = store.diff(steps)
    if not text.strip():
        print_info(t("diff.none", n=steps))
        return True
    print_diff(text)
    print_info(f"{t('review.files')} ({len(files)}): " + ", ".join(files[:20]))
    return True


def handle_review_command(agent: Agent, inp: str) -> bool:
    if inp.strip() != "/review":
        return False
    outcome = agent.last_outcome
    store = agent.checkpoint_store()
    files: list[str] = []
    if store is not None:
        rows = store.describe()
        files = rows[0]["files"] if rows else []
    if outcome is None and not files:
        print_info(t("review.none"))
        return True
    data = outcome.event() if outcome is not None else {"status": "unverified", "detail": ""}
    print_review(data, files or data.get("changed") or [])
    return True


def handle_context_command(agent: Agent, inp: str) -> bool:
    """/add FILE..., /drop [FILE...]."""
    words = inp.split()
    if not words or words[0] not in ("/add", "/drop"):
        return False
    names = [w.lstrip("@") for w in words[1:]]
    if words[0] == "/add":
        if not names:
            print_info(t("add.now", files=", ".join(agent.context_files)) if agent.context_files
                       else t("add.usage"))
            return True
        added, missing = agent.add_context_files(names)
        if added:
            print_info(t("add.added", files=", ".join(added)))
        if missing:
            print_warning(t("add.missing", files=", ".join(missing)))
        return True
    dropped = agent.drop_context_files(names or None)
    print_info(t("drop.done", files=", ".join(dropped)) if dropped else t("drop.none"))
    return True


def handle_init_command(inp: str) -> bool:
    """/init: KEELSON.md with the detected check commands and a Rules section to fill in."""
    if inp.strip() != "/init":
        return False
    from . import verify as verify_mod

    md = verify_mod.keelson_md()
    if md.exists():
        print_info(t("init.exists", path=md))
        return True
    md.write_text("# KEELSON.md\n\nNotes for Keelson about this project. It reads this file every session.\n\n"
                  "## Rules\n\n- (things that must not change, style, commands to avoid)\n\n",
                  encoding="utf-8", newline="\n")
    verify_mod.write_verify_section(verify_mod.detect())
    print_info(t("init.done", path=md))
    return True


def recent_sessions(limit: int = 10) -> list[dict]:
    from .session import list_sessions

    rows = [s for s in list_sessions() if s.get("id") and s.get("messageCount", 0)]
    rows.sort(key=lambda s: s.get("updated") or s.get("startTime", ""), reverse=True)
    return rows[:limit]


def handle_resume_command(agent: Agent, inp: str) -> bool:
    words = inp.split()
    if not words or words[0] != "/resume":
        return False
    rows = [r for r in recent_sessions() if r.get("id") != agent.session_id]
    if not rows:
        print_info(t("resume.none"))
        return True
    if len(words) == 1:
        lines = [f"  {i}. {r.get('updated') or str(r.get('startTime', ''))[:16]}  "
                 f"{(r.get('title') or '').splitlines()[0][:60] if r.get('title') else '—'}  "
                 f"({r.get('messageCount', 0)})" for i, r in enumerate(rows, 1)]
        print_info("\n".join(lines) + "\n" + t("resume.pick"))
        return True
    pick = words[1]
    row = rows[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(rows) else next(
        (r for r in rows if r.get("id") == pick), None)
    if row is None or not agent.resume(row["id"]):
        print_warning(t("resume.bad", n=pick))
        return True
    print_info(t("resume.done", id=row["id"], n=row.get("messageCount", 0)))
    return True


def handle_terminal_command(agent: Agent, inp: str) -> bool:
    """The commands added for the terminal in 0.4: /help /status /mode /diff /review /add /drop /init /resume."""
    return (handle_help_command(inp) or handle_status_command(agent, inp) or handle_mode_command(agent, inp)
            or handle_diff_command(agent, inp) or handle_review_command(agent, inp)
            or handle_context_command(agent, inp) or handle_init_command(inp) or handle_resume_command(agent, inp))


async def run_repl(agent: Agent) -> None:
    """Interactive REPL loop."""
    from .terminal_input import TerminalInput
    from .verify import commands_for

    term = TerminalInput(agent)

    async def ask(prompt: str) -> str:
        return await term.ask(prompt)

    async def confirm_fn(message: str, request: dict | None = None) -> dict:
        options = (request or {}).get("options") or ["once", "deny"]
        try:
            answer = await ask(confirm_prompt(options))
        except EOFError:
            return {"choice": "deny"}
        return parse_confirm_answer(answer, options)

    agent.set_confirm_fn(confirm_fn)

    async def plan_approval_fn(plan_content: str) -> dict:
        print_plan_for_approval(plan_content)
        print_plan_approval_options()
        while True:
            try:
                choice = (await ask("  Enter choice (1-4): ")).strip()
            except EOFError:
                return {"choice": "manual-execute"}
            if choice == "1":
                return {"choice": "clear-and-execute"}
            elif choice == "2":
                return {"choice": "execute"}
            elif choice == "3":
                return {"choice": "manual-execute"}
            elif choice == "4":
                try:
                    feedback = (await ask("  Feedback (what to change): ")).strip()
                except EOFError:
                    feedback = ""
                return {"choice": "keep-planning", "feedback": feedback or None}
            else:
                print_warning("Invalid choice. Enter 1, 2, 3, or 4.")

    agent.set_plan_approval_fn(plan_approval_fn)

    sigint_count = 0
    loop = asyncio.get_running_loop()

    def handle_sigint(sig, frame):
        # Reached while a turn runs (prompt_toolkit reads Ctrl+C itself at the prompt).
        nonlocal sigint_count
        if agent.is_processing:
            # Stop the running turn: the model call and any shell command it started.
            # Scheduled through the loop so a loop waiting on the network wakes up.
            loop.call_soon_threadsafe(agent.abort)
            print_interrupted()
            sigint_count = 0
        else:
            sigint_count += 1
            if sigint_count >= 2:
                print_goodbye()
                sys.exit(0)
            print_warning(t("ctrl_c_again"))
            if not term.rich:
                print_user_prompt()

    signal.signal(signal.SIGINT, handle_sigint)
    _confirm_trust()
    from . import profiles

    print_welcome(
        model=agent.model,
        permission_mode=agent.permission_mode,
        cwd=os.getcwd(),
        skill_count=len(discover_skills()),
        checks=commands_for()[0],
        profile=profiles.active_profile(),
    )

    while True:
        try:
            line = await term.read()
        except KeyboardInterrupt:
            # Ctrl+C at the prompt: the first one warns, the second one exits.
            sigint_count += 1
            if sigint_count >= 2:
                print_goodbye()
                break
            print_warning(t("ctrl_c_again"))
            continue
        if line is None:
            print_goodbye()
            break

        inp = line.strip()
        sigint_count = 0

        if not inp:
            continue
        if inp in ("exit", "quit", "/exit", "/quit"):
            offer_rules_for_keelson_md(agent)
            print_goodbye()
            break

        # REPL commands
        if inp == "/clear":
            agent.clear_history()
            continue
        if inp == "/plan":
            agent.toggle_plan_mode()
            continue
        if inp == "/cost":
            agent.show_cost()
            continue
        if handle_terminal_command(agent, inp):
            continue
        if handle_checkpoint_command(agent, inp):
            continue
        if await handle_verify_command(agent, inp):
            continue
        if handle_task_command(agent, inp) or handle_usage_command(agent, inp):
            continue
        if inp == "/model" or inp.startswith("/model "):
            wanted = inp[len("/model"):].strip()
            if wanted:
                agent.set_model(wanted)
                note = models.retirement_note(wanted)
                if note:
                    print_warning(note)
                print_info(f"Model: {agent.model} (same endpoint and key). To switch provider, exit and run "
                           "`keelson use <profile>`.")
            else:
                from . import profiles

                active = profiles.active_profile()
                print_info(f"Model: {agent.model}" + (f" · profile: {active}" if active else "")
                           + "\n  /model <name> switches the model on this endpoint; `keelson use` lists profiles.")
            continue
        if inp == "/compact":
            try:
                await agent.compact()
            except Exception as e:
                print_error(str(e))
            continue
        if inp == "/memory":
            memories = list_memories()
            if not memories:
                print_info("No memories saved yet.")
            else:
                print_memory_entries(memories)
            continue
        if inp == "/skills":
            skills = discover_skills()
            if not skills:
                print_info("No skills found. Add skills to .keelson/skills/<name>/SKILL.md")
            else:
                print_skill_entries(skills)
            continue
        if inp == "/skill-stats":
            print_info(skill_stats())
            continue
        if inp == "/skill-eval":
            side_query = agent._build_side_query(max_tokens=2400)
            print_info(await format_online_skill_eval_async(side_query=side_query))
            continue
        if inp.startswith("/extract_now"):
            hint = inp[len("/extract_now") :].strip()
            result = await agent.extract_now(hint)
            if result.get("ok"):
                print_info("Ran online skill extraction for the current pending window.")
            else:
                print_error(str(result.get("error") or result))
            continue
        if inp.startswith("/skill-feedback "):
            _, rest = inp.split(" ", 1)
            parts = rest.strip().split(" ", 2)
            if len(parts) < 2:
                print_error("Usage: /skill-feedback <skill-name> <rating> [note]")
                continue
            note = parts[2] if len(parts) > 2 else ""
            record_feedback(parts[0], parts[1], note)
            print_info(f"Recorded feedback for skill: {parts[0]}")
            continue
        if inp.startswith("/skill-evolve "):
            _, rest = inp.split(" ", 1)
            parts = rest.strip().split(" ", 1)
            if len(parts) < 2:
                print_error("Usage: /skill-evolve <skill-name> <durable lesson>")
                continue
            result = evolve_skill(parts[0], parts[1], rationale="Manual REPL evolution", target="active")
            if result.get("ok"):
                print_info(f"Evolved skill {result.get('skill')} to version {result.get('version')}")
            else:
                print_error(str(result.get("error") or result))
            continue
        if inp.startswith("/skill-history "):
            name = inp[len("/skill-history "):].strip()
            if not name:
                print_error("Usage: /skill-history <skill-name>")
                continue
            print_info(format_history(name))
            continue
        if inp.startswith("/skill-diff "):
            parts = inp[len("/skill-diff "):].strip().split()
            if not parts:
                print_error("Usage: /skill-diff <skill-name> [version]")
                continue
            res = diff_current_vs_snapshot(parts[0], parts[1] if len(parts) > 1 else None)
            if res.get("ok"):
                print_info(f"{parts[0]}: {res['from_version']} -> {res['to_version']}\n{res['diff']}")
            else:
                print_error(str(res.get("error") or res))
            continue
        if inp.startswith("/skill-rollback "):
            parts = inp[len("/skill-rollback "):].strip().split()
            if not parts:
                print_error("Usage: /skill-rollback <skill-name> [version]")
                continue
            res = rollback_skill(parts[0], parts[1] if len(parts) > 1 else None, actor="user")
            if res.get("ok"):
                reset_skill_cache()
                agent._refresh_runtime_system_prompt()
                print_info(
                    f"Rolled back {res['skill']} {res['from_version']} -> {res['to_version']} "
                    f"(restored body from v{res['restored_from']})"
                )
            else:
                print_error(str(res.get("error") or res))
            continue
        if inp.startswith("/skill-create "):
            _, rest = inp.split(" ", 1)
            parts = [part.strip() for part in rest.split("|", 3)]
            if len(parts) < 4 or not all(parts[:4]):
                print_error("Usage: /skill-create <name> | <description> | <when-to-use> | <instructions>")
                continue
            result = create_skill(
                name=parts[0],
                description=parts[1],
                when_to_use=parts[2],
                instructions=parts[3],
                target="project",
                context="inline",
                user_invocable=False,
                evidence="Manual REPL skill creation",
            )
            if result.get("ok"):
                print_info(f"Created skill {result.get('skill')} at {result.get('file')}")
            else:
                print_error(str(result.get("error") or result))
            continue

        # Skill invocation: /<skill-name> [args]
        if inp.startswith("/"):
            space_idx = inp.find(" ")
            cmd_name = inp[1:space_idx] if space_idx > 0 else inp[1:]
            cmd_args = inp[space_idx + 1:] if space_idx > 0 else ""
            skill = get_skill_by_name(cmd_name)
            if skill and skill.user_invocable:
                print_info(f"Invoking skill: {skill.name}")
                try:
                    if skill.context == "fork":
                        await agent.chat(f'Use the skill tool to invoke "{skill.name}" with args: {cmd_args or "(none)"}')
                    else:
                        result = execute_skill(skill.name, cmd_args)
                        if not result:
                            print_error(f"Unknown skill: {skill.name}")
                            continue
                        await agent.chat(result["prompt"])
                except Exception as e:
                    if "abort" not in str(e).lower():
                        print_error(str(e))
                continue
            if "/" not in cmd_name and "\\" not in cmd_name:
                print_warning(t("unknown_command", cmd="/" + cmd_name))
                continue

        # Normal chat
        try:
            await agent.chat(inp)
        except Exception as e:
            if "abort" not in str(e).lower():
                print_error(str(e))
    await agent.drain_background_skill_tasks()


async def run_one_shot(agent: Agent, prompt: str) -> None:
    async def confirm_fn(message: str, request: dict | None = None) -> dict:
        return read_confirm_answer((request or {}).get("options") or ["once", "deny"])

    agent.set_confirm_fn(confirm_fn)
    await agent.chat(prompt)
    await agent.drain_background_skill_tasks()


async def run_print(agent: Agent, prompt: str, output_format: str = "text", out=None) -> int:
    """``keelson -p``: run one request without asking anything, print only the answer.

    Progress, warnings and errors go to stderr; stdout gets the final answer (or one JSON
    object with ``--output-format json``). Anything that would need a confirmation is denied
    and the model is told why. Exit code: 0 done, 1 the end-of-turn checks failed, 2 error.
    """
    from . import ui

    out = out or sys.stdout
    ui.set_terminal_enabled(False)
    ui.console.file = sys.stderr

    async def deny(message: str, request: dict | None = None) -> dict:
        return {"choice": "deny", "reason": t("print.denied")}

    async def no_plan_approval(plan_content: str) -> dict:
        return {"choice": "manual-execute"}

    agent.set_confirm_fn(deny)
    agent.set_plan_approval_fn(no_plan_approval)
    code, error = 0, None
    try:
        await agent.chat(prompt)
    except Exception as e:  # noqa: BLE001 - reported, then the exit code says it failed
        code, error = 2, str(e)
        print(f"error: {e}", file=sys.stderr)
    try:
        await agent.drain_background_skill_tasks()
    except Exception:
        pass
    outcome = agent.last_outcome
    if code == 0 and outcome is not None and outcome.status == "failed":
        code = 1
    answer = agent.last_answer()
    if output_format == "json":
        hit = min(agent.total_cache_hit_tokens, agent.total_input_tokens)
        out.write(json.dumps({
            "result": answer,
            "verify": outcome.event() if outcome is not None else None,
            "usage": {"input_tokens": agent.total_input_tokens, "output_tokens": agent.total_output_tokens,
                      "cache_hit_tokens": hit, "cost_usd": round(agent._get_current_cost_usd(), 6)},
            "model": agent.model,
            "session_id": agent.session_id,
            "error": error,
        }, ensure_ascii=False) + "\n")
    elif answer:
        out.write(answer.rstrip("\n") + "\n")
    out.flush()
    return code


def main() -> None:
    """CLI 程序入口：准备运行配置，创建 Agent，并按参数选择一次性执行或交互模式。"""
    # Windows consoles default to a legacy codepage (GBK on zh-CN); force UTF-8
    # so spinner glyphs and non-ASCII tool output never raise UnicodeEncodeError.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # -P/--profile works for every sub-command, so it is taken out first.
    from . import profiles

    sys.argv = [sys.argv[0], *profiles.take_profile_flag(sys.argv[1:])]

    # Subcommands with their own options.
    if len(sys.argv) > 1 and sys.argv[1] == "use":
        sys.exit(profiles.use_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "trust":
        from . import trust

        sys.exit(trust.trust_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        from .web.cli import main as web_main

        sys.exit(web_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("doctor", "init"):
        from . import doctor

        run = doctor.doctor_main if sys.argv[1] == "doctor" else doctor.init_main
        sys.exit(run(sys.argv[2:]))

    # 解析命令行参数，例如 --plan、--resume、--model，以及可选的一次性 prompt。
    args = parse_args()
    _load_env_file()

    if args.help:
        # 自定义帮助文本，展示 Keelson 支持的启动参数和 REPL 内置命令。
        print("""
Usage: keelson [options] [prompt]
       keelson web [--port 8765] [--replay] [--no-open]    start the web console
       keelson init [--project | --profile NAME]           save model + key (all projects / this one / a profile)
       keelson use [NAME]                                  list profiles, or switch to one
       keelson doctor [--offline]                          check that this install works
       keelson trust [--revoke]                            let this folder's allow rules and MCP servers apply

Options:
  -P, --profile NAME  Use this profile for one run (see `keelson use`)
  --yolo, -y          Skip all confirmation prompts (bypassPermissions mode)
  --plan              Plan mode: read-only, describe changes without executing
  --accept-edits      Auto-approve file edits, still confirm dangerous shell
  --dont-ask          Auto-deny anything needing confirmation (for CI)
  --thinking          Turn on the model's thinking mode (Claude, DeepSeek)
  --model, -m         Model to use (default: deepseek-flash, or MODEL from the config)
  --api-base URL      Override the API base URL from the config
  --resume            Resume the last session (messages, task ledger, folded memory)
  -p, --print         Answer one request without asking anything; print only the answer.
                      The prompt comes from the arguments, stdin, or both. Exit code 1 if the
                      end-of-turn checks failed, 2 on an error.
  --output-format F   With -p: text (default) or json {result, verify, usage, session_id}
  --max-cost USD      Stop when estimated cost exceeds this amount
  --max-turns N       Stop after N agentic turns
  --help, -h          Show this help

REPL commands: type /help inside keelson.

Examples:
  keelson "fix the bug in src/app.ts"
  keelson --yolo "run all tests and fix failures"
  keelson --plan "how would you refactor this?"
  keelson --max-cost 0.50 --max-turns 20 "implement feature X"
  MODEL=deepseek-flash APIKEY=sk-xxx API=https://api.deepseek.com keelson "hello"
  MODEL=gpt-4o OPENAI_API_KEY=sk-xxx OPENAI_BASE_URL=https://aihubmix.com/v1 keelson "hello"
  keelson --resume
  keelson -p "explain src/app.ts"
  git diff | keelson -p --output-format json "review this change"
  keelson  # starts interactive REPL
""")
        sys.exit(0)

    # 将命令行布尔开关统一转换成 Agent 内部使用的权限模式。
    permission_mode = _resolve_permission_mode(args)
    # 模型优先使用命令行参数，其次读取 .env 中的 MODEL，最后回落到默认模型。
    model = args.model or os.environ.get("MODEL") or models.DEFAULT_MODEL
    resolved_api_base, resolved_api_key, resolved_use_openai = _resolve_api_config(args.api_base)
    if models.retirement_note(model):
        print_warning(models.retirement_note(model) + " Run `keelson init` to switch.")

    # 没有可用 API key 时无法调用模型，直接提示配置方式并退出。
    if not resolved_api_key:
        print_error(
            "No model is configured.\n"
            "  keelson init      set one up step by step\n"
            "  keelson doctor    see what is missing\n"
            "  keelson web       the web console still works without a key (replay mode)"
        )
        sys.exit(1)

    # 创建主 Agent。OpenAI-compatible 和 Anthropic 原生接口使用不同的 base URL 参数名传入。
    agent = Agent(
        permission_mode=permission_mode,
        model=model,
        thinking=args.thinking,
        max_cost_usd=args.max_cost,
        max_turns=args.max_turns,
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
    )

    # Resume session
    # --resume 会加载最近一次会话，把历史消息恢复到新建的 Agent 中。
    if args.resume:
        session_id = get_latest_session_id()
        if not session_id:
            print_info("No previous sessions found.")
        elif not agent.resume(session_id):
            print_info("No session found to resume.")

    # 如果命令行后面带了普通文本参数，就拼成一次性 prompt；否则进入交互式 REPL。
    prompt = " ".join(args.prompt) if args.prompt else None

    if args.print_mode:
        piped = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        text = "\n\n".join(part for part in (prompt, piped.strip()) if part)
        if not text:
            print(t("print.empty"), file=sys.stderr)
            sys.exit(2)
        sys.exit(asyncio.run(run_print(agent, text, args.output_format)))

    if prompt:
        # One-shot mode
        # 一次性模式：执行完用户 prompt 后进程结束。
        try:
            asyncio.run(run_one_shot(agent, prompt))
        except Exception as e:
            print_error(str(e))
            sys.exit(1)
    else:
        # Interactive REPL
        # 交互模式：启动循环读取用户输入，直到用户退出。
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()

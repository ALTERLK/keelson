"""``keelson doctor`` and ``keelson init`` — getting a new install working.

``doctor`` checks the things that most often stop a first run (Python version,
a configured model, whether that model actually answers, the external tools
the built-in tools shell out to) and says how to fix each one. ``init`` writes
a ``.env`` interactively and then runs the same checks.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import shutil
import sys
from pathlib import Path
from typing import Any

OK, WARN, FAIL = "ok", "warn", "fail"

PRESETS: list[dict[str, str]] = [
    {"name": "OpenAI-compatible endpoint (most gateways)", "base": "https://api.openai.com/v1", "model": "gpt-4o", "kind": "openai"},
    {"name": "DeepSeek", "base": "https://api.deepseek.com/anthropic", "model": "deepseek-chat", "kind": "anthropic"},
    {"name": "Anthropic", "base": "https://api.anthropic.com", "model": "claude-sonnet-4-5", "kind": "anthropic-native"},
    {"name": "Other Anthropic-compatible endpoint", "base": "https://your-host/anthropic", "model": "", "kind": "anthropic"},
]


def _check(check_id: str, label: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"id": check_id, "label": label, "status": status, "detail": detail, "fix": fix}


def _display_path(path: Path) -> str:
    """A path as people write it: ~ for the home directory (and no user name in a screenshot)."""
    try:
        return "~/" + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return str(path)


def _resolved_config() -> tuple[str | None, str | None, bool, str]:
    from .main import _load_env_file, _resolve_api_config

    _load_env_file()
    base, key, use_openai = _resolve_api_config(None)
    return base, key, use_openai, os.environ.get("MODEL") or "deepseek-chat"


async def _ping(base: str | None, key: str, use_openai: bool, model: str, timeout: float = 20.0) -> tuple[bool, str]:
    """One minimal request, to tell 'configured' apart from 'actually works'."""
    try:
        if use_openai:
            import openai

            client = openai.AsyncOpenAI(base_url=base, api_key=key, timeout=timeout, max_retries=0)
            reply = await client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "Reply with: ok"}], max_tokens=16,
            )
            text = reply.choices[0].message.content or ""
        else:
            import anthropic

            kwargs: dict[str, Any] = {"api_key": key, "timeout": timeout, "max_retries": 0}
            if base:
                kwargs["base_url"] = base
            reply = await anthropic.AsyncAnthropic(**kwargs).messages.create(
                model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with: ok"}],
            )
            text = "".join(getattr(block, "text", "") for block in reply.content)
        return True, f"{model} answered ({text.strip()[:20] or 'empty reply'})"
    except Exception as exc:  # every failure here is something to show the user
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return False, f"{type(exc).__name__}: {first_line[:160]}"


async def run_checks(*, ping_model: bool = True) -> list[dict[str, str]]:
    from . import __version__

    checks: list[dict[str, str]] = [_check("version", "Keelson", OK, __version__)]

    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info >= (3, 11):  # noqa: UP036 - can still be run from source on an older interpreter
        checks.append(_check("python", "Python", OK, version))
    else:
        checks.append(_check("python", "Python", FAIL, f"{version} — 3.11 or newer is required",
                             "Install Python 3.11+ and recreate the virtual environment (start.bat does this)."))

    base, key, use_openai, model = _resolved_config()
    if key:
        protocol = "OpenAI-compatible" if use_openai else "Anthropic-compatible"
        checks.append(_check("key", "API key", OK, f"configured ({len(key)} characters)"))
        checks.append(_check("endpoint", "Endpoint", OK, f"{base or 'provider default'} · {protocol} · model {model}"))
        if ping_model:
            reachable, detail = await _ping(base, key, use_openai, model)
            checks.append(_check(
                "model", "Model answers", OK if reachable else FAIL, detail,
                "" if reachable else "Check the key, the base URL and the model name in .env. "
                                     "A base URL whose path contains /anthropic uses the Anthropic protocol.",
            ))
    else:
        checks.append(_check("key", "API key", FAIL, "not configured — the console will run in replay mode only",
                             "Run `keelson init`, or copy .env.example to .env and fill in a key."))

    for tool, why, required in (
        ("git", "used by the built-in tools and the benchmarks", True),
        ("rg", "ripgrep makes grep_search much faster; a Python fallback is used without it", False),
    ):
        path = shutil.which(tool)
        if path:
            checks.append(_check(tool, tool, OK, "found on PATH"))
        else:
            checks.append(_check(tool, tool, FAIL if required else WARN, f"not found — {why}",
                                 f"Install {tool} and make sure it is on PATH."))

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

        checks.append(_check("web", "Web console", OK, "fastapi and uvicorn are installed"))
    except ImportError:
        checks.append(_check("web", "Web console", WARN, "extra packages are missing",
                             'pip install -e ".[web]"'))

    from . import paths

    home = paths.user_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("home", "State directory", OK, _display_path(home)))
    except OSError as exc:
        checks.append(_check("home", "State directory", FAIL, f"{home} is not writable: {exc}",
                             "Point KEELSON_HOME at a writable directory."))
    return checks


def _print(checks: list[dict[str, str]]) -> None:
    from rich.console import Console
    from rich.table import Table

    marks = {OK: "[green]✓[/green]", WARN: "[yellow]![/yellow]", FAIL: "[red]✗[/red]"}
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    for check in checks:
        table.add_row(marks[check["status"]], check["label"], check["detail"])
        if check["fix"]:
            table.add_row("", "", f"[dim]→ {check['fix']}[/dim]")
    console = Console(highlight=False)
    console.print()
    console.print(table)
    failed = sum(1 for c in checks if c["status"] == FAIL)
    console.print()
    if failed:
        console.print(f"[red]{failed} problem(s) to fix.[/red]")
    else:
        console.print("[green]Ready.[/green] Start with `keelson web` (browser) or `keelson` (terminal).")
    console.print()


def doctor_main(argv: list[str] | None = None) -> int:
    offline = "--offline" in (argv or [])
    checks = asyncio.run(run_checks(ping_model=not offline))
    _print(checks)
    return 1 if any(c["status"] == FAIL for c in checks) else 0


# ─── init ───────────────────────────────────────────────────


def render_env(preset: dict[str, str], base: str, model: str, key: str) -> str:
    """The .env text for one provider choice."""
    lines = ["# Written by `keelson init`. Keep this file out of version control."]
    if preset["kind"] == "openai":
        lines += [f"OPENAI_API_KEY={key}", f"OPENAI_BASE_URL={base}"]
    elif preset["kind"] == "anthropic-native":
        lines += [f"ANTHROPIC_API_KEY={key}", f"ANTHROPIC_BASE_URL={base}"]
    else:
        lines += [f"APIKEY={key}", f"API={base}"]
    lines.append(f"MODEL={model}")
    return "\n".join(lines) + "\n"


def init_main(argv: list[str] | None = None) -> int:
    env_path = Path.cwd() / ".env"
    print("\nKeelson setup — this writes a .env file in the current folder.\n")
    if env_path.exists():
        answer = input(f"{env_path} already exists. Overwrite it? [y/N] ").strip().lower()
        if not answer.startswith("y"):
            print("Left unchanged.")
            return 0

    for number, preset in enumerate(PRESETS, 1):
        print(f"  {number}. {preset['name']}")
    choice = input(f"\nProvider [1-{len(PRESETS)}, default 1]: ").strip() or "1"
    if not choice.isdigit() or not 1 <= int(choice) <= len(PRESETS):
        print("Not one of the options.")
        return 2
    preset = PRESETS[int(choice) - 1]

    base = input(f"Base URL [{preset['base']}]: ").strip() or preset["base"]
    model = input(f"Model [{preset['model'] or 'required'}]: ").strip() or preset["model"]
    if not model:
        print("A model name is required.")
        return 2
    key = getpass.getpass("API key (input is hidden): ").strip()
    if not key:
        print("No key entered; nothing written.")
        return 2

    env_path.write_text(render_env(preset, base, model, key), encoding="utf-8", newline="\n")
    print(f"\nWrote {env_path}. Checking that it works...")
    # Variables from an older .env may still be in this process; start clean.
    for name in ("APIKEY", "API", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                 "KEELSON_API_KEY", "KEELSON_API_BASE", "MODEL"):
        os.environ.pop(name, None)
    return doctor_main([])

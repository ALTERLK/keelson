"""Named model setups, switched with one command.

A profile is ``~/.keelson/profiles/<name>.env``: the same KEY=value lines as
config.env (key, base URL, model, options). The active one is chosen by, in order:

1. ``keelson -P <name> ...`` or the ``KEELSON_PROFILE`` environment variable
2. ``KEELSON_PROFILE=<name>`` in ``<project>/.keelson/config.env``
3. ``KEELSON_PROFILE=<name>`` in ``~/.keelson/config.env`` (what ``keelson use`` sets)

The active profile's settings come before the config files (only real
environment variables beat them), so switching profile really switches.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values

from . import paths

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$")


def profiles_dir() -> Path:
    return paths.user_home() / "profiles"


def profile_file(name: str) -> Path:
    return profiles_dir() / f"{name}.env"


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def list_profiles() -> list[str]:
    folder = profiles_dir()
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.env") if valid_name(p.stem))


def _profile_setting(file: Path) -> str | None:
    if not file.is_file():
        return None
    value = (dotenv_values(file).get("KEELSON_PROFILE") or "").strip()
    return value or None


def active_profile() -> str | None:
    """The chosen profile's name, or None when none is chosen."""
    from_env = (os.environ.get("KEELSON_PROFILE") or "").strip()
    if from_env:
        return from_env
    return _profile_setting(paths.project_config_file()) or _profile_setting(paths.user_config_file())


def describe(name: str) -> dict[str, str]:
    """Model and endpoint of a profile, never its key."""
    values = dotenv_values(profile_file(name)) if profile_file(name).is_file() else {}
    base = (values.get("API") or values.get("KEELSON_API_BASE") or values.get("OPENAI_BASE_URL")
            or values.get("ANTHROPIC_BASE_URL") or "")
    has_key = any(values.get(k) for k in ("APIKEY", "KEELSON_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"))
    return {"model": values.get("MODEL") or "", "base": base, "key": "set" if has_key else "missing"}


def set_active(name: str | None) -> Path:
    """Write (or remove) KEELSON_PROFILE in ~/.keelson/config.env, keeping every other line."""
    config = paths.user_config_file()
    lines = config.read_text(encoding="utf-8").splitlines() if config.is_file() else []
    kept = [line for line in lines if not re.match(r"\s*KEELSON_PROFILE\s*=", line)]
    if name:
        kept.append(f"KEELSON_PROFILE={name}")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8", newline="\n")
    return config


def take_profile_flag(argv: list[str]) -> list[str]:
    """Remove ``-P name`` / ``--profile name`` / ``--profile=name`` from argv and export it.

    Done before any sub-command parses its own options, so the flag works for
    ``keelson``, ``keelson web`` and ``keelson doctor`` alike.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-P", "--profile") and i + 1 < len(argv):
            os.environ["KEELSON_PROFILE"] = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--profile="):
            os.environ["KEELSON_PROFILE"] = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def use_main(argv: list[str]) -> int:
    """``keelson use``: list profiles; ``keelson use <name>``: make it the default; ``--none`` clears it."""
    names = list_profiles()
    if not argv:
        current = active_profile()
        if not names:
            print("No profiles yet. Create one with: keelson init --profile <name>")
            return 0
        print()
        for name in names:
            info = describe(name)
            mark = "*" if name == current else " "
            print(f"  {mark} {name:<12} {info['model'] or '(no model)':<24} {info['base'] or '(provider default)'}"
                  + ("" if info["key"] == "set" else "   [no key]"))
        print("\n  keelson use <name>      switch (all projects)\n  keelson -P <name> ...   one run only\n")
        return 0
    if argv[0] == "--none":
        set_active(None)
        print("No profile selected; config.env and ./.env are used as before.")
        return 0
    name = argv[0]
    if name not in names:
        print(f"No profile named {name!r}. Existing: {', '.join(names) or 'none'}. "
              f"Create one with: keelson init --profile {name}")
        return 2
    set_active(name)
    info = describe(name)
    print(f"Now using {name}: {info['model']} at {info['base'] or 'the provider default'}.")
    return 0

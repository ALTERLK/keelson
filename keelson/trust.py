"""Workspace trust: a cloned repository cannot grant itself permissions.

A project can ship ``.keelson/settings.json`` with ``allow`` rules and a
``.mcp.json`` that starts MCP servers. Both are code execution in disguise: a
rule like ``run_shell(*)`` or a server command runs on your machine. So until
you trust a folder, its allow rules (also those in ``settings.local.json``)
and its MCP servers are ignored. Its *deny* rules still apply — they only
take permissions away.

Trusted folders are listed in ``~/.keelson/trusted-folders.json``. A folder is
trusted with ``keelson trust``, by answering yes when the terminal asks, or by
choosing "always allow in this project" at a confirmation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import paths


def _file() -> Path:
    return paths.user_home() / "trusted-folders.json"


def _key(folder: Path | None) -> str:
    return os.path.normcase(os.path.realpath(folder or Path.cwd()))


def _load() -> list[str]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(x) for x in data.get("folders", [])] if isinstance(data, dict) else []


def is_trusted(folder: Path | None = None) -> bool:
    if paths.env_flag("KEELSON_TRUST_ALL"):
        return True
    key = _key(folder)
    return any(key == entry or key.startswith(entry.rstrip("\\/") + os.sep) for entry in _load())


def trust(folder: Path | None = None) -> None:
    folders = _load()
    key = _key(folder)
    if key not in folders:
        folders.append(key)
        target = _file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"folders": folders}, indent=1, ensure_ascii=False), encoding="utf-8")


def untrust(folder: Path | None = None) -> bool:
    folders = _load()
    key = _key(folder)
    if key not in folders:
        return False
    folders.remove(key)
    _file().write_text(json.dumps({"folders": folders}, indent=1, ensure_ascii=False), encoding="utf-8")
    return True


def _allow_rules(file: Path) -> list[str]:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    perms = data.get("permissions") if isinstance(data, dict) else None
    return [str(r) for r in (perms or {}).get("allow", [])] if isinstance(perms, dict) else []


def _mcp_servers(folder: Path) -> list[str]:
    names: list[str] = []
    for file in (folder / ".mcp.json", paths.project_settings_file(folder)):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            names += list(servers)
    return names


def what_it_enables(folder: Path | None = None) -> list[str]:
    """What trusting this folder would switch on (empty: nothing to decide)."""
    folder = folder or Path.cwd()
    found = []
    rules = _allow_rules(paths.project_settings_file(folder)) + _allow_rules(paths.project_local_settings_file(folder))
    if rules:
        found.append("allow rules: " + ", ".join(rules[:8]) + (" ..." if len(rules) > 8 else ""))
    servers = _mcp_servers(folder)
    if servers:
        found.append("MCP servers: " + ", ".join(servers[:8]))
    return found


def needs_decision(folder: Path | None = None) -> list[str]:
    return [] if is_trusted(folder) else what_it_enables(folder)


def trust_main(argv: list[str] | None = None) -> int:
    """``keelson trust [--revoke]`` for the current folder."""
    argv = list(argv or [])
    if "--revoke" in argv:
        print("No longer trusted." if untrust() else "This folder was not trusted.")
        return 0
    enables = what_it_enables()
    trust()
    print(f"Trusted {Path.cwd()}.")
    for line in enables:
        print(f"  now in effect — {line}")
    return 0

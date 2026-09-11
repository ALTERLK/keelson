"""Centralized runtime-path resolution for Keelson.

Runtime state used to be scattered across modules with the same helper logic
copy-pasted in each one. This module is the single source of truth.

Layout
------
User-level state lives under one root:

* ``$KEELSON_HOME`` if set, else ``~/.keelson``

    ├── skills/                     project-independent skills
    ├── agents/                     custom sub-agent definitions
    ├── plans/                      plan-mode plan files
    ├── sessions/                   saved sessions (for --resume)
    ├── tool-results/               spilled large tool outputs
    └── memory/projects/<hash>/memory/   long-term memory, per project

Project-level state lives under ``<cwd>/.keelson/``:

    ├── skills/  agents/  rules/  sessions/  skill-evolution/
    └── settings.json
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

__all__ = [
    "user_home",
    "project_hash",
    "user_skills_dir",
    "user_agents_dir",
    "user_settings_file",
    "plans_dir",
    "user_sessions_dir",
    "tool_results_dir",
    "memory_dir",
    "project_dir",
    "project_skills_dir",
    "project_agents_dir",
    "project_settings_file",
    "project_rules_dir",
    "project_sessions_dir",
    "skill_evolution_dir",
    "env_flag",
    "env_value",
]


def project_hash(cwd: Path | None = None) -> str:
    """Stable 16-hex digest of the working directory — isolates per-project state."""
    base = str((cwd or Path.cwd()).resolve())
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def user_home() -> Path:
    """User-level runtime root: ``$KEELSON_HOME`` or ``~/.keelson``."""
    override = os.environ.get("KEELSON_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".keelson"


# ---------------------------------------------------------------- user level ---

def user_skills_dir() -> Path:
    return user_home() / "skills"


def user_agents_dir() -> Path:
    return user_home() / "agents"


def user_settings_file() -> Path:
    return user_home() / "settings.json"


def plans_dir() -> Path:
    return user_home() / "plans"


def user_sessions_dir() -> Path:
    return user_home() / "sessions"


def tool_results_dir() -> Path:
    return user_home() / "tool-results"


def memory_dir(project_hash: str) -> Path:
    """Long-term memory directory for the given project hash."""
    return user_home() / "memory" / "projects" / project_hash / "memory"


# ------------------------------------------------------------- project level ---

def project_dir(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ".keelson"


def project_skills_dir(cwd: Path | None = None) -> Path:
    return project_dir(cwd) / "skills"


def project_agents_dir(cwd: Path | None = None) -> Path:
    return project_dir(cwd) / "agents"


def project_settings_file(cwd: Path | None = None) -> Path:
    return project_dir(cwd) / "settings.json"


def project_rules_dir(cwd: Path | None = None) -> Path:
    return project_dir(cwd) / "rules"


def project_sessions_dir(cwd: Path | None = None) -> Path:
    return project_dir(cwd) / "sessions"


def skill_evolution_dir(cwd: Path | None = None) -> Path:
    return project_dir(cwd) / "skill-evolution"


# ------------------------------------------------------------ env var access ---

def env_value(name: str, legacy: str = "", default: str = "") -> str:
    """Read an env var, with an optional deprecated-alias fallback."""
    val = os.environ.get(name)
    if val is None and legacy:
        val = os.environ.get(legacy)
    return default if val is None else val


def env_flag(name: str, legacy: str = "", default: bool = False) -> bool:
    raw = env_value(name, legacy, "").strip().lower()
    if raw == "":
        return default
    return raw not in ("0", "false", "no", "off")

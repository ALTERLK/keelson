"""Scope guard: a request's changes stay within what it is about.

The failure this answers: asked to fix one thing, an agent also "improves"
code nobody asked about, and those changes bring regressions. Claude Code users
asked for a strict-scope mode in its issue tracker.

How it works:

* The scope of a request is the files the user ``@``-mentioned plus what the
  model declared with ``declare_scope`` (files, folders or globs, with a
  reason). It is shown to the user and reset with each new request.
* While a scope exists, changing a file outside it asks the user first (in
  ``dontAsk`` mode it is refused). A file the user allowed joins the scope.
* Files changed outside the scope, and a large change for a short request,
  are reported at the end of the turn.

Honest limits: with no scope declared and nothing mentioned the guard is idle;
the model can widen its own scope with another ``declare_scope`` (the user
sees each one); shell commands are not checked against the scope.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .platforms import IS_WINDOWS

_MENTION = re.compile(r"(?<![\w@/\\])@([^\s@,;'\"`()<>\[\]{}，。；：]+)")
_GLOB = re.compile(r"[*?\[]")

# "A short request" and "a large change", for the size warning.
SHORT_REQUEST_CHARS = 200
LARGE_CHANGE_FILES = 8


def normalize(path: str, cwd: Path | None = None) -> str:
    """Project-relative, forward slashes; absolute (still forward slashes) for paths outside the project."""
    cwd = (cwd or Path.cwd()).resolve()
    raw = str(path).strip().strip("\"'")
    if _GLOB.search(raw):
        return raw.replace("\\", "/").removeprefix("./")
    p = Path(raw)
    full = (p if p.is_absolute() else cwd / p)
    full = Path(os.path.normpath(str(full)))
    try:
        rel = full.relative_to(cwd)
        text = rel.as_posix()
        return "." if text in ("", ".") else text
    except ValueError:
        return full.as_posix()


def _fold(text: str) -> str:
    return text.casefold() if IS_WINDOWS else text


def in_scope(path: str, scope: list[str], cwd: Path | None = None) -> bool:
    target = _fold(normalize(path, cwd))
    for entry in scope:
        e = _fold(normalize(entry, cwd)).rstrip("/")
        if e in (".", ""):
            return True
        if _GLOB.search(e):
            if fnmatch.fnmatchcase(target, e) or fnmatch.fnmatchcase(target, e + "/*"):
                return True
            continue
        if target == e or target.startswith(e + "/"):
            return True
    return False


def mentioned_paths(text: str, cwd: Path | None = None) -> list[str]:
    """``@file`` / ``@folder/`` mentions in a user message that name something that exists."""
    cwd = cwd or Path.cwd()
    found = []
    for raw in _MENTION.findall(text or ""):
        candidate = raw.rstrip(".:!?)")
        if not candidate:
            continue
        p = Path(candidate)
        if (p if p.is_absolute() else cwd / p).exists():
            norm = normalize(candidate, cwd)
            if norm not in found:
                found.append(norm)
    return found


def outside(paths: list[str], scope: list[str], cwd: Path | None = None) -> list[str]:
    return [p for p in paths if p and not p.startswith("(") and not in_scope(p, scope, cwd)]


def size_warning(request: str, changed: list[str]) -> str | None:
    files = [c for c in changed if not c.startswith("(")]
    if len(_strip_tags(request)) <= SHORT_REQUEST_CHARS and len(files) >= LARGE_CHANGE_FILES:
        return f"{len(files)} files changed for a short request; check that all of them were needed"
    return None


def _strip_tags(text: str) -> str:
    return re.sub(r"<(git-status|keelson-[\w-]+)>.*?</\1>", "", text or "", flags=re.DOTALL).strip()

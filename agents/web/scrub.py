"""Remove machine-specific details from a recorded trace before it is shared.

A trace holds tool results verbatim, and tool results contain paths: the
temporary workspace, the interpreter in a pytest banner, the home directory in
a traceback. ``scrub`` rewrites the known ones to neutral aliases and ``leaks``
is the check afterwards — a trace that still names the user or an absolute
Windows path is not saved.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

BACKSLASH = "\\"

# In a JSON trace a path separator is two backslashes (or a slash). A single
# backslash is an escape such as \n and must not be mistaken for a path, and
# the lookbehind keeps "https://" from looking like drive "s:".
_DRIVE_PATH = re.compile(r'(?<![A-Za-z])[A-Za-z]:(?:\\\\|/(?!/))[^\\/"]+(?:\\\\|/)')


def _short_path(path: str) -> str:
    """Windows 8.3 form of a path (temp dirs often show up in that form)."""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024):
            return buf.value
    except Exception:
        pass
    return ""


def _spellings(path: str) -> set[str]:
    """One path as it can appear in a JSON trace.

    Backslashes are doubled by JSON encoding, and doubled again when a tool
    result is itself JSON carried inside a JSON string.
    """
    if not path:
        return set()
    return {path, path.replace(BACKSLASH, "/"), path.replace(BACKSLASH, BACKSLASH * 2), path.replace(BACKSLASH, BACKSLASH * 4)}


def scrub(text: str, replacements: list[tuple[str, str]]) -> str:
    """Rewrite each raw path in the serialised trace to its alias.

    Every spelling of the path is tried, longest first, and the replacements
    are applied in the order given — list a directory before its parent.
    """
    for raw, alias in replacements:
        variants = _spellings(raw) | _spellings(_short_path(raw))
        for variant in sorted(variants, key=len, reverse=True):
            text = re.sub(re.escape(variant), lambda _match: alias, text, flags=re.IGNORECASE)
    return text


def leaks(text: str) -> list[str]:
    """What is still in the text that should not be published."""
    found = []
    home = str(Path.home())
    for needle in (*_spellings(home), os.environ.get("USERNAME", "")):
        if needle and needle.lower() in text.lower() and needle not in found:
            found.append(needle)
    if re.search(r"sk-[A-Za-z0-9]{16,}", text):
        found.append("something shaped like an API key")
    drive_path = _DRIVE_PATH.search(text)
    if drive_path:
        found.append(f"an absolute Windows path ({drive_path.group(0)[:40]}...)")
    return found

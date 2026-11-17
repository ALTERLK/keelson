"""list_files and grep_search: fast where possible, .gitignore-aware everywhere.

Which files are searched: ``rg --files`` when ripgrep is installed, else
``git ls-files`` (tracked + untracked, minus ignored) inside a git repository,
else a folder walk that skips the usual dependency and cache folders. Matching
lines are found with ripgrep when it is installed, else with Python's ``re``.
Text is decoded the way read_file decodes it (UTF-8, then GBK).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from keelson import textio
from keelson.platforms import IS_WINDOWS, no_window_flags

MAX_CANDIDATES = 50_000
LIST_LIMIT = 200
GREP_COLLECT = 200
GREP_SHOW = 100
GREP_LINE_CHARS = 300
GREP_MAX_FILE_BYTES = 2_000_000

WALK_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".next", ".gradle",
}


# ─── which files ────────────────────────────────────────────


def _run(args: list[str], cwd: Path, timeout: float = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, cwd=str(cwd), capture_output=True, timeout=timeout, **no_window_flags())
    except (OSError, subprocess.SubprocessError):
        return None


def _from_rg(base: Path) -> list[Path] | None:
    rg = shutil.which("rg")
    if not rg:
        return None
    done = _run([rg, "--files", "--hidden", "-g", "!.git", "--null"], base)
    if done is None or done.returncode not in (0, 1):
        return None
    return [base / name for name in done.stdout.decode("utf-8", "replace").split("\0") if name]


def _from_git(base: Path) -> list[Path] | None:
    git = shutil.which("git")
    if not git:
        return None
    done = _run([git, "-c", "core.quotepath=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], base)
    if done is None or done.returncode != 0:
        return None  # not a git repository
    names = dict.fromkeys(n for n in done.stdout.decode("utf-8", "replace").split("\0") if n)
    return [base / name for name in names if (base / name).is_file()]  # tracked but deleted files drop out


def _from_walk(base: Path) -> list[Path]:
    found: list[Path] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in WALK_SKIP_DIRS)
        for name in sorted(files):
            found.append(Path(root) / name)
            if len(found) >= MAX_CANDIDATES:
                return found
    return found


def candidate_files(base: Path) -> list[Path]:
    if base.is_file():
        return [base]
    for source in (_from_rg, _from_git):
        files = source(base)
        if files is not None:
            return files[:MAX_CANDIDATES]
    return _from_walk(base)


# ─── glob patterns ──────────────────────────────────────────


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """``**/``, ``*``, ``?``, ``[abc]`` and ``{a,b}`` over forward-slash paths."""
    out, i, n = [], 0, len(pattern)
    depth = 0
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        c = pattern[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            close = pattern.find("]", i + 1)
            if close < 0:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1:close]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = close
        elif c == "{":
            depth += 1
            out.append("(?:")
        elif c == "}" and depth:
            depth -= 1
            out.append(")")
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE if IS_WINDOWS else 0)


def _display(path: Path) -> str:
    """Relative to the working folder when inside it, with forward slashes."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


# ─── list_files ─────────────────────────────────────────────


def list_files(pattern: str, base: Path) -> str:
    pattern = (pattern or "*").replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern or "*"
    files = candidate_files(base)
    regex = glob_to_regex(pattern)
    hits = [f for f in files if regex.match(_rel(f, base))]
    note = ""
    if not hits and "/" not in pattern:
        regex = glob_to_regex("**/" + pattern)
        hits = [f for f in files if regex.match(_rel(f, base))]
        if hits:
            note = f"(no match at the top level; showing **/{pattern})\n"
    if not hits:
        return "No files found matching the pattern (files ignored by .gitignore are not listed)."

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    hits.sort(key=mtime, reverse=True)  # most recently changed first
    shown = "\n".join(_display(f) for f in hits[:LIST_LIMIT])
    more = f"\n...and {len(hits) - LIST_LIMIT} more files" if len(hits) > LIST_LIMIT else ""
    return note + shown + more


# ─── grep_search ────────────────────────────────────────────


def _format_matches(matches: list[str], total: int) -> str:
    if not matches:
        return "No matches found."
    out = "\n".join(matches[:GREP_SHOW])
    if total > GREP_SHOW:
        out += f"\n... and {total - GREP_SHOW}{'+' if total >= GREP_COLLECT else ''} more matches"
    return out


def _clip(line: str) -> str:
    line = line.rstrip("\r\n")
    return line if len(line) <= GREP_LINE_CHARS else line[:GREP_LINE_CHARS] + " …"


def _grep_rg(pattern: str, base: Path, include: str | None, ignore_case: bool) -> str | None:
    rg = shutil.which("rg")
    if not rg:
        return None
    args = [rg, "--line-number", "--no-heading", "--color", "never", "--hidden", "-g", "!.git",
            "--max-columns", str(GREP_LINE_CHARS), "--max-columns-preview"]
    if ignore_case:
        args.append("-i")
    if include:
        args += ["-g", include]
    args += ["-e", pattern, "--", str(base)]
    done = _run(args, Path.cwd(), timeout=30)
    if done is None or done.returncode == 2:
        return None  # a pattern ripgrep's regex engine rejects: Python's may accept it
    if done.returncode == 1:
        return "No matches found."
    lines = [line for line in done.stdout.decode("utf-8", "replace").splitlines() if line]
    shown = []
    for line in lines[:GREP_COLLECT]:
        path, sep, rest = line.partition(":")
        if IS_WINDOWS and len(path) == 1 and sep:   # a drive letter: C:\...
            more, sep, rest = rest.partition(":")
            path = f"{path}:{more}"
        shown.append(f"{_display(Path(path))}:{rest}" if sep else line)
    return _format_matches(shown, len(lines))


def _include_matcher(include: str | None):
    if not include:
        return lambda rel: True
    include = include.replace("\\", "/")
    if "/" in include:
        regex = glob_to_regex(include)
        return lambda rel: bool(regex.match(rel))
    regex = glob_to_regex(include)
    return lambda rel: bool(regex.match(rel.rsplit("/", 1)[-1]))


def _grep_python(pattern: str, base: Path, include: str | None, ignore_case: bool) -> str:
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return f"Error: invalid regular expression ({e}). Escape characters like ( [ . * + ? with a backslash."
    wanted = _include_matcher(include)
    root = base if base.is_dir() else base.parent
    matches: list[str] = []
    total = 0
    for path in candidate_files(base):
        if not wanted(_rel(path, root)):
            continue
        try:
            if path.stat().st_size > GREP_MAX_FILE_BYTES:
                continue
            tf = textio.decode(path.read_bytes(), name=path.name)
        except (OSError, textio.NotTextError):
            continue
        shown_path = _display(path)
        for number, line in enumerate(tf.lines, 1):
            if regex.search(line):
                total += 1
                if len(matches) < GREP_COLLECT:
                    matches.append(f"{shown_path}:{number}:{_clip(line)}")
        if total >= GREP_COLLECT:
            break
    return _format_matches(matches, total)


def grep(pattern: str, base: Path, include: str | None = None, ignore_case: bool = False) -> str:
    if not base.exists():
        return f"Error: {base} does not exist."
    return _grep_rg(pattern, base, include, ignore_case) or _grep_python(pattern, base, include, ignore_case)

"""The shell run_shell uses, and how a command is handed to it.

Windows: PowerShell 7 (``pwsh``) when installed, else Windows PowerShell 5.1,
which every Windows 10/11 has. ``KEELSON_SHELL=cmd`` keeps the old cmd.exe
behaviour. Elsewhere: ``/bin/sh``, as before.

A PowerShell command is written to a temporary UTF-8 ``.ps1`` file and run with
``-File``. Measured on Windows PowerShell 5.1: ``-EncodedCommand`` reports
errors on stderr as CLIXML (``#< CLIXML <Objs ...``), and ``-Command`` strips
embedded double quotes; ``-File`` gives plain-text errors and the right exit
code. ``-ExecutionPolicy Bypass`` applies to that one process only; no system
setting is changed.

Models write bash out of habit. Before running anything in PowerShell, the
command is checked for bash-only syntax (``rm -rf``, ``export``, ``&&`` on 5.1,
``/dev/null``, heredocs, ...). Such a command is not run; the model is told
how to write it in PowerShell instead. Running it would fail anyway, often
with an error that does not say why.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import IS_WINDOWS

POWERSHELL_PRELUDE = (
    "$ProgressPreference = 'SilentlyContinue'; "
    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
    "$OutputEncoding = [Console]::OutputEncoding; "
    "if ($PSStyle) { $PSStyle.OutputRendering = 'PlainText' }; "
    "$global:LASTEXITCODE = 0"
)
# $? right after the user's last statement decides the exit code, as in sh:
# a failed native program gives its own exit code, a failed cmdlet gives 1.
POWERSHELL_EPILOGUE = (
    "\n$__keelson_ok = $?\n"
    "if (-not $__keelson_ok) { if ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }\n"
    "exit 0\n"
)


@dataclass(frozen=True)
class Shell:
    kind: str                 # "powershell" | "cmd" | "posix"
    exe: str
    label: str                # what the model is told
    and_or: bool = True       # supports `a && b` / `a || b`

    @property
    def is_powershell(self) -> bool:
        return self.kind == "powershell"


def _windows_powershell() -> str | None:
    found = shutil.which("powershell")
    if found:
        return found
    root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else None


@cache
def _detect(preference: str) -> Shell:
    if not IS_WINDOWS:
        return Shell("posix", "/bin/sh", "/bin/sh (POSIX shell)")
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    cmd = Shell("cmd", comspec, "cmd.exe")
    if preference == "cmd":
        return cmd
    if preference in ("", "pwsh", "powershell"):
        pwsh = shutil.which("pwsh") if preference != "powershell" else None
        if pwsh:
            return Shell("powershell", pwsh, "PowerShell 7 (pwsh)", and_or=True)
        legacy = _windows_powershell()
        if legacy:
            return Shell("powershell", legacy, "Windows PowerShell 5.1", and_or=False)
    return cmd


def current() -> Shell:
    """The shell for this process. KEELSON_SHELL: pwsh | powershell (5.1 only) | cmd."""
    return _detect((os.environ.get("KEELSON_SHELL") or "").strip().lower())


def child_env() -> dict[str, str]:
    """Environment for commands: Python children print UTF-8, which PowerShell is set to read."""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def write_powershell_script(command: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="keelson-", suffix=".ps1")
    # UTF-8 *with* BOM: Windows PowerShell 5.1 reads a BOM-less script as the ANSI code page.
    with os.fdopen(handle, "w", encoding="utf-8-sig", newline="\r\n") as script:
        script.write(POWERSHELL_PRELUDE + "\n" + command.rstrip() + POWERSHELL_EPILOGUE)
    return Path(name)


def powershell_argv(shell: Shell, script: Path) -> list[str]:
    return [shell.exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]


def tidy_powershell_errors(text: str, script: Path) -> str:
    """Error positions refer to the user's command, not the temporary file."""
    if not text:
        return text

    def replace(match: re.Match) -> str:
        if match.group(1):
            return f"line {max(1, int(match.group(1)) - 1)}"   # the prelude is line 1
        return "<command>"

    # Matched by file name, not full path: PowerShell may print the long form of a
    # folder that Python's TEMP holds in 8.3 form (C:\Users\RUNNER~1\... on CI).
    name = re.split(r"[\\/]", str(script))[-1]
    pattern = r"""[^\s'"]*""" + re.escape(name) + r"(?::(\d+))?"
    return re.sub(pattern, replace, text)


# ─── bash habits in a PowerShell command ────────────────────


def _segments(command: str) -> list[str]:
    """Top-level pieces between ; | && || and newlines, quotes respected."""
    pieces, current, quote, i = [], [], "", 0
    while i < len(command):
        c = command[i]
        if quote:
            current.append(c)
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "'\"":
            quote = c
            current.append(c)
            i += 1
            continue
        if command.startswith(("&&", "||"), i):
            pieces.append("".join(current))
            current = []
            i += 2
            continue
        if c in ";|\n":
            pieces.append("".join(current))
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    pieces.append("".join(current))
    return [p.strip() for p in pieces if p.strip()]


def _unquoted(command: str) -> str:
    """The command with quoted strings blanked out, for operator checks."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", lambda m: " " * len(m.group(0)), command)


_ENV_PREFIX = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S*)\s+(\S.*)$")


def _segment_advice(seg: str) -> str | None:
    words = seg.split()
    head = words[0].lower() if words else ""
    rest = " ".join(words[1:])
    flags = [w for w in words[1:] if w.startswith("-") and len(w) > 1]

    m = _ENV_PREFIX.match(seg)
    if m:
        return f"`{seg}` → `$env:{m.group(1)} = '{m.group(2)}'; {m.group(3)}`"
    if head == "export" and "=" in rest:
        name, _, value = rest.partition("=")
        return f"`{seg}` → `$env:{name.strip()} = '{value.strip().strip(chr(34)).strip(chr(39))}'`"
    if head == "rm" and flags:   # deleting is refused anyway (safety.py); this only catches bash syntax
        return f"`{seg}` → use the delete_files tool (it moves files to the Recycle Bin)"
    if head == "cp" and any("r" in f.lower() for f in flags):
        targets = " ".join(w for w in words[1:] if not w.startswith("-"))
        return f"`{seg}` → `Copy-Item -Recurse {targets}`"
    if head == "ls" and flags:
        return f"`{seg}` → `Get-ChildItem -Force` (or the list_files tool)"
    if head == "touch":
        return f"`{seg}` → `New-Item -ItemType File {rest}` (or the write_file tool)"
    if head == "which":
        return f"`{seg}` → `(Get-Command {rest}).Source`"
    if head in ("grep", "egrep", "rg") and not shutil.which(head):
        return f"`{seg}` → use the grep_search tool (or `Select-String`)"
    if head in ("head", "tail"):
        return f"`{seg}` → use read_file with offset/limit (or `Select-Object -First/-Last N`)"
    if head in ("sed", "awk"):
        return f"`{seg}` → use the edit_file tool"
    if head == "source":
        return f"`{seg}` → `. {rest}`"
    if head == "chmod":
        return f"`{seg}` → not needed on Windows; leave it out"
    quoted_program = re.match(r"""^(['"])([^'"]+)\1(\s|$)""", seg)
    if quoted_program and (re.search(r"[\\/]", quoted_program.group(2))
                           or re.search(r"\.(exe|cmd|bat|ps1|py)$", quoted_program.group(2), re.IGNORECASE)):
        return f"`{seg}` → `& {seg}` (a quoted program path needs the call operator &)"
    return None


def powershell_problems(command: str, shell: Shell) -> list[str]:
    plain = _unquoted(command)
    found: list[str] = []
    # On the raw text: the delimiter is often quoted (<<'EOF'). A letter must follow,
    # so a shift like `x<<2` inside a Python one-liner is not mistaken for one.
    if re.search(r"<<-?\s*['\"]?[A-Za-z_]\w*['\"]?[ \t]*($|[>|])", command, re.MULTILINE):
        found.append("heredoc `<<EOF` → a here-string: @'\\n...\\n'@ (or write the file with write_file)")
    if "/dev/null" in plain:
        found.append("`/dev/null` → `$null` (for example `2>$null`)")
    if re.search(r"(?<![\w$])\d?>\s*nul\b", plain, re.IGNORECASE):
        found.append("`> nul` → `> $null` (`nul` is a cmd.exe name)")
    if not shell.and_or and re.search(r"&&|\|\|", plain):
        found.append("`a && b` → `a; if ($?) { b }` (Windows PowerShell 5.1 has no && or ||)")
    for seg in _segments(command):
        advice = _segment_advice(seg)
        if advice and advice not in found:
            found.append(advice)
    return found


def preflight(command: str, shell: Shell | None = None) -> str | None:
    """Why this command will not work in the shell, with the fix, or None."""
    shell = shell or current()
    if not shell.is_powershell:
        return None
    problems = powershell_problems(command, shell)
    if not problems:
        return None
    lines = "\n".join(f"  - {p}" for p in problems)
    return (f"Command not run: the shell is {shell.label}, not bash. Rewrite it for PowerShell:\n{lines}\n"
            "Then run it again.")

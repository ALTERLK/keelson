"""What an action can break, decided before it runs.

Two questions, asked of every file path and every shell command:

1. **Where does it land?** :func:`zone` sorts a path into

   * ``workspace`` — inside the project. Checkpoints cover it, so ``/undo``
     takes the change back;
   * ``scratch`` — the temp folder and Keelson's own plan and memory folders;
   * ``outside`` — anywhere else. Allowed, but only after asking;
   * ``protected`` — system folders and Keelson's checkpoint store, and, for
     anything that deletes or moves, also a drive root, the home folder, the
     project folder itself and every folder above those. Never touched.

   Paths are checked *after* expanding ``~``, ``$HOME``, ``$env:NAME``,
   ``%NAME%`` and ``$NAME``. A variable that cannot be expanded is reported,
   not guessed: ``rm -rf $DIR/`` with ``DIR`` unset is ``rm -rf /``.

2. **Can it be undone?** :func:`assess_shell` splits a command into its parts
   (``;``, ``&&``, ``||``, ``|``, newlines) and judges each one. Deleting from
   the shell is refused outright — ``delete_files`` moves things to the Recycle
   Bin instead. Things ``/undo`` cannot reverse (installing packages, network
   access, ``git push``, history rewrites, writing outside the project) are
   confirmed, with the reason. Everything else runs: it only changes files in
   the project, which the checkpoint taken at the start of the turn covers.

This is a guard rail, not a sandbox. A program the command starts (a test
suite, a build script) can still do anything its user account can; see
``docs/safety.md`` for what is and is not covered.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .platforms import IS_WINDOWS

WORKSPACE, SCRATCH, OUTSIDE, PROTECTED = "workspace", "scratch", "outside", "protected"


# ─── paths ───────────────────────────────────────────────────


def _real(path: Path) -> Path:
    try:
        return Path(os.path.realpath(path))
    except (OSError, ValueError):
        return Path(os.path.abspath(path))


def _key(path: Path) -> str:
    text = os.path.normcase(str(path))
    stripped = text.rstrip("\\/")
    return stripped if stripped and not stripped.endswith(":") else text


def _within(child: Path, parent: Path) -> bool:
    c, p = _key(child), _key(parent)
    if c == p:
        return True
    sep = "\\" if IS_WINDOWS else "/"
    return c.startswith(p if p.endswith(sep) else p + sep)


def _system_roots() -> list[Path]:
    if IS_WINDOWS:
        names = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
        found = {os.environ.get(name) for name in names}
        return [_real(Path(p)) for p in found if p]
    return [Path(p) for p in ("/bin", "/sbin", "/usr", "/etc", "/boot", "/lib", "/lib32", "/lib64", "/opt",
                              "/proc", "/sys", "/dev", "/System", "/Library", "/Applications",
                              "/private/etc", "/var/lib")]


def _scratch_roots() -> list[Path]:
    home = paths.user_home()
    return [_real(Path(tempfile.gettempdir())), _real(paths.plans_dir()), _real(home / "memory")]


def checkpoint_store() -> Path:
    """Where checkpoints live; the agent may never touch it."""
    return paths.user_home() / "projects"


def describe_protected(path: Path, workspace: Path | None = None, *, destructive: bool = True) -> str | None:
    """Why ``path`` is off limits, or None if it is not.

    ``destructive``: the action deletes, moves or changes permissions of
    ``path`` itself. Writing a file *into* the home folder is merely outside
    the project; moving the home folder is never allowed.
    """
    p = _real(path)
    ws = _real(workspace or Path.cwd())
    if _within(p, _real(checkpoint_store())):
        return "Keelson's checkpoint store"
    if destructive:
        home = _real(Path.home())
        if _key(p) == _key(Path(p.anchor)):
            return "a drive root" if IS_WINDOWS else "the filesystem root"
        if _within(home, p):
            return "your home folder" if _key(p) == _key(home) else "a folder that contains your home folder"
        if _within(ws, p):
            return "the project folder itself" if _key(p) == _key(ws) else "a folder that contains the project"
    if _within(p, ws):
        return None
    for root in _system_roots():
        if _within(p, root):
            return f"a system folder ({root})"
    return None


def zone(path: Path, workspace: Path | None = None, *, destructive: bool = True) -> str:
    if describe_protected(path, workspace, destructive=destructive):
        return PROTECTED
    p = _real(path)
    if _within(p, _real(workspace or Path.cwd())):
        return WORKSPACE
    if any(_within(p, root) for root in _scratch_roots()):
        return SCRATCH
    return OUTSIDE


# ─── variables ───────────────────────────────────────────────

_VAR = re.compile(
    r"\$\{env:(?P<psenv_b>\w+)\}"         # ${env:NAME}
    r"|\$env:(?P<psenv>\w+)"              # $env:NAME
    r"|%(?P<cmd>\w+)%"                    # %NAME%
    r"|\$\{(?P<brace>\w+)\}"              # ${NAME}
    r"|\$(?P<plain>[A-Za-z_]\w*)",        # $NAME
    re.IGNORECASE,
)
_HOME_NAMES = {"home", "userprofile"}


def expand(text: str, local: dict[str, str] | None = None, *, cwd: Path | None = None) -> tuple[str, list[str]]:
    """Expand ``~`` and variables the way the shell would; also list the ones it could not."""
    local = {k.lower(): v for k, v in (local or {}).items()}
    unresolved: list[str] = []

    def env(name: str) -> str | None:
        if name.lower() in _HOME_NAMES:
            return str(Path.home())
        if name.lower() == "pwd":
            return str(cwd or Path.cwd())
        if name.lower() in local:
            return local[name.lower()]
        return os.environ.get(name)   # case-insensitive on Windows, as the shells there are

    def replace(match: re.Match) -> str:
        name = next(g for g in match.groups() if g)
        value = env(name)
        if value is None:
            unresolved.append(match.group(0))
            return match.group(0)
        return value

    out = _VAR.sub(replace, text)
    if out == "~" or out.startswith(("~/", "~\\")):
        out = str(Path.home()) + out[1:]
    return out, unresolved


# ─── commands ────────────────────────────────────────────────


@dataclass
class Verdict:
    action: str = "allow"                 # "allow" | "confirm" | "block"
    reasons: list[str] = field(default_factory=list)
    dangerous: bool = False               # a confirm that may never be "always allowed"
    rule_keys: list[str] = field(default_factory=list)   # command prefixes an "always allow" rule covers
    unkeyed: bool = False                 # some part needs confirming that no rule can cover
    read_only: bool = True                # only commands that look at things

    def need(self, reason: str, *, key: str = "", dangerous: bool = False) -> None:
        if self.action != "block":
            self.action = "confirm"
        if reason not in self.reasons:
            self.reasons.append(reason)
        self.dangerous = self.dangerous or dangerous
        if key and not dangerous:
            if key not in self.rule_keys:
                self.rule_keys.append(key)
        else:
            self.unkeyed = True

    def refuse(self, reason: str) -> None:
        self.action = "block"
        self.reasons.insert(0, reason)

    @property
    def can_always_allow(self) -> bool:
        return self.action == "confirm" and not self.dangerous and not self.unkeyed and bool(self.rule_keys)


DELETE_ADVICE = ("Deleting from the shell is turned off in Keelson. Use the delete_files tool instead: it moves "
                 "files to the Recycle Bin, so the deletion can be undone.")

_DELETE_VERBS = {"rm", "rmdir", "rd", "del", "erase", "unlink", "shred", "srm", "remove-item", "ri", "rimraf",
                 "remove-itemproperty"}
_LAUNCHERS = {"sudo", "doas", "nohup", "time", "exec", "command", "builtin", "env", "xargs", "call", "&", ".",
              "start", "nice", "ionice", "timeout", "watch", "-exec", "-execdir", "-ok", "foreach-object", "%"}
_WRITE_VERBS = {"mv", "move", "move-item", "mi", "cp", "copy", "copy-item", "cpi", "xcopy", "robocopy", "ren",
                "rename", "rename-item", "rni", "mkdir", "md", "new-item", "ni", "touch", "set-content", "sc",
                "add-content", "ac", "out-file", "tee", "tee-object", "ln", "chmod", "chown", "icacls", "takeown",
                "attrib", "install", "clear-content", "clc", "expand-archive", "tar", "unzip", "7z", "rsync"}
_DESTRUCTIVE_VERBS = {"mv", "move", "move-item", "mi", "ren", "rename", "rename-item", "rni", "robocopy", "rsync",
                      "chmod", "chown", "icacls", "takeown", "attrib", "clear-content", "clc"}
_CD_VERBS = {"cd", "chdir", "set-location", "sl", "pushd", "push-location"}
_SYSTEM = {"mkfs", "format", "diskpart", "fdisk", "parted", "shutdown", "reboot", "halt", "poweroff",
           "stop-computer", "restart-computer", "bcdedit", "vssadmin", "wipefs", "cipher", "format-volume",
           "clear-disk", "initialize-disk", "remove-partition", "reg"}
# Commands that erase disks or partitions (with mkfs.*, `dd of=/dev/...` and `format X:`): refused outright.
_WIPE = {"format-volume", "clear-disk", "initialize-disk", "remove-partition", "wipefs", "diskpart"}
_ELEVATE = {"sudo", "doas", "runas", "su", "gsudo"}
_KILL = {"kill", "pkill", "killall", "taskkill", "stop-process", "spps", "tskill"}
_NETWORK = {"curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "ssh", "scp", "sftp", "ftp",
            "nc", "ncat", "telnet", "start-bitstransfer", "aria2c"}
_INSTALLERS = {
    "pip": {"install", "uninstall", "download"}, "pip3": {"install", "uninstall", "download"},
    "uv": {"add", "remove", "pip", "tool", "sync"}, "poetry": {"add", "remove", "install", "update", "publish"},
    "npm": {"install", "i", "ci", "add", "uninstall", "remove", "rm", "update", "up", "publish", "link"},
    "yarn": {"", "add", "install", "remove", "upgrade", "global", "publish", "dlx"},
    "pnpm": {"add", "install", "i", "remove", "rm", "update", "up", "publish", "dlx"},
    "bun": {"add", "install", "i", "remove", "update", "publish", "x"},
    "cargo": {"install", "add", "remove", "publish", "uninstall"},
    "go": {"get", "install"}, "gem": {"install", "uninstall", "push"},
    "conda": {"install", "create", "remove", "update", "uninstall"}, "mamba": {"install", "create", "remove"},
    "apt": {"install", "remove", "purge", "upgrade"}, "apt-get": {"install", "remove", "purge", "upgrade"},
    "yum": {"install", "remove"}, "dnf": {"install", "remove"}, "pacman": {"-s", "-r", "-syu"},
    "brew": {"install", "uninstall", "upgrade", "reinstall"}, "winget": {"install", "uninstall", "upgrade"},
    "choco": {"install", "uninstall", "upgrade"}, "scoop": {"install", "uninstall", "update"},
    "composer": {"require", "install", "update", "remove"}, "dotnet": {"add", "tool"},
    "twine": {"upload"}, "docker": {"push", "pull", "login"},
}
_ALWAYS_INSTALL = {"npx", "pipx", "install-module", "install-package", "install-script", "uvx", "bunx"}
_PUBLISH = {"publish", "upload"}
_INLINE = {"python": ("-c",), "python3": ("-c",), "py": ("-c",), "node": ("-e", "--eval", "-p"),
           "ruby": ("-e",), "perl": ("-e",), "php": ("-r",)}
_SUBSHELLS = {"bash": ("-c",), "sh": ("-c",), "zsh": ("-c",), "cmd": ("/c", "/k"),
              "powershell": ("-command", "-c"), "pwsh": ("-command", "-c")}
_CODE_DELETES = re.compile(
    r"rmtree|os\.remove|os\.unlink|\.unlink\(|os\.rmdir|removedirs|fs\.rm|fs\.unlink|rimraf|"
    r"File\]::Delete|Directory\]::Delete|Remove-Item|FileUtils\.rm|\bunlink\b",
    re.IGNORECASE,
)
_GIT_HISTORY = {
    "rebase": "rewrites git history", "filter-branch": "rewrites git history",
    "filter-repo": "rewrites git history", "reflog": "can drop git history",
    "update-ref": "moves git refs directly", "gc": "can drop unreachable commits for good",
}
_READ_ONLY = {"ls", "dir", "gci", "get-childitem", "cat", "type", "gc", "get-content", "echo", "write-output",
              "write-host", "pwd", "get-location", "select-string", "sls", "findstr", "where", "which",
              "get-command", "gcm", "get-item", "gi", "test-path", "resolve-path", "measure-object", "sort-object",
              "select-object", "format-table", "format-list", "head", "tail", "wc", "grep", "rg", "find", "tree",
              "date", "get-date", "whoami", "hostname", "env", "printenv", "get-process", "ps", "du", "df",
              "file", "stat", "diff", "fc", "more", "less", "sort", "uniq", "cut", "true", "false", "exit"}
_GIT_READ_ONLY = {"status", "diff", "log", "show", "branch", "rev-parse", "remote", "ls-files", "blame",
                  "describe", "shortlog", "config", "grep", "fetch", "tag", "stash", "version", "help"}


def tokens(segment: str) -> list[str]:
    """Words of one command, quotes removed, backslashes kept (Windows paths)."""
    out, cur, quote, has = [], [], "", False
    for ch in segment:
        if quote:
            if ch == quote:
                quote = ""
            else:
                cur.append(ch)
            continue
        if ch in "'\"":
            quote, has = ch, True
            continue
        if ch.isspace():
            if cur or has:
                out.append("".join(cur))
            cur, has = [], False
            continue
        cur.append(ch)
    if cur or has:
        out.append("".join(cur))
    return out


def _verb(word: str) -> str:
    word = word.lstrip("({&").rstrip(")};").lower()
    word = re.split(r"[\\/]", word)[-1]
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if word.endswith(ext):
            word = word[: -len(ext)]
    return word


def _command_positions(words: list[str]) -> list[int]:
    """Indexes of words that start a command: the first word, and words after a launcher or `{`."""
    found = [0] if words else []
    for i, w in enumerate(words[:-1]):
        if _verb(w) in _LAUNCHERS or w.endswith(("{", "(")):
            found.append(i + 1)
    for i, w in enumerate(words):
        if w.startswith(("{", "(")) and len(w) > 1 and i not in found:
            found.append(i)
    return sorted(set(found))


_ASSIGN_PS = re.compile(r"^\$(\w+)\s*=\s*(['\"]?)([^'\"$]*)\2\s*$")
_ASSIGN_SH = re.compile(r"^(?:export\s+|set\s+)?([A-Za-z_]\w*)=(['\"]?)([^'\"$\s]*)\2\s*$", re.IGNORECASE)
_REDIRECT = re.compile(r"(?<![<>\d&])\d?>>?\s*(\"[^\"]*\"|'[^']*'|[^\s;|&]+)")


def _redirect_targets(segment: str) -> list[str]:
    targets = []
    for m in _REDIRECT.finditer(segment):
        raw = m.group(1).strip("'\"")
        if raw.lower() in ("$null", "nul", "/dev/null", "/dev/stdout", "/dev/stderr") or raw.startswith("&"):
            continue
        targets.append(raw)
    return targets


def _subcommand(words: list[str], start: int) -> str:
    """The first word after ``start`` that is not an option (``git -C x push`` → ``push``)."""
    i = start + 1
    while i < len(words):
        w = words[i]
        if w in ("-C", "-c", "--git-dir", "--work-tree", "--prefix", "--cwd", "--dir") and i + 1 < len(words):
            i += 2
            continue
        if w.startswith("-") and not re.match(r"^-[SR]", w):   # pacman -S / -R are the subcommand
            i += 1
            continue
        return w.lower()
    return ""


class _Assessor:
    def __init__(self, workspace: Path, cwd: Path) -> None:
        self.workspace = workspace
        self.cwd: Path | None = cwd
        self.local: dict[str, str] = {}
        self.verdict = Verdict()

    def check_target(self, raw: str, verb: str, segment: str) -> None:
        """A path argument of a command that writes, moves or changes permissions."""
        if not raw or raw.startswith("-"):
            return
        text, unresolved = expand(raw, self.local, cwd=self.cwd)
        if unresolved:
            self.verdict.need(f"`{segment}` uses {', '.join(unresolved)}, which is not set here, so it could act "
                              "on a different folder than intended", dangerous=True)
            return
        path = Path(text)
        if not path.is_absolute():
            if self.cwd is None:
                self.verdict.need(f"`{segment}` runs after a `cd` to a folder that could not be worked out",
                                  dangerous=True)
                return
            path = self.cwd / path
        destructive = verb in _DESTRUCTIVE_VERBS
        why = describe_protected(path, self.workspace, destructive=destructive)
        if why:
            self.verdict.refuse(f"Refused: `{segment}` would change {why} ({path}). Keelson never writes, moves "
                                "or deletes there.")
            return
        if zone(path, self.workspace, destructive=destructive) == OUTSIDE:
            self.verdict.need(f"writes outside the project ({path}); /undo cannot take that back")

    def segment(self, seg: str) -> None:
        m = _ASSIGN_PS.match(seg) or _ASSIGN_SH.match(seg)
        if m:
            value, _ = expand(m.group(3), self.local, cwd=self.cwd)
            self.local[m.group(1)] = value
            return
        words = tokens(seg)
        while words and re.match(r"^[A-Za-z_]\w*=", words[0]):    # FOO=1 cmd
            words = words[1:]
        if not words:
            return
        self._judge(seg, words)
        for target in _redirect_targets(seg):
            self.verdict.read_only = False
            self.check_target(target, ">", seg)

    def _judge(self, seg: str, words: list[str]) -> None:
        verbs = [(_verb(words[i]), i) for i in _command_positions(words)]
        head, at = verbs[0]
        if head not in _READ_ONLY and not (head == "git" and _subcommand(words, at) in _GIT_READ_ONLY):
            self.verdict.read_only = False

        # Deleting: refused everywhere; the model is pointed at delete_files.
        if any(v in _DELETE_VERBS for v, _ in verbs) or (head == "find" and "-delete" in words):
            self.verdict.refuse(DELETE_ADVICE)
            return

        # Erasing a disk or partition: nothing in the project can bring it back, so never, in any mode.
        for v, i in verbs:
            rest = [w.lower() for w in words[i + 1:]]
            if (v in _WIPE or v.startswith("mkfs")
                    or (v == "dd" and any(w.startswith("of=") and ("/dev/" in w or "physicaldrive" in w)
                                          for w in rest))
                    or (v == "format" and any(re.match(r"^[a-z]:\\?$", w) for w in rest))):
                self.verdict.refuse(f"Refused: `{seg}` erases a disk or partition. Keelson never runs that; "
                                    "if it is really wanted, the user has to do it themselves.")
                return

        if head in _CD_VERBS:
            target = next((w for w in words[1:] if not w.startswith("-")), "~")
            text, unresolved = expand(target, self.local, cwd=self.cwd)
            if unresolved or self.cwd is None:
                self.cwd = None
            else:
                p = Path(text)
                self.cwd = p if p.is_absolute() else self.cwd / p
            return

        for v, _ in verbs:
            if v in _ELEVATE or (v == "start-process" and any(w.lower() == "runas" for w in words)):
                self.verdict.need(f"`{seg}` runs with administrator rights", dangerous=True)
            if v in _SYSTEM and not (v == "reg" and _subcommand(words, at) in ("query", "export")):
                self.verdict.need(f"`{seg}` changes the system (disks, boot, power, registry); nothing can undo it",
                                  dangerous=True)
            if v in _KILL:
                self.verdict.need(f"`{seg}` stops running programs", key=v)

        if head == "git":
            self._git(seg, words, at)
        elif head == "gh" and _subcommand(words, at) not in ("auth", "status", "help", ""):
            self.verdict.need(f"`{seg}` acts on GitHub", key=f"gh {_subcommand(words, at)}")
        elif head in _INSTALLERS or head in _ALWAYS_INSTALL or (head in ("python", "python3", "py")
                                                                and words[1:3] == ["-m", "pip"]):
            self._installer(seg, words, head, at)
        elif head in _NETWORK:
            self.verdict.need(f"`{seg}` uses the network", key=head)
        elif head in ("invoke-expression", "iex"):
            self.verdict.need(f"`{seg}` runs code built at run time, which Keelson cannot check", dangerous=True)
        if any(v in _NETWORK for v, _ in verbs[1:]):
            self.verdict.need(f"`{seg}` uses the network")

        if head in _SUBSHELLS:
            for i, w in enumerate(words[1:], 1):
                if w.lower() in _SUBSHELLS[head] and i + 1 < len(words):
                    inner = " ".join(words[i + 1:]) if head == "cmd" else words[i + 1]
                    nested = assess_shell(inner, workspace=self.workspace, cwd=self.cwd)
                    if nested.action == "block":
                        self.verdict.refuse(nested.reasons[0])
                    elif nested.action == "confirm":
                        for reason in nested.reasons:
                            self.verdict.need(reason, dangerous=nested.dangerous)
                    break
        if head in _INLINE:
            for i, w in enumerate(words[1:], 1):
                if w in _INLINE[head] and i + 1 < len(words) and _CODE_DELETES.search(words[i + 1]):
                    self.verdict.need(f"`{seg}` runs inline code that deletes files, which Keelson cannot check "
                                      "(delete_files deletes into the Recycle Bin)", dangerous=True)
                    break

        write = next(((v, i) for v, i in verbs if v in _WRITE_VERBS), None)
        if write:
            verb, i = write
            for w in words[i + 1:]:
                if IS_WINDOWS and re.match(r"^/[A-Za-z]{1,4}(:\S*)?$", w):    # cmd switches: /s /q /MIR /XD:x
                    continue
                self.check_target(w, verb, seg)

    def _git(self, seg: str, words: list[str], at: int) -> None:
        sub = _subcommand(words, at)
        rest = [w for w in words[at + 1:]]
        low = [w.lower() for w in rest]
        if sub == "push":
            force = any(w in ("-f", "--force", "--mirror", "--delete", "-d") or w.startswith(("+", "--force"))
                        for w in low)
            if force:
                self.verdict.need(f"`{seg}` overwrites or deletes commits on the remote", dangerous=True)
            else:
                self.verdict.need(f"`{seg}` publishes commits to the remote; /undo cannot take that back",
                                  key="git push")
        elif sub in ("clone", "pull", "submodule"):
            self.verdict.need(f"`{seg}` downloads from the network", key=f"git {sub}")
        elif sub == "clean":
            ignored_too = any(w.startswith("-") and not w.startswith("--") and "x" in w.lower() for w in rest)
            if ignored_too:
                self.verdict.need(f"`{seg}` deletes ignored files for good; no checkpoint covers them",
                                  dangerous=True)
            else:
                self.verdict.need(f"`{seg}` deletes untracked files (a checkpoint keeps a copy)", key="git clean")
        elif sub == "reset" and "--hard" in low:
            self.verdict.need(f"`{seg}` discards uncommitted changes and moves the branch", key="git reset --hard")
        elif sub in ("checkout", "restore") and ("." in rest or "--" in rest) and "--staged" not in low:
            self.verdict.need(f"`{seg}` discards uncommitted changes", key=f"git {sub}")
        elif sub == "stash" and any(w in ("drop", "clear") for w in low):
            self.verdict.need(f"`{seg}` throws away stashed work", dangerous=True)
        elif sub == "branch" and any(w in ("-d", "-D", "--delete") for w in rest):
            self.verdict.need(f"`{seg}` deletes a branch", key="git branch -d")
        elif sub == "tag" and any(w in ("-d", "--delete") for w in low):
            self.verdict.need(f"`{seg}` deletes a tag", key="git tag -d")
        elif sub == "commit" and "--amend" in low:
            self.verdict.need(f"`{seg}` rewrites the last commit", key="git commit --amend")
        elif sub in _GIT_HISTORY:
            self.verdict.need(f"`{seg}` {_GIT_HISTORY[sub]}", key=f"git {sub}")

    def _installer(self, seg: str, words: list[str], head: str, at: int) -> None:
        if head in ("python", "python3", "py"):
            head, sub = "pip", _subcommand(words[2:], 0)
        else:
            sub = _subcommand(words, at)
        if head in _ALWAYS_INSTALL:
            self.verdict.need(f"`{seg}` downloads and runs packages from the network", key=head)
            return
        actions = _INSTALLERS[head]
        if sub in actions or ("" in actions and not sub):
            if sub in _PUBLISH or (head in ("docker", "gem") and sub in ("push",)):
                self.verdict.need(f"`{seg}` publishes to a public registry; it cannot be taken back", dangerous=True)
            else:
                self.verdict.need(f"`{seg}` installs, removes or downloads packages over the network; /undo does "
                                  "not reverse that", key=f"{head} {sub}".strip())


def assess_shell(command: str, *, workspace: Path | None = None, cwd: Path | None = None) -> Verdict:
    from .platforms.shell import _segments

    ws = _real(workspace or Path.cwd())
    assessor = _Assessor(ws, cwd or ws)
    for seg in _segments(command):
        assessor.segment(seg)
        if assessor.verdict.action == "block":
            break
    verdict = assessor.verdict
    if verdict.action != "block" and any(_verb(w) in _NETWORK for w in tokens(command)) and re.search(
            r"\|\s*(sh|bash|zsh|iex|invoke-expression|python3?|node)\b", command, re.IGNORECASE):
        verdict.need("downloads a script and runs it straight away", dangerous=True)
    return verdict


def assess_path(path: Path, *, verb: str = "write", workspace: Path | None = None) -> Verdict:
    """The verdict for a file tool (write_file, edit_file, delete_files) on one path."""
    verdict = Verdict(read_only=False)
    destructive = verb == "delete"
    why = describe_protected(path, workspace, destructive=destructive)
    if why:
        verdict.refuse(f"Refused: {path} is {why}. Keelson never {verb}s there.")
        return verdict
    if zone(path, workspace, destructive=destructive) == OUTSIDE:
        verdict.need(f"{verb}s outside the project ({path})")
    return verdict

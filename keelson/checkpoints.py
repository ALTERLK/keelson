"""Checkpoints: the project as it was when each turn started, so ``/undo`` can put it back.

Claude Code's checkpoints only see its own file-edit tools; a file changed by a
shell command (``mv``, a code generator, a formatter) is not covered. Keelson
takes a snapshot of the whole project instead, so a turn is undone together
with everything its commands did.

How
---
A *shadow* git repository, kept outside the project in
``~/.keelson/projects/<hash>/checkpoints/shadow.git`` with its own index. The
project's own ``.git`` (if any) is never read or written: every git call here
sets ``GIT_DIR``, ``GIT_WORK_TREE`` and ``GIT_INDEX_FILE`` and ignores the
user's global and system git config. Files are stored byte for byte
(``core.autocrlf=false``, no clean/smudge filters, no hooks).

At the start of a turn: ``git add -A`` + ``write-tree`` (only changed files are
hashed again, so this is quick after the first time). At the end, the same
again. ``/undo`` restores the files the turn changed to their start state;
``/redo`` puts them back.

Not covered, and said so: files ignored by ``.gitignore`` and files outside the
project — unless Keelson's own file tools changed them, in which case their
previous bytes are kept separately (``before_write``); installed packages;
commits and pushes; databases and anything on the network. Deletions made by
``delete_files`` also sit in the Recycle Bin.

Without git (or when the project folder is a drive root or the home folder),
checkpoints fall back to keeping the previous bytes of every file Keelson's
own tools change; shell commands are then not covered, and the permission
layer asks before running them.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .i18n import pick
from .platforms import no_window_flags

GIT, FILES, OFF = "git", "files", "off"

DEFAULT_EXCLUDES = """\
# Written by Keelson: never snapshotted (big, and rebuilt by tools anyway).
node_modules/
.venv/
venv/
__pycache__/
*.pyc
.mypy_cache/
.pytest_cache/
.ruff_cache/
.tox/
.nox/
.gradle/
.next/
.nuxt/
.terraform/
.DS_Store
Thumbs.db
"""

# Highest-priority attributes: store every file as raw bytes, no filters (git-lfs and the like).
NO_FILTERS = "* -text -filter -eol -ident -diff -merge -working-tree-encoding\n"


def _collapse_binary(diff: str) -> str:
    """Replace the body of each file section that holds NUL bytes with one line."""
    sections = diff.split("\ndiff --git ")
    out = []
    for i, section in enumerate(sections):
        if "\x00" in section:
            header = section.split("\n", 1)[0]
            section = f"{header}\nBinary file changed"
        out.append(section if i == 0 else "diff --git " + section)
    return "\n".join(out)


@dataclass
class Report:
    ok: bool
    message: str
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def summary(self, limit: int = 12) -> str:
        lines = [self.message]
        for label, items in (("restored", self.restored), ("removed", self.removed),
                             ("changed since, not touched", self.conflicts)):
            if items:
                shown = ", ".join(items[:limit]) + (f" (+{len(items) - limit} more)" if len(items) > limit else "")
                lines.append(f"  {label}: {shown}")
        return "\n".join(lines)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class Checkpoints:
    """Per-project checkpoint store. Safe to share between sessions in one process."""

    def __init__(self, workspace: Path, root: Path | None = None) -> None:
        self.workspace = Path(os.path.realpath(workspace))
        self.root = root or paths.project_state_dir(self.workspace) / "checkpoints"
        self.git_dir = self.root / "shadow.git"
        self.index_file = self.root / "index"
        self.state_file = self.root / "checkpoints.json"
        self.blob_dir = self.root / "blobs"
        self._lock = threading.RLock()
        self._state: dict | None = None
        self._open: dict | None = None        # the turn in progress
        self._depth = 0                       # sessions sharing this folder share the open turn
        self.mode, self.reason = self._choose_mode()
        self.timeout = float(os.environ.get("KEELSON_CHECKPOINT_TIMEOUT", "120"))
        self.max_file_bytes = int(float(os.environ.get("KEELSON_CHECKPOINT_MAX_FILE_MB", "50")) * 1024 * 1024)

    # ── setup ────────────────────────────────────────────────

    def _choose_mode(self) -> tuple[str, str]:
        if not paths.env_flag("KEELSON_CHECKPOINTS", default=True):
            return OFF, "checkpoints are turned off (KEELSON_CHECKPOINTS=0)"
        if _same_folder(self.workspace, Path(self.workspace.anchor)) or _same_folder(self.workspace, Path.home()):
            return FILES, ("the project folder is a drive root or your home folder, too big to snapshot; "
                           "only changes made by Keelson's file tools can be undone — open a project folder instead")
        if not shutil.which("git"):
            return FILES, "git was not found, so only changes made by Keelson's file tools can be undone"
        return GIT, ""

    @property
    def covers_shell(self) -> bool:
        return self.mode == GIT

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        empty_config = self.root / "empty.gitconfig"
        env.update({
            "GIT_DIR": str(self.git_dir),
            "GIT_WORK_TREE": str(self.workspace),
            "GIT_INDEX_FILE": str(self.index_file),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_AUTHOR_NAME": "keelson", "GIT_AUTHOR_EMAIL": "checkpoints@keelson.local",
            "GIT_COMMITTER_NAME": "keelson", "GIT_COMMITTER_EMAIL": "checkpoints@keelson.local",
            "LC_ALL": "C", "LANG": "C",
        })
        return env

    def _git(self, *args: str, input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args], cwd=self.workspace, env=self._env(), input=input, capture_output=True,
            timeout=self.timeout, **no_window_flags(),
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {result.stderr.decode('utf-8', 'replace').strip()[:300]}")
        return result

    def _ensure_repo(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "empty.gitconfig").write_bytes(b"")
        hooks = self.root / "no-hooks"
        hooks.mkdir(exist_ok=True)
        self._git("init", "-q")
        for key, value in (("core.autocrlf", "false"), ("core.safecrlf", "false"), ("core.longpaths", "true"),
                           ("core.quotepath", "false"), ("core.fsmonitor", "false"), ("core.hooksPath", str(hooks)),
                           ("gc.auto", "0"), ("commit.gpgsign", "false"), ("core.bare", "false")):
            self._git("config", key, value)
        info = self.git_dir / "info"
        info.mkdir(exist_ok=True)
        excludes = DEFAULT_EXCLUDES
        with contextlib.suppress(ValueError):      # Keelson's own state, if it lives inside the project
            excludes += "/" + Path(os.path.realpath(paths.user_home())).relative_to(self.workspace).as_posix() + "/\n"
        (info / "exclude").write_text(excludes, encoding="utf-8", newline="\n")
        (info / "attributes").write_text(NO_FILTERS, encoding="utf-8", newline="\n")

    # ── state ────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._state is None:
            try:
                self._state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._state = {}
            self._state.setdefault("turns", [])
            self._state.setdefault("redo", [])
        return self._state

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._load(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def turns(self) -> list[dict]:
        with self._lock:
            return list(self._load()["turns"])

    # ── snapshots ────────────────────────────────────────────

    def _skip_large_new_files(self) -> None:
        """Keep big untracked files (datasets, binaries) out of the snapshot."""
        listed = self._git("ls-files", "-z", "-o", "--exclude-standard", check=False).stdout
        big = []
        for raw in listed.split(b"\0"):
            if not raw:
                continue
            rel = raw.decode("utf-8", "replace")
            with contextlib.suppress(OSError):
                if (self.workspace / rel).stat().st_size > self.max_file_bytes:
                    big.append("/" + rel.replace("\\", "/"))
        if big:
            exclude = self.git_dir / "info" / "exclude"
            text = exclude.read_text(encoding="utf-8")
            new = [b for b in big if b not in text.splitlines()]
            if new:
                exclude.write_text(text.rstrip("\n") + "\n# too big to snapshot\n" + "\n".join(new) + "\n",
                                   encoding="utf-8", newline="\n")

    def _snapshot(self) -> str:
        self._ensure_repo()
        self._skip_large_new_files()
        # --ignore-errors: a locked or unreadable file is skipped instead of failing the whole snapshot.
        self._git("add", "-A", "--ignore-errors", "--", ".", check=False)
        tree = self._git("write-tree").stdout.decode().strip()
        parent = self._load().get("head")
        args = ["commit-tree", tree, "-m", "checkpoint"] + (["-p", parent] if parent else [])
        commit = self._git(*args).stdout.decode().strip()
        self._git("update-ref", "refs/keelson/checkpoints", commit)
        self._load()["head"] = commit
        return tree

    def _safe_snapshot(self) -> str | None:
        try:
            return self._snapshot()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            self.mode = FILES
            self.reason = f"snapshots stopped working ({exc}); only changes made by Keelson's file tools can be undone"
            return None

    # ── turn lifecycle ───────────────────────────────────────

    def begin_turn(self, label: str) -> None:
        if self.mode == OFF:
            return
        with self._lock:
            self._depth += 1
            if self._open is not None:          # another session's turn is running here: share it
                return
            state = self._load()
            start = self._safe_snapshot() if self.mode == GIT else None
            self._open = {"label": label.strip().splitlines()[0][:80] if label.strip() else "(turn)",
                          "time": time.strftime("%Y-%m-%d %H:%M:%S"), "start": start, "end": None, "extras": {}}
            state["redo"] = []
            self._save()

    def end_turn(self) -> None:
        with self._lock:
            self._depth = max(0, self._depth - 1)
            if self._depth:
                return
            turn, self._open = self._open, None
            if turn is None:
                return
            if self.mode == GIT and turn["start"]:
                turn["end"] = self._safe_snapshot()
            for path, entry in turn["extras"].items():
                entry["after"] = self._store_file(Path(path))
            changed = bool(turn["extras"]) or (turn["start"] and turn["end"] and turn["start"] != turn["end"])
            if changed:       # a turn that changed nothing is not something to undo
                self._load()["turns"].append(turn)
                self._save()

    def before_write(self, path: Path) -> None:
        """Called by the file tools before they change ``path``: keep its bytes if no snapshot does."""
        if self.mode == OFF:
            return
        with self._lock:
            turn = self._open
            if turn is None:
                return
            key = os.path.realpath(path)
            if key in turn["extras"] or Path(key).is_dir():
                return
            if self.mode == GIT and turn["start"] and self._covered(Path(key)):
                return
            turn["extras"][key] = {"before": self._store_file(Path(key)), "after": None}

    def turn_changes(self) -> list[str] | None:
        """Files changed since the running turn started (project-relative where possible); None: no turn open."""
        with self._lock:
            turn = self._open
            if turn is None:
                return None
            changed = {self._display(Path(p)) for p in turn["extras"]}
            if self.mode == GIT and turn["start"]:
                now = self._safe_snapshot()
                if now:
                    changed |= set(self._changed(turn["start"], now))
            return sorted(changed)

    def start_content(self, path: str) -> bytes | None:
        """A file's bytes when the running turn started; None if it did not exist (or is not known)."""
        with self._lock:
            turn = self._open
            if turn is None:
                return None
            target = Path(path) if Path(path).is_absolute() else self.workspace / path
            key = os.path.realpath(target)
            if key in turn["extras"]:
                digest = turn["extras"][key]["before"]
                return (self.blob_dir / digest).read_bytes() if digest else None
            if self.mode == GIT and turn["start"]:
                try:
                    rel = Path(key).relative_to(self.workspace).as_posix()
                except ValueError:
                    return None
                result = self._git("cat-file", "blob", f"{turn['start']}:{rel}", check=False)
                return result.stdout if result.returncode == 0 else None
            return None

    def _covered(self, path: Path) -> bool:
        try:
            rel = Path(os.path.realpath(path)).relative_to(self.workspace)
        except ValueError:
            return False
        ignored = self._git("check-ignore", "-q", "--no-index", "--", rel.as_posix(), check=False).returncode == 0
        return not ignored

    def _store_file(self, path: Path) -> str | None:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            return None
        digest = _sha(data)
        target = self.blob_dir / digest
        if not target.exists():
            self.blob_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return digest

    # ── restoring ────────────────────────────────────────────

    def _changed(self, a: str, b: str) -> list[str]:
        out = self._git("diff-tree", "-r", "-z", "--no-renames", "--name-only", a, b).stdout
        return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]

    def _paths_in(self, tree: str, rel_paths: list[str]) -> set[str]:
        if not rel_paths:
            return set()
        # The whole listing, not a pathspec per file: thousands of paths would not fit on a command line.
        out = self._git("ls-tree", "-r", "-z", "--name-only", tree).stdout
        return {p.decode("utf-8", "replace") for p in out.split(b"\0") if p} & set(rel_paths)

    def _restore_paths(self, tree: str, rel_paths: list[str], report: Report) -> None:
        present = self._paths_in(tree, rel_paths)
        to_write = sorted(present)
        to_remove = sorted(set(rel_paths) - present)
        if to_write:
            self._git("--literal-pathspecs", "checkout", tree, "--pathspec-from-file=-", "--pathspec-file-nul",
                      input=b"\0".join(p.encode("utf-8") for p in to_write))
            report.restored += to_write
        for rel in to_remove:
            target = self.workspace / rel
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
                report.removed.append(rel)
            self._prune_empty_dirs(target.parent)

    def _prune_empty_dirs(self, folder: Path) -> None:
        while folder != self.workspace and self.workspace in folder.parents:
            try:
                folder.rmdir()           # only succeeds when empty
            except OSError:
                return
            folder = folder.parent

    def _restore_extra(self, path: str, digest: str | None, report: Report) -> None:
        target = Path(path)
        label = self._display(target)
        if digest is None:
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
                report.removed.append(label)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((self.blob_dir / digest).read_bytes())
        report.restored.append(label)

    def _display(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)

    def undo(self, steps: int = 1, *, force: bool = False) -> Report:
        """Put back the files the last ``steps`` turns changed (shell changes included)."""
        with self._lock:
            if self._open is not None:
                return Report(False, pick("这一轮还在进行，先停下它。", "A turn is still running; stop it first."))
            turns = self._load()["turns"]
            if steps < 1 or steps > len(turns):
                return Report(False, pick("没有可以撤销的。", "Nothing to undo.") if not turns else
                              pick(f"只能撤销 {len(turns)} 轮。", f"Only {len(turns)} turn(s) can be undone."))
            undone = turns[-steps:]
            first, last = undone[0], undone[-1]
            report = Report(True, "")
            redo: dict = {"turns": undone, "tree": None, "paths": [], "extras": {}}

            # Work out everything first; change nothing until it is clear there is no conflict.
            current, touched, conflicts = None, [], []
            if self.mode == GIT and first["start"]:
                current = self._safe_snapshot()
                if current is None:
                    return Report(False, pick(f"撤销前没能拍下快照：{self.reason}",
                                              f"Could not take a snapshot before undoing: {self.reason}"))
                end = last["end"] or current
                touched = self._changed(first["start"], end)
                conflicts = sorted(set(self._changed(end, current)) & set(touched))
            extras: dict[str, str | None] = {}
            for turn in undone:                      # the earliest "before" wins
                for path, entry in turn["extras"].items():
                    extras.setdefault(path, entry["before"])
            conflicts += [self._display(Path(p)) for p in extras
                          if self._store_file(Path(p)) != self._latest_after(undone, p)]
            if conflicts and not force:
                return Report(False, pick("这些文件在那一轮结束后又被改过，撤销会覆盖这些改动。"
                                          "确定要撤销就用 `/undo --force`。",
                                          "These files were changed after that turn ended; undoing would overwrite "
                                          "those changes. Run `/undo --force` to undo anyway."), conflicts=conflicts)

            if current is not None:
                self._restore_paths(first["start"], touched, report)
                redo.update(tree=current, paths=touched)
            for path, digest in extras.items():
                redo["extras"][path] = self._store_file(Path(path))
                self._restore_extra(path, digest, report)

            del turns[-steps:]
            self._load()["redo"].append(redo)
            self._save()
            n = len(report.restored) + len(report.removed)
            report.message = pick(f"已撤销 {steps} 轮：{n} 个文件恢复到“{first['label']}”之前的样子。/redo 可以反悔。",
                                  f"Undid {steps} turn(s): {n} file(s) put back as they were before "
                                  f"\"{first['label']}\". /redo reverses this.")
            return report

    @staticmethod
    def _latest_after(turns: list[dict], path: str) -> str | None:
        for turn in reversed(turns):
            if path in turn["extras"]:
                return turn["extras"][path]["after"]
        return None

    def redo(self) -> Report:
        with self._lock:
            if self._open is not None:
                return Report(False, pick("这一轮还在进行，先停下它。", "A turn is still running; stop it first."))
            stack = self._load()["redo"]
            if not stack:
                return Report(False, pick("没有可以重做的。", "Nothing to redo."))
            entry = stack.pop()
            report = Report(True, "")
            if entry["tree"] and entry["paths"]:
                self._restore_paths(entry["tree"], entry["paths"], report)
            for path, digest in entry["extras"].items():
                self._restore_extra(path, digest, report)
            self._load()["turns"].extend(entry["turns"])
            self._save()
            report.message = pick(f"已重做 {len(entry['turns'])} 轮。", f"Redid {len(entry['turns'])} turn(s).")
            return report

    def revert(self, file: str, steps: int = 1) -> Report:
        """Put one file back as it was before the last ``steps`` turns, leaving everything else alone."""
        with self._lock:
            turns = self._load()["turns"]
            if steps < 1 or steps > len(turns):
                return Report(False, pick("没有那么早的检查点。", "No checkpoint that far back."))
            first = turns[-steps]
            target = Path(file)
            if not target.is_absolute():
                target = Path.cwd() / target
            key = os.path.realpath(target)
            report = Report(True, "")
            for turn in turns[-steps:]:
                if key in turn["extras"]:
                    self._restore_extra(key, turn["extras"][key]["before"], report)
                    break
            else:
                if self.mode != GIT or not first["start"]:
                    return Report(False, pick(f"那个检查点里没有 {file} 的副本。",
                                              f"No saved copy of {file} in that checkpoint."))
                try:
                    rel = Path(key).relative_to(self.workspace).as_posix()
                except ValueError:
                    return Report(False, pick(f"{file} 在项目之外，Keelson 也没有改过它。",
                                              f"{file} is outside the project and Keelson did not change it."))
                self._restore_paths(first["start"], [rel], report)
            report.message = pick(f"{file} 已恢复到“{first['label']}”之前的样子。",
                                  f"Put {file} back as it was before \"{first['label']}\".")
            return report

    def diff(self, steps: int = 1, *, max_chars: int = 200_000) -> tuple[str, list[str]]:
        """A unified diff of what the last ``steps`` turns changed (shell changes included), and the files."""
        with self._lock:
            turns = self._load()["turns"]
            if steps < 1 or not turns:
                return "", []
            chosen = turns[-steps:]
            first, last = chosen[0], chosen[-1]
            parts: list[str] = []
            files: list[str] = []
            if self.mode == GIT and first["start"]:
                end = last["end"] or self._safe_snapshot()
                if end:
                    with contextlib.suppress(RuntimeError, subprocess.TimeoutExpired):
                        files = self._changed(first["start"], end)
                        # The shadow repo marks every file -diff (byte-exact snapshots), so --text is needed
                        # for a readable diff; files that really are binary are collapsed afterwards.
                        out = self._git("diff", "--no-color", "--no-ext-diff", "--no-renames", "--text",
                                        first["start"], end, check=False).stdout
                        parts.append(_collapse_binary(out.decode("utf-8", "replace")))
            # Ignored or outside files Keelson's tools changed: compare the saved copy with the file now.
            extras: dict = {}
            for turn in chosen:
                for key, item in turn["extras"].items():
                    extras.setdefault(key, item)
            for key, item in extras.items():
                shown = self._display(Path(key))
                files.append(shown)
                before = (self.blob_dir / item["before"]).read_bytes() if item.get("before") else b""
                try:
                    after = Path(key).read_bytes()
                except OSError:
                    after = b""
                parts.append("".join(difflib.unified_diff(
                    before.decode("utf-8", "replace").splitlines(keepends=True),
                    after.decode("utf-8", "replace").splitlines(keepends=True),
                    f"a/{shown}", f"b/{shown}")))
            text = "\n".join(p for p in parts if p)
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n… (diff cut at {max_chars} characters)\n"
            return text, list(dict.fromkeys(files))

    def describe(self) -> list[dict]:
        """One row per undoable turn, newest first: label, time, files changed."""
        rows = []
        with self._lock:
            for i, turn in enumerate(reversed(self._load()["turns"]), 1):
                files: list[str] = []
                if self.mode == GIT and turn["start"] and turn["end"]:
                    with contextlib.suppress(RuntimeError, subprocess.TimeoutExpired):
                        files = self._changed(turn["start"], turn["end"])
                files += [self._display(Path(p)) for p in turn["extras"]]
                rows.append({"steps": i, "label": turn["label"], "time": turn["time"], "files": files})
        return rows


_stores: dict[str, Checkpoints] = {}
_stores_lock = threading.Lock()


def for_workspace(workspace: Path | None = None) -> Checkpoints:
    real = os.path.realpath(workspace or Path.cwd())
    key = os.path.normcase(real)
    with _stores_lock:
        if key not in _stores:
            _stores[key] = Checkpoints(Path(real))
        return _stores[key]


def active() -> Checkpoints | None:
    """The store for the current folder, if one has been opened (the file tools call this)."""
    return _stores.get(os.path.normcase(os.path.realpath(Path.cwd())))


def before_write(path: Path) -> None:
    store = active()
    if store is not None:
        store.before_write(path)


def reset() -> None:
    """Forget open stores (tests)."""
    with _stores_lock:
        _stores.clear()

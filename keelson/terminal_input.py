"""The REPL's input line: prompt_toolkit when there is a real terminal, plain input() otherwise.

- Enter sends; Alt+Enter (or Ctrl+J) starts a new line; a pasted block stays one message.
- History survives restarts (``~/.keelson/history.txt``).
- Tab completes ``/commands`` (with their descriptions), ``@files`` and the files after
  ``/add``, ``/drop`` and ``/revert``.
- Shift+Tab cycles the permission mode: default → accept edits → plan.
- A bottom toolbar shows model · mode · context · cost · cache · files in the conversation.

``KEELSON_SIMPLE_INPUT=1`` (or no terminal, or prompt_toolkit failing to start) falls
back to ``input()``, so pipes, tests and odd consoles keep working.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import commands, paths
from .i18n import t

if TYPE_CHECKING:
    from .agent import Agent

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".tox", "dist", "build", ".keelson"}
PATH_COMMANDS = ("/add ", "/drop ", "/revert ")
MAX_PROJECT_MATCHES = 20


def simple_input_forced() -> bool:
    return os.environ.get("KEELSON_SIMPLE_INPUT", "").strip().lower() in ("1", "true", "yes", "on")


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def status_line(agent: Agent) -> str:
    """model │ mode │ ctx │ cost │ cache │ files — the toolbar, also used by /status."""
    parts = [agent.model, t(f"mode_short.{agent.permission_mode}")]
    window = getattr(agent, "effective_window", 0) or 0
    used = getattr(agent, "last_input_token_count", 0) or 0
    if window:
        parts.append(f"{t('turn.ctx')} {used / window:.0%}")
    parts.append(f"${agent._get_current_cost_usd():.4f}")
    if agent.total_input_tokens:
        parts.append(t("turn.cache", rate=min(agent.total_cache_hit_tokens, agent.total_input_tokens)
                       / agent.total_input_tokens))
    if agent.context_files:
        parts.append("+" + ", ".join(agent.context_files[:3]) + (" …" if len(agent.context_files) > 3 else ""))
    return " │ ".join(parts)


# ── completion ────────────────────────────────────────────────


def _dir_entries(prefix: str) -> list[str]:
    """Entries of the folder ``prefix`` points into whose names start with its last part."""
    norm = prefix.replace("\\", "/")
    folder, _, partial = norm.rpartition("/")
    base = Path(folder or ".")
    if folder and not norm.startswith("/") and not (len(folder) >= 2 and folder[1] == ":"):
        base = Path.cwd() / folder
    try:
        entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return []
    out = []
    for e in entries:
        if e.name in SKIP_DIRS or not e.name.lower().startswith(partial.lower()):
            continue
        if partial == "" and e.name.startswith("."):
            continue
        name = (folder + "/" if folder else "") + e.name
        out.append(name + "/" if e.is_dir() else name)
    return out


class _ProjectFiles:
    """The project's file list (gitignore respected), read once per session."""

    def __init__(self) -> None:
        self._files: list[str] | None = None

    def get(self) -> list[str]:
        if self._files is None:
            from .search import candidate_files

            base = Path.cwd()
            files = []
            try:
                for p in candidate_files(base)[:20_000]:
                    try:
                        files.append(p.relative_to(base).as_posix())
                    except ValueError:
                        continue
            except Exception:
                pass
            self._files = files
        return self._files


def path_matches(prefix: str, project: list[str] | None = None) -> list[str]:
    """Completions for a path: the folder listing first, then project files whose name contains it."""
    found = _dir_entries(prefix)
    if project and prefix and "/" not in prefix.replace("\\", "/"):
        low = prefix.lower()
        seen = set(found)
        extra = [f for f in project if low in f.rsplit("/", 1)[-1].lower() and f not in seen]
        extra.sort(key=lambda f: (not f.rsplit("/", 1)[-1].lower().startswith(low), len(f)))
        found += extra[:MAX_PROJECT_MATCHES]
    return found


def command_matches(text: str, skill_names: list[str] | None = None) -> list[tuple[str, str]]:
    """(name, description) for slash commands starting with ``text``."""
    out = [(c.name, c.help()) for c in commands.COMMANDS if c.name.startswith(text)]
    known = {name for name, _ in out}
    for name in skill_names or []:
        slash = "/" + name
        if slash.startswith(text) and slash not in known and slash not in commands.BY_NAME:
            out.append((slash, t("skill_meta")))
    return out


def _make_completer(agent: Agent):
    from prompt_toolkit.completion import Completer, Completion

    project = _ProjectFiles()

    def skill_names() -> list[str]:
        try:
            from .skills import discover_skills

            return [s.name for s in discover_skills() if getattr(s, "user_invocable", False)]
        except Exception:
            return []

    class KeelsonCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith("/") and " " not in text and "\n" not in text:
                for name, meta in command_matches(text, skill_names()):
                    yield Completion(name, start_position=-len(text), display_meta=meta)
                return
            word = document.get_word_before_cursor(WORD=True)
            if word.startswith("@"):
                for path in path_matches(word[1:], project.get()):
                    yield Completion("@" + path, start_position=-len(word), display=path)
                return
            if text.startswith("/drop "):
                for name in agent.context_files:
                    if name.startswith(word):
                        yield Completion(name, start_position=-len(word))
                return
            if text.startswith(PATH_COMMANDS):
                for path in path_matches(word, project.get()):
                    yield Completion(path, start_position=-len(word))

    return KeelsonCompleter()


# ── the input line ────────────────────────────────────────────


class TerminalInput:
    """Reads one message at a time. ``read()`` returns None at end of input (Ctrl+D / Ctrl+Z / EOF)."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.session = None
        self._ask_session = None
        if simple_input_forced() or not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        try:
            self.session = self._build()
        except Exception:
            self.session = None

    @property
    def rich(self) -> bool:
        return self.session is not None

    def _build(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        kb = KeyBindings()

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("s-tab")
        def _cycle_mode(event):
            from prompt_toolkit.application import run_in_terminal

            # Entering or leaving plan mode prints a line; run_in_terminal keeps the prompt intact.
            run_in_terminal(self.agent.next_permission_mode)
            event.app.invalidate()

        history_file = paths.user_home() / "history.txt"
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(history_file))
        except OSError:
            history = None

        def toolbar():
            try:
                line = status_line(self.agent)
            except Exception:
                line = self.agent.model
            return HTML("<b>{}</b>  <i>{}</i>").format(line, "Shift+Tab " + t("toolbar_mode"))

        def continuation(width, line_number, wrap_count):
            return "." * (width - 1) + " "

        style = Style.from_dict({"bottom-toolbar": "noreverse #8a8a8a bg:default", "prompt": "bold #d97757"})
        return PromptSession(
            message=HTML("<prompt>keelson</prompt> ❯ "),
            history=history,
            completer=_make_completer(self.agent),
            complete_while_typing=False,
            key_bindings=kb,
            bottom_toolbar=toolbar,
            prompt_continuation=continuation,
            style=style,
            multiline=False,
            enable_history_search=False,
            mouse_support=False,
        )

    async def read(self) -> str | None:
        """One message. KeyboardInterrupt propagates (Ctrl+C at the prompt)."""
        if self.session is None:
            from .ui import print_user_prompt

            print_user_prompt()
            try:
                return input()
            except EOFError:
                return None
        try:
            return await self.session.prompt_async()
        except EOFError:
            return None

    async def ask(self, prompt: str) -> str:
        """A short answer (confirmation prompts). EOFError propagates."""
        if self.session is None:
            return input(prompt)
        from prompt_toolkit import PromptSession

        if self._ask_session is None:
            self._ask_session = PromptSession()
        try:
            return await self._ask_session.prompt_async(prompt)
        except KeyboardInterrupt:
            return "n"

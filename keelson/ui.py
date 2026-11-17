"""Terminal front end.

Two halves:

* ``print_*`` / ``start_spinner`` — the facade the runtime calls. Runtime-facing
  functions do not draw anything themselves; they publish a typed event on the
  bus (``keelson/events.py``).
* The terminal renderer — one bus subscriber that turns those events into Rich
  output. The web console and the trace recorder are other subscribers of the
  same stream, so every front end sees an identical run.

REPL-only chrome (welcome banner, prompt, plan approval, tables) has no meaning
outside a terminal and is drawn directly.
"""

from __future__ import annotations

import os
import sys
import threading
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment, Segments
from rich.table import Table
from rich.text import Text

from . import events
from .i18n import t


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


# Benchmark / CI runs set KEELSON_QUIET=1 to silence the spinner and the
# per-tool lines (hundreds of lines per run, and irrelevant to results).
_QUIET = _env_on("KEELSON_QUIET")
# KEELSON_VERBOSE=1 prints each tool's (truncated) output under its status line.
_VERBOSE = _env_on("KEELSON_VERBOSE")

console = Console(highlight=False)

# ─── Palette ───────────────────────────────────────────────
# One place for colour so the terminal and the web console stay recognisably
# the same product (the web tokens in static/css/tokens.css mirror these).

BRAND = "#f6c177"
BRAND_DIM = "#d19a66"
ACCENT = "cyan"
OK = "green"
WARN = "yellow"
BAD = "red"
MUTED = "dim"

# Rough public list price used for the on-screen estimate only ($/1M tokens).
_PRICE_IN_PER_M = 3.0
_PRICE_OUT_PER_M = 15.0

_MAX_EVENT_PREVIEW = 6000   # chars of a tool result carried on the event
_MAX_PANEL_PREVIEW = 500    # chars shown in the terminal in verbose mode


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * _PRICE_IN_PER_M + (output_tokens / 1_000_000) * _PRICE_OUT_PER_M


def _safe_text(value: object) -> str:
    return str(value).encode("utf-8", errors="replace").decode("utf-8")


def _safe_stdout_write(text: object) -> None:
    msg = _safe_text(text)
    try:
        sys.stdout.write(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(msg.encode(enc, errors="replace").decode(enc, errors="replace"))
    sys.stdout.flush()


def _fmt_count(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "▰" * filled + "▱" * (width - filled)


# ─── Runtime facade: publish, don't draw ────────────────────


def print_assistant_text(text: str) -> None:
    events.emit(events.ASSISTANT_DELTA, text=_safe_text(text))


def print_tool_call(name: str, inp: dict, call_id: str | None = None) -> None:
    events.emit(
        events.TOOL_CALL,
        id=call_id,
        name=name,
        input=inp,
        summary=_safe_text(_get_tool_summary(name, inp)),
    )


def tool_result_ok(result: str) -> bool:
    head = result.lstrip()[:40]
    failure_prefixes = ("Error", "Action denied", "User denied", "Loop guard", "Command failed", "Command timed out")
    return not head.startswith(failure_prefixes)


def print_tool_result(name: str, result: str, call_id: str | None = None) -> None:
    result = _safe_text(result)
    events.emit(
        events.TOOL_RESULT,
        id=call_id,
        name=name,
        ok=tool_result_ok(result),
        preview=result[:_MAX_EVENT_PREVIEW],
        chars=len(result),
    )


def print_error(msg: str) -> None:
    events.emit(events.NOTICE, level="error", message=_safe_text(msg))


def print_info(msg: str) -> None:
    events.emit(events.NOTICE, level="info", message=_safe_text(msg))


def print_warning(msg: str) -> None:
    events.emit(events.NOTICE, level="warning", message=_safe_text(msg))


def print_confirmation(command: str, reason: str = "", options: list[str] | None = None) -> None:
    events.emit(events.CONFIRM_PROMPT, message=_safe_text(command), reason=_safe_text(reason),
                options=list(options or ["once", "deny"]))


def print_divider() -> None:
    events.emit(events.TURN_END)


def print_cost(input_tokens: int, output_tokens: int, cost_usd: float | None = None) -> None:
    """cost_usd: the agent's estimate from the model's own price; a generic guess if not given."""
    events.emit(
        events.USAGE,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(estimate_cost_usd(input_tokens, output_tokens) if cost_usd is None else cost_usd, 6),
    )


def print_retry(attempt: int, max_retries: int, reason: str) -> None:
    events.emit(events.RETRY, attempt=attempt, max_retries=max_retries, reason=_safe_text(reason))


def print_sub_agent_start(agent_type: str, description: str) -> None:
    events.emit(events.SUBAGENT, phase="start", agent_type=agent_type, description=_safe_text(description))


def print_sub_agent_end(agent_type: str, description: str) -> None:
    events.emit(events.SUBAGENT, phase="end", agent_type=agent_type, description=_safe_text(description))


def start_spinner(label: str = "Thinking") -> None:
    events.emit(events.THINKING, active=True, label=label)


def stop_spinner() -> None:
    events.emit(events.THINKING, active=False)


# ─── Streaming Markdown ─────────────────────────────────────


class _MarkdownStream:
    """The model's answer rendered as Markdown while it streams.

    While text arrives, a live area shows the last screenful of the rendered
    answer; when the answer ends (a tool call, the end of the turn, anything
    else drawn), the live area is removed and the whole answer is printed once,
    so the scrollback holds it exactly once. Without a real terminal (pipes,
    tests, ``KEELSON_MARKDOWN=0``) the raw text is written as it comes.
    """

    MIN_INTERVAL = 0.08

    def __init__(self) -> None:
        self.buf = ""
        self.live: Live | None = None
        self.last = 0.0

    @staticmethod
    def enabled() -> bool:
        return console.is_terminal and os.environ.get("KEELSON_MARKDOWN", "1").strip() not in ("0", "false", "off")

    @property
    def active(self) -> bool:
        return bool(self.buf)

    def feed(self, text: str) -> None:
        if not self.enabled():
            _safe_stdout_write(text)
            return
        if not self.buf:
            text = text.lstrip("\n")
            if not text:
                return
        self.buf += text
        if self.live is None:
            self.live = Live(console=console, auto_refresh=False, transient=True, vertical_overflow="crop")
            self.live.start()
        now = time.monotonic()
        if now - self.last >= self.MIN_INTERVAL:
            self.last = now
            self._refresh()

    def _refresh(self) -> None:
        if self.live is None:
            return
        lines = console.render_lines(Markdown(self.buf), console.options, pad=False)
        keep = max(3, console.size.height - 3)
        segments: list[Segment] = []
        for line in lines[-keep:]:
            segments.extend(line)
            segments.append(Segment.line())
        self.live.update(Segments(segments), refresh=True)

    def finish(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
        if self.buf:
            if self.enabled():
                console.print(Markdown(self.buf))
            self.buf = ""


_markdown = _MarkdownStream()


# ─── Terminal renderer: one bus subscriber ──────────────────


class TerminalRenderer:
    """Turns runtime events into Rich output. Holds only display state."""

    def __init__(self) -> None:
        self._call_started: dict[str, float] = {}
        self._last_call_key: str | None = None
        self._turn_started: float | None = None
        self._usage: dict = {}
        self._handlers = {
            events.USER_MESSAGE: self._on_user_message,
            events.ASSISTANT_DELTA: self._on_assistant_delta,
            events.THINKING: self._on_thinking,
            events.TOOL_CALL: self._on_tool_call,
            events.TOOL_RESULT: self._on_tool_result,
            events.PERMISSION: self._on_permission,
            events.CONFIRM_PROMPT: self._on_confirm_prompt,
            events.LOOP_GUARD: self._on_loop_guard,
            events.VERIFY: self._on_verify,
            events.SCOPE: self._on_scope,
            events.TASK: self._on_task,
            events.CONTEXT_FOLD: self._on_context_fold,
            events.USAGE: self._on_usage,
            events.SKILL_EVOLUTION: self._on_skill_evolution,
            events.SUBAGENT: self._on_subagent,
            events.RETRY: self._on_retry,
            events.NOTICE: self._on_notice,
            events.TURN_END: self._on_turn_end,
        }

    def __call__(self, event: events.Event) -> None:
        handler = self._handlers.get(event.type)
        if handler is None:
            return
        if event.type != events.ASSISTANT_DELTA and _markdown.active and event.type not in (
                events.USAGE, events.SPAN, events.TOOL_OUTPUT):
            _markdown.finish()          # anything else drawn ends the streamed answer first
        handler(event.data)

    # -- conversation ------------------------------------------------------

    def _on_user_message(self, data: dict) -> None:
        self._turn_started = time.time()

    def _on_assistant_delta(self, data: dict) -> None:
        _markdown.feed(data.get("text", ""))

    def _on_thinking(self, data: dict) -> None:
        if _QUIET:
            return
        if data.get("active"):
            label = data.get("label") or "Thinking"
            _spinner.start(t("thinking") if label == "Thinking" else label)
        else:
            _spinner.stop()

    # -- tools -------------------------------------------------------------

    def _on_tool_call(self, data: dict) -> None:
        if _QUIET:
            return
        key = data.get("id") or data.get("name", "")
        self._call_started[key] = time.time()
        self._last_call_key = key
        line = Text("  ")
        line.append("● ", style=WARN)
        line.append(data.get("name", ""), style="bold")
        summary = data.get("summary") or ""
        if summary:
            line.append("  " + summary, style=MUTED)
        console.print()
        console.print(line)

    def _on_tool_result(self, data: dict) -> None:
        if _QUIET:
            return
        name = data.get("name", "")
        key = data.get("id") or name
        started = self._call_started.pop(key, None)
        elapsed = f"{time.time() - started:.2f}s · " if started else ""
        preview = data.get("preview", "")
        ok = bool(data.get("ok", True))

        # Calls that ran concurrently report back in any order; name the tool
        # whenever the result is not sitting directly under its own call line.
        label = "" if key == self._last_call_key else f"{name} · "
        self._last_call_key = None
        line = Text("    ")
        if ok:
            line.append("✓ ", style=OK)
            line.append(f"{label}{elapsed}{_fmt_count(int(data.get('chars', 0)))} {t('chars')}", style=MUTED)
        else:
            line.append("✗ ", style=BAD)
            first = preview.strip().splitlines()[0] if preview.strip() else "failed"
            line.append(f"{label}{elapsed}{first[:160]}", style=BAD)
        console.print(line)

        if ok and name in ("edit_file", "write_file"):
            self._render_file_change(preview)
        elif _VERBOSE and preview:
            shown = preview[:_MAX_PANEL_PREVIEW]
            if len(preview) > _MAX_PANEL_PREVIEW:
                shown += f"\n… ({data.get('chars', len(preview))} chars total)"
            console.print(Panel(shown, border_style=MUTED, box=box.ROUNDED, padding=(0, 1)))

    @staticmethod
    def _render_file_change(result: str) -> None:
        lines = result.split("\n")
        console.print(Text("      " + lines[0], style=OK))
        max_display = 24
        body = [ln for ln in lines[1:] if ln.strip()]
        for line in body[:max_display]:
            if line.startswith("@@"):
                style = ACCENT
            elif line.startswith("- "):
                style = BAD
            elif line.startswith("+ "):
                style = OK
            else:
                style = MUTED
            console.print(Text("      " + line, style=style))
        if len(body) > max_display:
            console.print(Text("      " + t("more_lines", n=len(body) - max_display), style=MUTED))

    # -- runtime guards ----------------------------------------------------

    def _on_permission(self, data: dict) -> None:
        # Allowed calls are the common case and stay silent; the terminal only
        # speaks up when the gate actually intervened.
        if _QUIET or data.get("action") != "deny":
            return
        console.print(Text(f"    ⛔ {t('denied')} — {data.get('message', '')}", style=BAD))

    def _on_confirm_prompt(self, data: dict) -> None:
        body = Text(data.get("message", ""), style="bold")
        if data.get("reason"):
            body.append("\n" + data["reason"], style="")
        options = data.get("options") or ["once", "deny"]
        keys = {o: t(f"confirm.{o}") for o in ("once", "session", "project", "deny")}
        console.print()
        console.print(Panel(
            body,
            title=f"[bold {WARN}]{t('confirm_title')}[/bold {WARN}]",
            subtitle="[dim]" + " · ".join(keys[o] for o in options if o in keys) + "[/dim]",
            border_style=WARN,
            box=box.ROUNDED,
            padding=(0, 1),
        ))

    def _on_verify(self, data: dict) -> None:
        if data.get("phase") == "running":
            console.print(Text(f"  ⧗ {t('verify.running')}: " + " · ".join(data.get("commands") or []), style=MUTED))
            return
        status = data.get("status")
        label, style = {"passed": (t("verify.passed"), OK), "failed": (t("verify.failed"), BAD)}.get(
            status, (t("verify.unverified"), WARN))
        line = Text(f"  {label}", style=f"bold {style}")
        line.append(f"  {data.get('detail', '')}", style="")
        console.print(line)
        for item in data.get("weakened") or []:
            console.print(Text(f"    ! {t('verify.weakened')} — {item}", style=WARN))
        for claim in data.get("unproven") or []:
            console.print(Text("    ! " + t("verify.unproven", claim=claim), style=WARN))
        out = data.get("out_of_scope") or []
        if out:
            console.print(Text(f"    ! {t('verify.out_of_scope')} — " + ", ".join(out), style=WARN))
        for warning in data.get("warnings") or []:
            console.print(Text(f"    ! {warning}", style=WARN))

    def _on_scope(self, data: dict) -> None:
        if _QUIET:
            return
        reason = data.get("reason") or ""
        console.print(Text(f"  ◇ {t('scope')}: " + ", ".join(data.get("paths") or []) + (f"  — {reason}" if reason else ""),
                           style=ACCENT))

    def _on_task(self, data: dict) -> None:
        if _QUIET:
            return
        marks = {"done": ("✓", OK), "in_progress": ("▸", ACCENT), "blocked": ("!", WARN), "pending": ("○", MUTED)}
        plan = data.get("plan") or []
        if plan:
            console.print(Text("  " + t("plan"), style="bold"))
        for step in plan:
            mark, style = marks.get(step.get("status"), ("○", MUTED))
            console.print(Text(f"    {mark} {step.get('step', '')}", style=style))
        if data.get("decision"):
            console.print(Text(f"    {t('decision')}: {data['decision']}", style=MUTED))

    def _on_loop_guard(self, data: dict) -> None:
        if _QUIET:
            return
        console.print(Text(f"    ⟳ loop guard stopped repeated '{data.get('name', '')}' calls", style=WARN))

    def _on_context_fold(self, data: dict) -> None:
        if _QUIET:
            return
        console.print(Text("\n  ⇲ " + t("folded", trigger=data.get("trigger", "manual")), style=ACCENT))
        if data.get("kept"):
            console.print(Text("    " + t("folded_kept", kept=data["kept"]), style=MUTED))

    def _on_skill_evolution(self, data: dict) -> None:
        action = data.get("action", "")
        style = BAD if "quarantin" in action else OK
        console.print(Text(f"\n  ⬢ skill {action}: {data.get('skill', '')}", style=style))

    def _on_subagent(self, data: dict) -> None:
        if _QUIET:
            return
        if data.get("phase") == "start":
            console.print(Text(f"\n  ▸ sub-agent [{data.get('agent_type', '')}] {data.get('description', '')}", style="magenta"))
        else:
            console.print(Text(f"  ◂ sub-agent [{data.get('agent_type', '')}] finished", style="magenta"))

    def _on_retry(self, data: dict) -> None:
        console.print(Text(
            f"\n  ↻ retry {data.get('attempt')}/{data.get('max_retries')}: {data.get('reason', '')}", style=WARN
        ))

    def _on_notice(self, data: dict) -> None:
        level = data.get("level", "info")
        title, style = {
            "error": ("Error", BAD),
            "warning": ("Notice", WARN),
        }.get(level, ("Info", ACCENT))
        console.print(Panel(
            data.get("message", ""),
            title=f"[bold {style}]{title}[/bold {style}]",
            border_style=style,
            box=box.ROUNDED,
            padding=(0, 1),
        ))

    # -- turn footer -------------------------------------------------------

    def _on_usage(self, data: dict) -> None:
        self._usage.update(data)

    def _on_turn_end(self, data: dict) -> None:
        if _QUIET:
            return
        parts: list[str] = []
        usage = self._usage
        if usage.get("input_tokens") is not None:
            tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            parts.append(t("turn.tokens", tin=_fmt_count(tin), tout=_fmt_count(tout)))
            cost = usage.get("cost_usd")
            parts.append(t("turn.cost", cost=float(cost) if cost is not None else estimate_cost_usd(tin, tout)))
            hit = usage.get("cache_hit_tokens")
            if hit is not None and tin > 0:
                parts.append(t("turn.cache", rate=int(hit) / tin))
        if self._turn_started is not None:
            parts.append(f"{time.time() - self._turn_started:.1f}s")
            self._turn_started = None
        window = int(usage.get("context_window") or 0)
        if window > 0:
            frac = min(1.0, int(usage.get("context_tokens") or 0) / window)
            parts.append(f"{t('turn.ctx')} {_bar(frac)} {frac * 100:.0f}%")
        label = "  ·  ".join(parts) if parts else t("turn.done")
        console.print()
        console.rule(f"[dim]{label}[/dim]", style=MUTED)


class _Spinner:
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, label: str) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def _run() -> None:
            frame = 0
            _safe_stdout_write(f"\n  {self.FRAMES[0]} {label}...")
            while not self._stop.is_set():
                time.sleep(0.08)
                frame = (frame + 1) % len(self.FRAMES)
                _safe_stdout_write(f"\r  {self.FRAMES[frame]} {label}...")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1)
        self._thread = None
        _safe_stdout_write("\r\033[K")


_spinner = _Spinner()
_renderer = TerminalRenderer()
_unsubscribe = events.bus.subscribe(_renderer)


def attach_terminal(bus: events.EventBus):
    """Render another bus (a web session's, say) in this terminal as well."""
    return bus.subscribe(_renderer)


def set_terminal_enabled(enabled: bool) -> None:
    """Attach or detach the terminal renderer (the web server runs headless)."""
    global _unsubscribe
    if enabled and _unsubscribe is None:
        _unsubscribe = events.bus.subscribe(_renderer)
    elif not enabled and _unsubscribe is not None:
        _spinner.stop()
        _unsubscribe()
        _unsubscribe = None


# ─── REPL chrome (terminal only, drawn directly) ────────────


def print_diff(text: str) -> None:
    """A unified diff, coloured: file headers, hunks, additions, removals."""
    for line in text.splitlines():
        if line.startswith(("diff --git", "+++", "---", "index ")):
            style = "bold"
        elif line.startswith("@@"):
            style = ACCENT
        elif line.startswith("+"):
            style = OK
        elif line.startswith("-"):
            style = BAD
        else:
            style = MUTED
        console.print(Text(line, style=style), soft_wrap=True)


def print_review(outcome: dict, files: list[str]) -> None:
    """/review: the last turn's verification badge and flags, then the files it changed."""
    console.print()
    _renderer._on_verify({"phase": "done", **outcome})
    if files:
        console.print(Text(f"  {t('review.files')} ({len(files)})", style="bold"))
        for f in files[:40]:
            console.print(Text(f"    {f}", style=MUTED))
        if len(files) > 40:
            console.print(Text("    " + t("more_lines", n=len(files) - 40), style=MUTED))
    console.print(Text("  " + t("review.hint"), style=MUTED))


def print_rows(rows: list[tuple[str, str]]) -> None:
    """Two aligned columns (label, value): /status and the like."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column()
    for label, value in rows:
        table.add_row(label, value)
    console.print()
    console.print(table)


_WELCOME_HINT_RUNS = 5


def _welcome_hint_due() -> bool:
    """The key hints show for the first few sessions, then stay out of the way."""
    import json

    from . import paths

    f = paths.user_home() / "ui.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        data = {}
    runs = int(data.get("welcome_runs", 0))
    if runs >= _WELCOME_HINT_RUNS:
        return False
    data["welcome_runs"] = runs + 1
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return True


def print_welcome(
    *,
    model: str | None = None,
    permission_mode: str | None = None,
    cwd: str | None = None,
    skill_count: int | None = None,
    checks: list[str] | None = None,
    profile: str | None = None,
) -> None:
    title = Text("Keelson", style=f"bold {BRAND}")
    title.append("  " + t("tagline"), style=MUTED)

    status = Table.grid(padding=(0, 2))
    status.add_column(style=MUTED, no_wrap=True)
    status.add_column()
    if model:
        status.add_row(t("model"), Text(model + (f"  ({profile})" if profile else ""), style="bold"))
    if permission_mode:
        status.add_row(t("mode"), Text(t(f"mode.{permission_mode}"), style="bold"))
    if cwd:
        status.add_row(t("workspace"), Text(cwd, style="bold"))
    if checks is not None:
        status.add_row(t("checks"), Text(" · ".join(checks) if checks else t("checks_none"),
                                         style="bold" if checks else WARN))

    body = Table.grid()
    body.add_row(title)
    if status.row_count:
        body.add_row("")
        body.add_row(status)
    if _welcome_hint_due():
        body.add_row("")
        body.add_row(Text(t("hint_keys"), style=ACCENT))
        body.add_row(Text(t("hint_first"), style=MUTED))

    console.print()
    console.print(Panel(body, subtitle=f"[dim]{t('type_request')}[/dim]", border_style=BRAND_DIM,
                        box=box.ROUNDED, padding=(1, 2)))


def print_user_prompt() -> None:
    console.print(f"\n[bold {BRAND}]keelson[/bold {BRAND}] [dim]❯[/dim] ", end="")


def print_goodbye() -> None:
    console.print(Text("\n  " + t("goodbye"), style=BRAND))


def print_interrupted() -> None:
    print_warning(t("interrupted"))


def print_plan_for_approval(plan_content: str) -> None:
    lines = plan_content.split("\n")
    max_lines = 60
    preview = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        preview += f"\n\n... ({len(lines) - max_lines} more lines)"
    console.print(Panel(
        _safe_text(preview),
        title=f"[bold {ACCENT}]Plan for approval[/bold {ACCENT}]",
        border_style=ACCENT,
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def print_plan_approval_options() -> None:
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("choice", style=f"bold {WARN}", no_wrap=True)
    table.add_column("action", style="white")
    table.add_column("detail", style=MUTED)
    table.add_row("1", "Clear context and execute", "fresh start with auto-accept edits")
    table.add_row("2", "Execute", "keep context, auto-accept edits")
    table.add_row("3", "Manually approve edits", "keep context, confirm each edit")
    table.add_row("4", "Keep planning", "provide feedback to revise")
    console.print(Panel(table, title=f"[bold {WARN}]Choose an option[/bold {WARN}]", border_style=WARN, box=box.ROUNDED))


def print_memory_entries(memories: list[object]) -> None:
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACCENT}")
    table.add_column("Type", style=f"bold {BRAND}", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Description", style=MUTED)
    for m in memories:
        table.add_row(
            _safe_text(getattr(m, "type", "")),
            _safe_text(getattr(m, "name", "")),
            _safe_text(getattr(m, "description", "")),
        )
    console.print(table)


def print_skill_entries(skills: list[object]) -> None:
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACCENT}")
    table.add_column("Skill", style=f"bold {BRAND}", no_wrap=True)
    table.add_column("Source", style=ACCENT, no_wrap=True)
    table.add_column("Mode", style="magenta", no_wrap=True)
    table.add_column("Description", style="white")
    for s in skills:
        name = getattr(s, "name", "")
        tag = f"/{name}" if getattr(s, "user_invocable", False) else name
        table.add_row(
            _safe_text(tag),
            _safe_text(getattr(s, "source", "")),
            _safe_text(getattr(s, "context", "")),
            _safe_text(getattr(s, "description", "")),
        )
    console.print(table)


# ─── Tool summaries ─────────────────────────────────────────


def _get_tool_summary(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    if name in ("read_file", "write_file", "edit_file"):
        return inp.get("file_path", "")
    if name == "delete_files":
        targets = inp.get("paths") or []
        return ", ".join(map(str, targets if isinstance(targets, list) else [targets]))[:80]
    if name == "list_files":
        return inp.get("pattern", "")
    if name == "grep_search":
        return f'"{inp.get("pattern", "")}" in {inp.get("path", ".")}'
    if name == "run_shell":
        cmd = inp.get("command", "")
        return cmd[:80] + "…" if len(cmd) > 80 else cmd
    if name in ("skill", "skill_evolve"):
        return inp.get("skill_name", "")
    if name == "skill_create":
        return inp.get("name", "")
    if name == "agent":
        return f'[{inp.get("type", "general")}] {inp.get("description", "")}'
    return ""

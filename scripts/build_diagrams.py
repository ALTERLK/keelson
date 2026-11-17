"""Generate the architecture diagrams under docs/assets/diagrams/.

The diagrams are code, not drawings: every figure is laid out on the same grid
with the same node, edge and colour rules, in English and Chinese from one
description, and each SVG carries both a light and a dark palette (it follows
the reader's colour scheme, so it reads on GitHub in either theme).

    python scripts/build_diagrams.py            # rewrite all figures
    python scripts/build_diagrams.py --check    # fail if the committed files are stale (CI)

To change a figure, edit its function below and re-run. No drawing tool needed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "assets" / "diagrams"
WIDTH = 1040

LIGHT = {
    "canvas": "#ffffff", "frame": "#d8dee4", "panel": "#f6f8fa", "line": "#d0d7de", "group": "#afb8c1",
    "text": "#1f2328", "muted": "#59636e", "edge": "#6e7781",
    "brand": "#9a6700", "brand-bg": "#fff8e6", "blue": "#0969da", "blue-bg": "#eef6ff",
    "ok": "#1a7f37", "ok-bg": "#eefbf1", "bad": "#cf222e", "bad-bg": "#fff1f0",
}
DARK = {
    "canvas": "#0d1117", "frame": "#30363d", "panel": "#161b22", "line": "#30363d", "group": "#484f58",
    "text": "#e6edf3", "muted": "#9198a1", "edge": "#7d8590",
    "brand": "#f6c177", "brand-bg": "#2a2113", "blue": "#58a6ff", "blue-bg": "#0f1e33",
    "ok": "#3fb950", "ok-bg": "#0f2417", "bad": "#f85149", "bad-bg": "#2d1214",
}
KINDS = ("plain", "brand", "blue", "ok", "bad")

SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif'
MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'


def _vars(palette: dict[str, str]) -> str:
    return "".join(f"--{name}:{value};" for name, value in palette.items())


def _style(force_dark: bool) -> str:
    base = DARK if force_dark else LIGHT
    dark = "" if force_dark else f"@media (prefers-color-scheme: dark){{svg{{{_vars(DARK)}}}}}"
    kinds = "".join(
        f".edge.{k}{{stroke:var(--{k})}}.head.{k}{{fill:var(--{k})}}.label.{k}{{fill:var(--{k})}}"
        f".node.{k} .bar{{fill:var(--{k})}}.node.{k} .box{{fill:var(--{k}-bg);stroke:var(--{k})}}"
        for k in KINDS if k != "plain"
    )
    return (
        f"svg{{{_vars(base)}font-family:{SANS}}}{dark}"
        ".canvas{fill:var(--canvas);stroke:var(--frame)}"
        ".h1{font-size:19px;font-weight:650;fill:var(--text)}.h2{font-size:12.5px;fill:var(--muted)}"
        ".group{fill:none;stroke:var(--group);stroke-dasharray:4 4}"
        ".group-label{font-size:10.5px;font-weight:600;letter-spacing:.08em;fill:var(--muted)}"
        ".box{fill:var(--panel);stroke:var(--line)}.bar{fill:var(--group)}"
        ".title{font-size:13.5px;font-weight:620;fill:var(--text)}.sub{font-size:11.5px;fill:var(--muted)}"
        f".code{{font-size:10.5px;font-family:{MONO};fill:var(--muted)}}"
        ".edge{fill:none;stroke:var(--edge);stroke-width:1.5}.edge.dashed{stroke-dasharray:5 4}"
        ".head{fill:var(--edge)}.label{font-size:11px;fill:var(--muted)}.label-bg{fill:var(--canvas)}"
        ".note{font-size:12px;fill:var(--muted)}.note-strong{font-size:12.5px;font-weight:620;fill:var(--text)}"
        ".track{fill:var(--panel);stroke:var(--line)}.fill-1{fill:var(--blue);opacity:.55}.fill-2{fill:var(--brand);opacity:.7}"
        ".fill-3{fill:var(--bad);opacity:.6}.tick{stroke:var(--text);stroke-width:1.5}"
        + kinds
    )


def text_width(text: str, size: float) -> float:
    """Rough rendered width: wide for CJK, narrow for Latin. Enough to size a label background."""
    return sum(size if ord(ch) > 0x2E80 else size * 0.56 for ch in text)


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def left(self, t: float = 0.5) -> tuple[float, float]:
        return (self.x, self.y + self.h * t)

    def right(self, t: float = 0.5) -> tuple[float, float]:
        return (self.x + self.w, self.y + self.h * t)

    def top(self, t: float = 0.5) -> tuple[float, float]:
        return (self.x + self.w * t, self.y)

    def bottom(self, t: float = 0.5) -> tuple[float, float]:
        return (self.x + self.w * t, self.y + self.h)


class Canvas:
    def __init__(self, height: int, title: str, subtitle: str) -> None:
        self.height = height
        self.title = title
        self.subtitle = subtitle
        self.below: list[str] = []   # groups: drawn first
        self.edges: list[str] = []
        self.nodes: list[str] = []
        self.labels: list[str] = []  # edge labels: drawn last, over everything

    # -- shapes -----------------------------------------------------------

    def group(self, x: float, y: float, w: float, h: float, label: str) -> Box:
        self.below.append(f'<rect class="group" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>')
        self.below.append(f'<text class="group-label" x="{x + 14}" y="{y + 20}">{escape(label.upper())}</text>')
        return Box(x, y, w, h)

    def node(self, x: float, y: float, w: float, h: float, title: str, sub: str | list[str] = "", code: str = "",
             kind: str = "plain", center: bool = False) -> Box:
        lines = [sub] if isinstance(sub, str) and sub else list(sub or [])
        tx, anchor = (x + w / 2, ' text-anchor="middle"') if center else (x + 16, "")
        parts = [f'<g class="node {kind}">', f'<rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="9"/>']
        if not center:
            parts.append(f'<rect class="bar" x="{x}" y="{y + 9}" width="3.5" height="{h - 18}" rx="1.75"/>')
        content = 22 + 15.5 * len(lines) + (15 if code else 0)
        ty = y + (h - content) / 2 + 17
        parts.append(f'<text class="title" x="{tx}" y="{ty:.1f}"{anchor}>{escape(title)}</text>')
        for i, line in enumerate(lines):
            parts.append(f'<text class="sub" x="{tx}" y="{ty + 18 + 15.5 * i:.1f}"{anchor}>{escape(line)}</text>')
        if code:
            parts.append(f'<text class="code" x="{tx}" y="{ty + 19 + 15.5 * len(lines):.1f}"{anchor}>{escape(code)}</text>')
        parts.append("</g>")
        self.nodes.append("".join(parts))
        return Box(x, y, w, h)

    def diamond(self, cx: float, cy: float, w: float, h: float, text: str) -> Box:
        points = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        self.nodes.append(f'<g class="node brand"><polygon class="box" points="{points}"/>'
                          f'<text class="title" x="{cx}" y="{cy + 4.5}" text-anchor="middle">{escape(text)}</text></g>')
        return Box(cx - w / 2, cy - h / 2, w, h)

    def text(self, x: float, y: float, value: str, cls: str = "note", anchor: str = "start") -> None:
        self.nodes.append(f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{escape(value)}</text>')

    def raw(self, svg: str) -> None:
        self.nodes.append(svg)

    # -- edges ------------------------------------------------------------

    def edge(self, *points: tuple[float, float], label: str = "", kind: str = "plain", dashed: bool = False,
             at: float = 0.5, both: bool = False, dy: float = 0, dx: float = 0) -> None:
        """An arrow through ``points`` (axis-aligned segments, rounded corners)."""
        radius = 8
        d = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
        for i in range(1, len(points)):
            x, y = points[i]
            if i < len(points) - 1:
                px, py = points[i - 1]
                nx, ny = points[i + 1]
                r_in = min(radius, (abs(x - px) + abs(y - py)) / 2)
                r_out = min(radius, (abs(nx - x) + abs(ny - y)) / 2)
                ax = x - r_in * ((x > px) - (x < px))
                ay = y - r_in * ((y > py) - (y < py))
                bx = x + r_out * ((nx > x) - (nx < x))
                by = y + r_out * ((ny > y) - (ny < y))
                d.append(f"L{ax:.1f},{ay:.1f} Q{x:.1f},{y:.1f} {bx:.1f},{by:.1f}")
            else:
                d.append(f"L{x:.1f},{y:.1f}")
        cls = f"edge {kind}" + (" dashed" if dashed else "")
        start = f' marker-start="url(#tail-{kind})"' if both else ""
        self.edges.append(f'<path class="{cls}" d="{" ".join(d)}" marker-end="url(#head-{kind})"{start}/>')
        if label:
            lx, ly = self._along(points, at)
            width = text_width(label, 11) + 10
            self.labels.append(
                f'<rect class="label-bg" x="{lx + dx - width / 2:.1f}" y="{ly + dy - 9:.1f}" width="{width:.1f}" height="17" rx="4"/>'
                f'<text class="label {kind}" x="{lx + dx:.1f}" y="{ly + dy + 3.5:.1f}" text-anchor="middle">{escape(label)}</text>')

    @staticmethod
    def _along(points: tuple[tuple[float, float], ...], t: float) -> tuple[float, float]:
        lengths = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:], strict=False)]
        target = sum(lengths) * t
        for (a, b), length in zip(zip(points, points[1:], strict=False), lengths, strict=True):
            if target <= length and length:
                f = target / length
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            target -= length
        return points[-1]

    # -- output -----------------------------------------------------------

    def render(self, *, force_dark: bool = False) -> str:
        markers = "".join(
            f'<marker id="head-{k}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path class="head {k}" d="M0,0.8 L9,5 L0,9.2 z"/></marker>'
            f'<marker id="tail-{k}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path class="head {k}" d="M0,0.8 L9,5 L0,9.2 z"/></marker>'
            for k in KINDS
        )
        body = "\n".join([*self.below, *self.edges, *self.nodes, *self.labels])
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {self.height}" width="{WIDTH}" height="{self.height}" '
            f'role="img" aria-label="{escape(self.title)}">\n'
            f"<title>{escape(self.title)}</title>\n<style>{_style(force_dark)}</style>\n<defs>{markers}</defs>\n"
            f'<rect class="canvas" x="0.5" y="0.5" width="{WIDTH - 1}" height="{self.height - 1}" rx="14"/>\n'
            f'<text class="h1" x="30" y="42">{escape(self.title)}</text>\n'
            f'<text class="h2" x="30" y="63">{escape(self.subtitle)}</text>\n'
            f"{body}\n</svg>\n"
        )


def pick(lang: str, en, zh):
    return zh if lang == "zh" else en


# ─── 1. system overview ─────────────────────────────────────


def overview(lang: str) -> Canvas:
    t = lambda en, zh: pick(lang, en, zh)  # noqa: E731
    c = Canvas(700, t("Keelson · system overview", "Keelson · 系统总览"),
               t("The model proposes; the runtime decides, executes and records. Every front end watches the same event stream.",
                 "模型只提议；运行时负责裁决、执行和记录。所有前端看的是同一条事件流。"))

    fronts = c.group(30, 88, 222, 432, t("Front ends", "前端"))
    terminal = c.node(44, 122, 194, 78, t("Terminal REPL", "终端 REPL"), t("Rich rendering, slash commands", "Rich 渲染，斜杠命令"), "keelson/main.py · ui.py", "blue")
    c.node(44, 214, 194, 78, t("Web console", "Web 控制台"), t("sessions, live tool output", "多会话，工具实时输出"), "keelson/web/ · SSE", "blue")
    replay = c.node(44, 306, 194, 78, t("Replay", "回放"), t("recorded runs, no model", "播放录制的运行，不调模型"), "keelson web --replay", "blue")
    c.node(44, 398, 194, 78, t("Hosted demo", "在线演示"), t("static export of the console", "控制台的静态导出"), "GitHub Pages", "blue")

    c.group(282, 88, 476, 432, t("Runtime", "运行时"))
    loop = c.node(298, 122, 444, 74, t("Agent loop", "Agent 循环"),
                  t("call the model → gate each tool call → run it → feed the result back",
                    "调用模型 → 逐个裁决工具调用 → 执行 → 把结果写回"), "keelson/agent.py", "brand")
    xs = (298, 451, 604)
    c.node(xs[0], 210, 138, 88, t("Context manager", "上下文管理"), t(["budget · prune", "fold at 70%"], ["预算 · 修剪", "70% 时折叠"]), "session_memory.py")
    c.node(xs[1], 210, 138, 88, t("Loop guard", "循环保护"), t(["stops a tool that", "repeats for nothing"], ["叫停无进展的", "重复调用"]), "agent.py")
    gate = c.node(xs[2], 210, 138, 88, t("Permission gate", "权限门"), t(["allow · confirm", "· deny"], ["放行 · 询问", "· 拒绝"]), "tools.py", "ok")
    c.node(xs[0], 312, 138, 88, t("Skills", "技能"), t(["retrieved per turn,", "updates are gated"], ["按轮检索，", "更新要过门禁"]), "skills.py")
    c.node(xs[1], 312, 138, 88, t("Memory", "长期记忆"), t(["facts per project,", "across sessions"], ["按项目保存，", "跨会话有效"]), "memory.py")
    tools = c.node(xs[2], 312, 138, 88, t("Tool dispatch", "工具分发"), t(["built-in · skill", "MCP · sub-agent"], ["内置 · 技能", "MCP · 子 Agent"]), "tools.py")
    bus = c.node(298, 416, 444, 76, t("Event bus", "事件总线"),
                 t("typed, ordered, numbered events · one bus per session", "带类型、有序、带序号的事件 · 每个会话一条总线"),
                 "keelson/events.py", "brand")

    c.group(788, 88, 222, 432, t("Outside", "外部"))
    model = c.node(802, 122, 194, 78, t("Model API", "模型 API"), t("OpenAI- or Anthropic-compatible", "OpenAI / Anthropic 兼容协议"), "streaming")
    work = c.node(802, 214, 194, 84, t("Workspace", "工作目录"), t(["files, and a shell that is", "async, streamed, killable"], ["文件；shell 异步执行、", "实时输出、可终止"]))
    mcp = c.node(802, 318, 194, 78, t("MCP servers", "MCP 服务"), t("external tools, untrusted by default", "外部工具，默认不信任"))

    c.group(30, 548, 980, 122, t("Evidence and state", "证据与状态"))
    ex = (46, 238, 430, 622, 814)
    trace = c.node(ex[0], 580, 180, 72, t("Trace recorder", "轨迹录制"), t("every event → JSONL", "每个事件 → JSONL"), ".keelson/traces/")
    c.node(ex[1], 580, 180, 72, t("Benchmarks", "基准评测"), t("ToolHop · sandbox tasks", "ToolHop · 沙箱任务"), "bench/runs/")
    c.node(ex[2], 580, 180, 72, t("Case study", "实战案例"), t("real issues, upstream tests", "真实 issue，上游测试判分"), "bench/casestudy/")
    c.node(ex[3], 580, 180, 72, t("Live smoke test", "端到端实测"), t("stop · isolation · resume", "停止 · 隔离 · 恢复"), "scripts/smoke_live.py")
    c.node(ex[4], 580, 180, 72, t("State", "状态"), t("skills · memory · sessions", "技能 · 记忆 · 会话"), "~/.keelson/ · .keelson/")

    c.edge((fronts.x + fronts.w, loop.cy), loop.left(), label=t("task", "任务"), kind="blue", dy=-11)
    c.edge(bus.left(), (fronts.x + fronts.w, bus.cy), label=t("events", "事件"), kind="brand", dy=-11)
    c.edge(loop.right(), (model.x, loop.cy), both=True, label=t("stream", "流式"), dy=-11)
    c.edge(gate.right(), (work.x, gate.cy), label=t("allowed", "放行的"), kind="ok", dy=-11)
    c.edge(tools.right(), (mcp.x, tools.cy))
    c.edge(bus.bottom(0.3), (bus.bottom(0.3)[0], 534), (trace.top(0.72)[0], 534), trace.top(0.72), label=t("record", "录制"), kind="brand", at=0.5)
    c.edge(trace.top(0.2), (trace.top(0.2)[0], 482), label=t("replay", "回放"), dashed=True, at=0.5, dx=26)
    del terminal, replay
    return c


# ─── 2. one turn ────────────────────────────────────────────


def turn(lang: str) -> Canvas:
    t = lambda en, zh: pick(lang, en, zh)  # noqa: E731
    c = Canvas(960, t("One turn, start to finish", "一轮对话的完整路径"),
               t("Every tool call passes the gate before it runs. A refusal is a result too: the model sees why and changes course.",
                 "每个工具调用都先过权限门。拒绝也是一种结果：模型会看到原因并换一种做法。"))
    mid, w = 520, 330
    x = mid - w / 2

    start = c.node(x + 60, 86, w - 120, 40, t("User message", "用户消息"), kind="blue", center=True)
    prep = c.node(x, 152, w, 64, t("Retrieve skills, prefetch memory", "检索技能，预取记忆"), t("BM25 over skill descriptions", "对技能描述做 BM25 检索"), "span skill_retrieval")
    fit = c.node(x, 244, w, 64, t("Fit the context", "整理上下文"), t("result budget → prune at 60% → fold at 70%", "结果预算 → 60% 修剪 → 70% 折叠"), "context_fold")
    call = c.node(x, 336, w, 64, t("Call the model", "调用模型"), t("Anthropic or OpenAI protocol, streamed", "Anthropic 或 OpenAI 协议，流式"), "assistant_delta · usage", "brand")
    ask = c.diamond(mid, 452, 210, 62, t("tool calls?", "有工具调用？"))
    done = c.node(60, 420, 240, 64, t("Answer", "给出回答"), t("save the session", "保存会话"), "turn_end", "blue")
    gate = c.node(x, 524, w, 66, t("Permission gate", "权限门"), t("rules → mode → destructive patterns", "规则 → 模式 → 危险命令模式"), "permission", "brand")

    deny = c.node(60, 652, 240, 70, t("Refused", "拒绝"), t("reason returned as the result", "把原因作为结果返回"), "tool_result ok=false", "bad")
    human = c.node(400, 652, 240, 70, t("Ask the user", "询问用户"), t("the turn waits for an answer", "这一轮停下来等答复"), "confirm_request", "brand")
    guard = c.node(740, 652, 240, 70, t("Loop guard", "循环保护"), t("same tool again, nothing new?", "又是同一个工具且没有新结果？"), "loop_guard")
    run = c.node(740, 762, 240, 74, t("Run the tool", "执行工具"), t(["read-only calls in parallel;", "shell async, streamed, killable"], ["只读调用并发执行；", "shell 异步、实时输出、可终止"]), "tool_output", "ok")
    back = c.node(x, 866, w, 60, t("Write the result back", "把结果写回对话"), code="tool_result", kind="brand")

    c.edge(start.bottom(), prep.top())
    c.edge(prep.bottom(), fit.top())
    c.edge(fit.bottom(), call.top())
    c.edge(call.bottom(), ask.top())
    c.edge(ask.left(), done.right(0.5), label=t("no", "没有"), kind="blue")
    c.edge(ask.bottom(), gate.top(), label=t("yes, for each call", "有，逐个处理"))
    c.edge(gate.left(), (deny.cx, gate.cy), deny.top(), label=t("deny", "拒绝"), kind="bad", at=0.45)
    c.edge(gate.bottom(), human.top(), label=t("confirm", "需确认"), kind="brand")
    c.edge(gate.right(), (guard.cx, gate.cy), guard.top(), label=t("allow", "放行"), kind="ok", at=0.45)
    c.edge(human.left(), deny.right(), label=t("no", "不允许"), kind="bad")
    c.edge(human.right(), guard.left(), label=t("yes", "允许"), kind="ok")
    c.edge(guard.bottom(), run.top(), label=t("no", "不是"), kind="ok")
    c.edge(guard.right(), (1006, guard.cy), (1006, back.cy + 14), back.right(0.73), label=t("yes: skip it", "是：跳过"), at=0.9, dy=-11)
    c.edge(run.bottom(), (run.cx, back.cy - 12), back.right(0.3))
    c.edge(deny.bottom(), (deny.cx, back.cy), back.left())
    c.edge(back.bottom(), (mid, 942), (34, 942), (34, fit.cy), fit.left(), label=t("next iteration", "进入下一次迭代"), kind="brand", at=0.17)
    return c


# ─── 3. skill evolution ─────────────────────────────────────


def evolution(lang: str) -> Canvas:
    t = lambda en, zh: pick(lang, en, zh)  # noqa: E731
    c = Canvas(690, t("How a skill changes", "技能是怎么演化的"),
               t("The evidence is the user's next message, never the model grading itself. An update must not be worse than what it replaces.",
                 "证据来自用户的下一条消息，而不是模型给自己打分。新版本不能比被它替换的旧版本差。"))
    w, gap, y = 180, 20, 100
    xs = [30 + i * (w + gap) for i in range(5)]
    a = c.node(xs[0], y, w, 86, t("Turn N", "第 N 轮"), t(["task → answer;", "remember the exchange"], ["任务 → 回答；", "记下这次交互"]), kind="blue")
    b = c.node(xs[1], y, w, 86, t("Turn N+1", "第 N+1 轮"), t(["the user reacts:", "“from now on, always…”"], ["用户给出反馈：", "“以后都……”"]), kind="blue")
    g = c.node(xs[2], y, w, 86, t("Pre-gate", "预筛"), t(["does it read as durable", "feedback? (no model call)"], ["像长期有效的反馈吗？", "（不调用模型）"]), "skill_gating.py")
    e = c.node(xs[3], y, w, 86, t("Extractor", "提取器"), t(["one candidate rule,", "from the user's words"], ["从用户原话里", "提取一条候选规则"]), "side model", "brand")
    m = c.node(xs[4], y, w, 86, t("Maintainer", "维护者"), t(["add · merge · discard", "against existing skills"], ["新增 · 合并 · 丢弃", "对照已有技能决定"]), "side model", "brand")
    for left, right in ((a, b), (b, g), (g, e), (e, m)):
        c.edge(left.right(), right.left())

    gate = c.node(330, 262, 380, 96, t("Non-inferiority gate", "非劣门禁"),
                  t(["replay the skill's own past cases on old and new;", "hard failures must not rise, pass rate must not fall"],
                    ["用该技能自己的历史用例重放新旧两版；", "硬失败不能变多，通过率不能下降"]), "skill_gate.py", "brand")
    add = c.node(xs[4], 262, w, 96, t("New skill", "新增技能"), t(["nothing to compare", "with yet: written as v1"], ["没有旧版本可比：", "直接写成 v1"]), kind="ok")
    c.edge(m.bottom(0.3), (m.bottom(0.3)[0], 226), (gate.top(0.8)[0], 226), gate.top(0.8), label=t("merge", "合并"), kind="brand", at=0.5)
    c.edge(m.bottom(0.72), add.top(0.72), label=t("add", "新增"), kind="ok")

    admit = c.node(250, 430, 270, 96, t("Admit", "采纳"), t(["write SKILL.md vN+1, snapshot vN,", "write an Evolution Card"], ["写入 SKILL.md vN+1，快照 vN，", "生成进化卡片"]), "skill_history.py", "ok")
    quarantine = c.node(550, 430, 270, 96, t("Quarantine", "隔离"), t(["candidate set aside, live file", "restored, card still written"], ["候选被搁置，线上文件恢复，", "卡片照样生成"]), "evolution_card.py", "bad")
    c.edge((440, gate.y + gate.h), (440, admit.y), label=t("pass", "通过"), kind="ok")
    c.edge((620, gate.y + gate.h), (620, quarantine.y), label=t("fail", "未通过"), kind="bad")

    store = c.node(250, 590, 570, 62, t("Skill store", "技能库"), t("SKILL.md · history/ · cards/ · quarantine/", "SKILL.md · history/ · cards/ · quarantine/"), ".keelson/skills/<name>/")
    c.edge(admit.bottom(), (admit.cx, 590))
    c.edge(quarantine.bottom(), (quarantine.cx, 590), dashed=True)
    c.edge(add.bottom(), (add.cx, store.cy), store.right())
    c.edge(store.left(), (60, store.cy), (60, a.y + a.h), label=t("retrieved on the next matching task", "下次遇到同类任务时被检索出来"), kind="blue", at=0.4, dx=104)

    c.text(84, 286, t("Why a gate?", "为什么要有门禁？"), "note-strong")
    for i, line in enumerate(t(["A rule distilled from one", "conversation can quietly break", "what an older version got right.", "The gate makes that visible", "before the file is replaced."],
                               ["从一次对话里提炼出的规则，", "可能悄悄破坏旧版本原本", "做对的事。门禁让这种", "退步在文件被替换之前", "就暴露出来。"])):
        c.text(84, 308 + i * 17, line)
    return c


# ─── 4. event stream ────────────────────────────────────────


def events(lang: str) -> Canvas:
    t = lambda en, zh: pick(lang, en, zh)  # noqa: E731
    c = Canvas(770, t("One event stream, three consumers", "一条事件流，三个消费者"),
               t("The runtime never prints. It publishes events; the terminal, the browser and the recorder subscribe. Replay re-feeds the same events.",
                 "运行时自己不打印，只发布事件；终端、浏览器和录制器各自订阅。回放就是把同一批事件再喂一遍。"))
    w, xs = 310, (30, 365, 700)
    p1 = c.node(xs[0], 92, w, 62, t("Agent loop", "Agent 循环"), code="user_message · assistant_delta · usage · span", kind="blue")
    p2 = c.node(xs[1], 92, w, 62, t("Tools and the gate", "工具与权限门"), code="tool_call · permission · tool_output · tool_result", kind="blue")
    p3 = c.node(xs[2], 92, w, 62, t("Session", "会话"), code="session · confirm_request · confirm_resolved", kind="blue")

    c.node(30, 206, 980, 104, t("EventBus", "EventBus"),
                 t(["delivered synchronously, in publish order · every event gets a sequence number · a subscriber that throws never breaks the run",
                    "keeps the last 5000 events, so a page that joins late (or reconnects) catches up from the number it last saw",
                    "the current bus is a ContextVar: a turn and every task it spawns publish to their own session's bus"],
                   ["同步投递，严格按发布顺序 · 每个事件带一个序号 · 订阅者抛异常不会影响运行",
                    "保留最近 5000 个事件：晚加入或断线重连的页面，从它见过的最后一个序号接着追",
                    "当前总线是一个 ContextVar：一轮对话及其派生的所有任务，都发布到自己会话的总线上"]),
                 "keelson/events.py", "brand")
    for p in (p1, p2, p3):
        c.edge(p.bottom(), (p.cx, 206), label="emit()" if p is p2 else "")

    s1 = c.node(xs[0], 366, w, 64, t("Terminal renderer", "终端渲染器"), t("the REPL's display", "REPL 的显示"), "keelson/ui.py")
    s2 = c.node(xs[1], 366, w, 64, t("SSE endpoint", "SSE 接口"), t("one stream per session", "每个会话一条流"), "GET /api/sessions/{id}/events")
    s3 = c.node(xs[2], 366, w, 64, t("Trace recorder", "轨迹录制器"), t("appends every event to a file", "把每个事件追加到文件"), "keelson/web/recorder.py")
    for s in (s1, s2, s3):
        c.edge((s.cx, 310), s.top(), kind="brand", label=t("subscribe", "订阅") if s is s2 else "")

    c.group(350, 462, 660, 278, t("In the browser", "浏览器里"))
    live = c.node(xs[1], 492, w, 64, t("LiveStream", "LiveStream"), t("EventSource; resumes with Last-Event-ID", "EventSource；用 Last-Event-ID 续传"), "js/stream.js")
    player = c.node(xs[2], 492, w, 64, t("ReplayPlayer", "ReplayPlayer"), t("plays a trace on its original timing", "按原始时间节奏播放轨迹"), "js/stream.js")
    reducer = c.node(xs[1], 592, 645, 58, t("One reducer: state = apply(state, event)", "同一个 reducer：state = apply(state, event)"), code="js/store.js", kind="brand")
    views = c.node(xs[1], 678, 645, 46, t("Conversation · inspector · timing waterfall · guided tour", "对话 · 检查器 · 耗时瀑布图 · 讲解模式"), center=True)
    term = c.node(xs[0], 492, w, 64, t("Your terminal", "你的终端"), t("spinner, tool lines, Markdown", "加载动画、工具行、Markdown"))

    c.edge(s1.bottom(), term.top())
    c.edge(s2.bottom(), live.top(), label="SSE")
    c.edge(s3.bottom(), player.top(), label="trace.jsonl", dashed=True)
    c.edge(live.bottom(), (live.cx, 592))
    c.edge(player.bottom(), (player.cx, 592))
    c.edge((reducer.cx, 650), (views.cx, 678))
    return c


# ─── 5. context management ──────────────────────────────────


def context(lang: str) -> Canvas:
    t = lambda en, zh: pick(lang, en, zh)  # noqa: E731
    c = Canvas(560, t("Keeping a long task inside the context window", "让长任务留在上下文窗口之内"),
               t("Three tiers, cheapest first. The first two cost nothing; only the last one calls a model.",
                 "三层手段，先用便宜的。前两层没有额外开销，只有最后一层需要调用模型。"))
    x0, x1, y, h = 60, 980, 112, 22
    span = x1 - x0
    x60, x70 = x0 + span * 0.6, x0 + span * 0.7
    c.raw(f'<rect class="track" x="{x0}" y="{y}" width="{span}" height="{h}" rx="6"/>'
          f'<rect class="fill-1" x="{x0}" y="{y}" width="{span * 0.6:.1f}" height="{h}" rx="6"/>'
          f'<rect class="fill-2" x="{x60:.1f}" y="{y}" width="{span * 0.1:.1f}" height="{h}"/>'
          f'<rect class="fill-3" x="{x70:.1f}" y="{y}" width="{span * 0.3:.1f}" height="{h}" rx="6"/>'
          f'<line class="tick" x1="{x60:.1f}" y1="{y - 6}" x2="{x60:.1f}" y2="{y + h + 6}"/>'
          f'<line class="tick" x1="{x70:.1f}" y1="{y - 6}" x2="{x70:.1f}" y2="{y + h + 6}"/>')
    c.text(x0, y - 12, t("context window in use", "上下文窗口占用"), "sub")
    c.text(x60, y - 12, "60%", "label", "middle")
    c.text(x70, y - 12, "70%", "label", "middle")
    c.text(x1, y - 12, "100%", "label", "end")

    w = 306
    tier1 = c.node(30, 214, w, 112, t("1 · Per-result budget", "1 · 单条结果预算"),
                   t(["one oversized tool result is written", "to disk; the model gets a preview", "and a reference it can read later"],
                     ["过大的单条工具结果写到磁盘；", "模型拿到的是预览，", "以及之后可以再读的引用"]), t("on every tool result", "每条工具结果都检查"), "blue")
    tier2 = c.node(367, 214, w, 112, t("2 · Prune stale results", "2 · 修剪过期结果"),
                   t(["old tool results are trimmed;", "the most recent ones stay intact", "so the current step still has them"],
                     ["旧的工具结果被裁短；", "最近的几条完整保留，", "当前这一步仍然用得上"]), t("at 60% of the window", "窗口占用到 60% 时"), "brand")
    tier3 = c.node(704, 214, w, 112, t("3 · Fold", "3 · 折叠"),
                   t(["a side model rewrites the history", "into three structured slices,", "then the turn continues"],
                     ["由辅助模型把历史改写成", "三个结构化的切片，", "然后这一轮继续"]), t("at 70% · emits context_fold", "70% 时 · 发出 context_fold"), "bad")
    c.edge((x0 + 40, y + h), (x0 + 40, 180), (tier1.cx, 180), tier1.top(), kind="blue")
    c.edge((x60, y + h + 6), (x60, 180), (tier2.cx, 180), tier2.top(), kind="brand")
    c.edge((x70, y + h + 6), (x70, 196), (tier3.cx, 196), tier3.top(), kind="bad")

    sw = 198
    labels = [
        (t("Episodic", "情景记忆"), t(["what happened: the task,", "key events, progress"], ["发生了什么：任务、", "关键事件、当前进度"])),
        (t("Working", "工作记忆"), t(["the goal right now", "and the next actions"], ["眼下的目标", "和接下来要做的事"])),
        (t("Tool", "工具记忆"), t(["what worked, what failed,", "so it is not retried blindly"], ["哪些做法有效、哪些失败，", "避免盲目重试"])),
    ]
    for i, (name, lines) in enumerate(labels):
        slice_box = c.node(398 + i * (sw + 9), 410, sw, 92, name, lines)
        c.edge((slice_box.cx, 370), slice_box.top())
    c.raw(f'<path class="edge" d="M{tier3.cx},{tier3.y + tier3.h} V370 M{398 + sw / 2},370 H{398 + 2 * (sw + 9) + sw / 2}"/>')

    c.text(30, 424, t("Why three tiers?", "为什么分三层？"), "note-strong")
    for i, line in enumerate(t(["Folding loses detail and costs a model", "call, so it is the last resort. Most long", "runs are kept in bounds by the first", "two, which only touch tool output."],
                               ["折叠会丢细节，还要多调用一次模型，", "所以放在最后。大多数长任务靠前两层", "就能控制住，它们只动工具输出，", "不动对话本身。"])):
        c.text(30, 446 + i * 17, line)
    return c



# ─── banner (README header) ─────────────────────────────────


def banner(lang: str, *, force_dark: bool = False) -> str:
    """The README header: name, one line of what it is, and the loop as a motif."""
    import math

    t = lambda en, zh: pick(lang, en, zh)  # noqa: E731
    height = 250
    tagline = t("The model proposes. The runtime decides, executes and records.", "模型只管提议，执行由运行时把关。")
    sub = t("A self-evolving agent harness in Python, with no agent framework underneath.",
            "用 Python 写的可自进化 Agent 运行时，底层不依赖任何 Agent 框架。")
    pills = t(["permission gate", "loop guard", "context folding", "skill evolution", "trace replay"],
              ["权限门", "循环保护", "上下文折叠", "技能自进化", "轨迹回放"])
    stages = t(["model", "gate", "tool", "result", "fold"], ["模型", "权限门", "工具", "回写", "折叠"])

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" role="img" aria-label="Keelson">',
        f"<title>Keelson — {escape(tagline)}</title>",
        f"<style>{_style(force_dark)}"
        ".name{font-size:50px;font-weight:720;letter-spacing:-.02em;fill:var(--text)}.tag{font-size:18px;font-weight:560;fill:var(--text)}"
        ".lead{font-size:13.5px;fill:var(--muted)}.mark{fill:none;stroke:var(--brand);stroke-width:2.2;stroke-linecap:round}"
        ".pill{fill:var(--panel);stroke:var(--line)}.pill-text{font-size:11.5px;fill:var(--muted)}"
        ".ring{fill:none;stroke:var(--line);stroke-width:1.5}.ring-flow{fill:none;stroke:var(--brand);stroke-width:1.8;stroke-dasharray:2 11;stroke-linecap:round}"
        ".dot{fill:var(--canvas);stroke:var(--group);stroke-width:1.8}.dot.on{fill:var(--brand);stroke:var(--brand)}"
        ".stage{font-size:11.5px;fill:var(--muted)}.stage.on{fill:var(--text);font-weight:620}</style>",
        f'<rect class="canvas" x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="14"/>',
        '<g class="mark" transform="translate(44,40) scale(2.1)"><path d="M16 4v24"/><path d="M16 9c-5 1-8 4-9 9"/>'
        '<path d="M16 9c5 1 8 4 9 9"/><path d="M16 17c-3 .6-5 2.4-6 5.5"/><path d="M16 17c3 .6 5 2.4 6 5.5"/></g>',
        '<text class="name" x="122" y="92">Keelson</text>',
        f'<text class="tag" x="48" y="146">{escape(tagline)}</text>',
        f'<text class="lead" x="48" y="172">{escape(sub)}</text>',
    ]
    x = 48.0
    for label in pills:
        width = text_width(label, 11.5) + 22
        parts.append(f'<rect class="pill" x="{x:.1f}" y="194" width="{width:.1f}" height="24" rx="12"/>'
                     f'<text class="pill-text" x="{x + width / 2:.1f}" y="210" text-anchor="middle">{escape(label)}</text>')
        x += width + 8

    cx, cy, radius = 862, 125, 70
    parts.append(f'<circle class="ring" cx="{cx}" cy="{cy}" r="{radius}"/><circle class="ring-flow" cx="{cx}" cy="{cy}" r="{radius}"/>')
    for i, label in enumerate(stages):
        angle = math.radians(-90 + i * 72)
        px, py = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        on = " on" if i == 1 else ""
        side = "middle" if abs(px - cx) < 6 else ("start" if px > cx else "end")
        lx = px + (0 if side == "middle" else 13 if side == "start" else -13)
        ly = py - 13 if side == "middle" else (py + 21 if py > cy and abs(px - cx) < radius * 0.8 else py + 4)
        if side != "middle" and py > cy and abs(px - cx) < radius * 0.8:
            lx, side = px, "middle"
        parts.append(f'<circle class="dot{on}" cx="{px:.1f}" cy="{py:.1f}" r="{7 if on else 5}"/>'
                     f'<text class="stage{on}" x="{lx:.1f}" y="{ly:.1f}" text-anchor="{side}">{escape(label)}</text>')
    parts.append("</svg>\n")
    return "\n".join(parts)


FIGURES = {"overview": overview, "turn": turn, "evolution": evolution, "events": events, "context": context}


def build(out_dir: Path, *, force_dark: bool = False) -> dict[Path, str]:
    files = {out_dir / f"{name}.{lang}.svg": figure(lang).render(force_dark=force_dark)
             for name, figure in FIGURES.items() for lang in ("en", "zh")}
    for lang in ("en", "zh"):
        files[out_dir / f"banner.{lang}.svg"] = banner(lang, force_dark=force_dark)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the files on disk differ from what would be generated")
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--dark", action="store_true", help="force the dark palette (for previewing; do not commit)")
    args = parser.parse_args()
    files = build(Path(args.out), force_dark=args.dark)

    if args.check:
        stale = [path.name for path, svg in files.items() if not path.exists() or path.read_text(encoding="utf-8") != svg]
        if stale:
            print("stale diagrams (run scripts/build_diagrams.py):", ", ".join(stale))
            return 1
        print(f"{len(files)} diagrams up to date")
        return 0

    Path(args.out).mkdir(parents=True, exist_ok=True)
    for path, svg in files.items():
        path.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {len(files)} diagrams to {Path(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

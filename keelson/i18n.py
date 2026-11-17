"""Interface language: Chinese or English.

``KEELSON_LANG=zh`` or ``en`` chooses; otherwise the system's UI language
decides (Chinese for a Chinese Windows or a ``zh_*`` locale, else English).
Only what a person reads is translated: commands keep their English names
(``/undo``), and what the model reads stays in English.
"""

from __future__ import annotations

import locale
import os
import sys

_cached: str | None = None


def _system_language() -> str:
    if sys.platform == "win32":
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "zh" if (langid & 0x3FF) == 0x04 else "en"
        except Exception:
            pass
    for value in (os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"), os.environ.get("LANG"),
                  (locale.getlocale()[0] or "")):
        if value:
            return "zh" if value.lower().startswith(("zh", "chinese")) else "en"
    return "en"


def lang() -> str:
    global _cached
    wanted = os.environ.get("KEELSON_LANG", "").strip().lower()
    if wanted.startswith("zh"):
        return "zh"
    if wanted.startswith("en"):
        return "en"
    if _cached is None:
        _cached = _system_language()
    return _cached


def t(key: str, **values) -> str:
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang()) or entry["en"]
    return text.format(**values) if values else text


def pick(zh: str, en: str) -> str:
    return zh if lang() == "zh" else en


STRINGS: dict[str, dict[str, str]] = {
    # welcome and session chrome
    "tagline": {"en": "a coding agent that checks its work and can undo it",
                "zh": "会核实结果、能完整撤销的编码 Agent"},
    "model": {"en": "model", "zh": "模型"},
    "mode": {"en": "mode", "zh": "模式"},
    "workspace": {"en": "workspace", "zh": "工作区"},
    "checks": {"en": "checks", "zh": "验证"},
    "checks_none": {"en": "none yet (/init)", "zh": "未设置（/init）"},
    "type_request": {"en": "type a request · /help for commands", "zh": "直接输入需求 · /help 查看命令"},
    "hint_keys": {"en": "Enter sends · Alt+Enter new line · Ctrl+C stops the turn · Shift+Tab switches mode · "
                        "@file adds a file · /help",
                  "zh": "Enter 发送 · Alt+Enter 换行 · Ctrl+C 打断本轮 · Shift+Tab 切换模式 · @文件 引用文件 · /help"},
    "hint_first": {"en": "(these tips show for your first few sessions)", "zh": "（前几次启动时显示）"},
    "goodbye": {"en": "Session saved. Bye.", "zh": "会话已保存，再见。"},
    "interrupted": {"en": "Stopped. Press Ctrl+C again to exit.", "zh": "已打断。再按一次 Ctrl+C 退出。"},
    "ctrl_c_again": {"en": "Press Ctrl+C again to exit.", "zh": "再按一次 Ctrl+C 退出。"},
    # permission modes
    "mode.default": {"en": "default: reversible steps run, the rest asks",
                     "zh": "默认：可撤销的直接做，撤不回的先问"},
    "mode.acceptEdits": {"en": "accept edits", "zh": "自动接受改动"},
    "mode.plan": {"en": "plan (read-only)", "zh": "计划（只读）"},
    "mode.dontAsk": {"en": "don't ask (deny what needs an OK)", "zh": "不询问（需要确认的一律拒绝）"},
    "mode.bypassPermissions": {"en": "bypass (no checks)", "zh": "全部放行（不检查）"},
    "mode_short.default": {"en": "default", "zh": "默认"},
    "mode_short.acceptEdits": {"en": "accept edits", "zh": "自动接受改动"},
    "mode_short.plan": {"en": "plan", "zh": "计划·只读"},
    "mode_short.dontAsk": {"en": "don't ask", "zh": "不询问"},
    "mode_short.bypassPermissions": {"en": "bypass", "zh": "全部放行"},
    "mode_now": {"en": "Mode: {mode}", "zh": "模式：{mode}"},
    # confirmation
    "confirm_title": {"en": "Permission required", "zh": "需要你确认"},
    "confirm.once": {"en": "y = once", "zh": "y = 允许一次"},
    "confirm.session": {"en": "s = this session", "zh": "s = 本会话都允许"},
    "confirm.project": {"en": "p = always in this project", "zh": "p = 本项目一直允许"},
    "confirm.deny": {"en": "n = deny (n: reason = deny and say why)", "zh": "n = 拒绝（n: 理由 = 拒绝并说明）"},
    "confirm_ask": {"en": "  Allow? [{keys}]: ", "zh": "  允许吗？[{keys}]: "},
    # verification badge
    "verify.running": {"en": "verifying", "zh": "正在验证"},
    "verify.passed": {"en": "✓ Verified", "zh": "✓ 已验证"},
    "verify.failed": {"en": "✗ Checks fail", "zh": "✗ 未通过验证"},
    "verify.unverified": {"en": "! Not verified", "zh": "! 未验证"},
    "verify.weakened": {"en": "tests weakened", "zh": "测试被削弱"},
    "verify.unproven": {"en": "unproven claim — “{claim}” (no passing check backs it)",
                        "zh": "未经证实的说法 —「{claim}」（没有通过的检查记录支撑）"},
    "verify.out_of_scope": {"en": "changed outside the request's scope", "zh": "改了本次请求范围外的文件"},
    # scope, task, folds
    "scope": {"en": "scope", "zh": "范围"},
    "plan": {"en": "Plan", "zh": "计划"},
    "decision": {"en": "decision", "zh": "决定"},
    "folded": {"en": "context folded ({trigger})", "zh": "上下文已折叠（{trigger}）"},
    "folded_kept": {"en": "kept word for word: {kept}", "zh": "原样保留：{kept}"},
    # turn line
    "turn.tokens": {"en": "{tin} in / {tout} out", "zh": "输入 {tin} / 输出 {tout}"},
    "turn.cost": {"en": "est. ${cost:.4f}", "zh": "约 ${cost:.4f}"},
    "turn.cache": {"en": "cache {rate:.0%}", "zh": "缓存 {rate:.0%}"},
    "turn.ctx": {"en": "ctx", "zh": "上下文"},
    "turn.done": {"en": "turn complete", "zh": "本轮结束"},
    # tools
    "thinking": {"en": "Thinking", "zh": "思考中"},
    "denied": {"en": "denied by permission gate", "zh": "被权限门拒绝"},
    "more_lines": {"en": "… {n} more lines", "zh": "… 还有 {n} 行"},
    "chars": {"en": "chars", "zh": "字符"},
    # misc REPL
    "unknown_command": {"en": "Unknown command {cmd}. /help lists them.", "zh": "没有 {cmd} 这个命令，/help 查看全部。"},
    "trust_q": {"en": "  Trust this folder? [y/N]: ", "zh": "  信任这个文件夹吗？[y/N]: "},
    "skill_meta": {"en": "skill", "zh": "技能"},
    "toolbar_mode": {"en": "switches mode", "zh": "切换模式"},
    # /status
    "status.session": {"en": "session", "zh": "会话"},
    "status.context": {"en": "context", "zh": "上下文"},
    "status.cost": {"en": "cost", "zh": "花费"},
    "status.files": {"en": "files added", "zh": "已加入的文件"},
    "status.undo": {"en": "undo", "zh": "撤销"},
    "status.undo_full": {"en": "{n} turn(s) can be undone, shell changes included",
                         "zh": "可撤销 {n} 轮，包括 shell 造成的改动"},
    "status.undo_partial": {"en": "{n} turn(s) can be undone (edits only: {reason})",
                            "zh": "可撤销 {n} 轮（仅编辑工具的改动：{reason}）"},
    # /diff, /review
    "diff.none": {"en": "No changes in the last {n} turn(s).", "zh": "最近 {n} 轮没有改动。"},
    "diff.off": {"en": "Checkpoints are off here, so there is nothing to diff.", "zh": "这里没有开启检查点，无法查看改动。"},
    "review.none": {"en": "No turn to review yet.", "zh": "还没有可回顾的一轮。"},
    "review.files": {"en": "files changed", "zh": "改动的文件"},
    "review.hint": {"en": "/diff shows the changes · /undo puts them back", "zh": "/diff 查看改动 · /undo 全部撤回"},
    # /add, /drop
    "add.usage": {"en": "Usage: /add FILE... (Tab completes paths)", "zh": "用法：/add 文件...（Tab 补全路径）"},
    "add.added": {"en": "Added to the conversation: {files}", "zh": "已加入对话：{files}"},
    "add.missing": {"en": "Not found: {files}", "zh": "找不到：{files}"},
    "add.now": {"en": "In the conversation: {files}", "zh": "当前在对话中：{files}"},
    "add.empty": {"en": "No files added. /add FILE puts one in.", "zh": "还没有加入文件。/add 文件 可加入。"},
    "drop.done": {"en": "Taken out: {files}", "zh": "已移出：{files}"},
    "drop.none": {"en": "Nothing to take out.", "zh": "没有可移出的文件。"},
    # /init
    "init.exists": {"en": "{path} already exists. /verify init adds the check commands to it.",
                    "zh": "{path} 已存在。/verify init 可把验证命令写进去。"},
    "init.done": {"en": "Wrote {path}. Edit it to add your rules; Keelson reads it every session.",
                  "zh": "已写入 {path}。可以在里面补充你的规则，Keelson 每次会话都会读取。"},
    # /resume
    "resume.none": {"en": "No saved sessions in this project.", "zh": "这个项目还没有保存的会话。"},
    "resume.pick": {"en": "/resume N reopens one.", "zh": "输入 /resume N 重新打开。"},
    "resume.done": {"en": "Resumed {id} ({n} messages).", "zh": "已恢复会话 {id}（{n} 条消息）。"},
    "resume.bad": {"en": "No session {n}. /resume lists them.", "zh": "没有第 {n} 个会话，/resume 查看列表。"},
    # /mode
    "mode.bad": {"en": "Unknown mode {mode}. Choose: {choices}", "zh": "没有 {mode} 这个模式，可选：{choices}"},
    # -p
    "print.empty": {"en": "Nothing to do: give a prompt (keelson -p \"...\") or pipe one in.",
                    "zh": "没有任务：请给出提示（keelson -p \"...\"）或通过管道输入。"},
    "print.denied": {"en": "needs confirmation; keelson -p never asks (use --accept-edits or --yolo)",
                     "zh": "需要确认；keelson -p 不会询问（可用 --accept-edits 或 --yolo）"},
}

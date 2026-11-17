"""The terminal's slash commands: one list for /help, completion and the docs.

Names stay English (``/undo``); descriptions follow the interface language.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import lang


@dataclass(frozen=True)
class Command:
    name: str           # "/undo"
    args: str           # "[N] [--force]"
    group: str          # work | undo | check | context | session | skills
    en: str
    zh: str

    def help(self) -> str:
        return self.zh if lang() == "zh" else self.en


COMMANDS: list[Command] = [
    # work
    Command("/help", "", "session", "List commands", "列出所有命令"),
    Command("/status", "", "session", "Model, mode, workspace, checks, context, cost", "模型、模式、工作区、验证、上下文、花费"),
    Command("/model", "[NAME]", "session", "Show or switch the model on this endpoint", "查看或切换模型（同一接口）"),
    Command("/mode", "[default|acceptEdits|plan]", "session", "Show or switch the permission mode (also Shift+Tab)",
            "查看或切换权限模式（也可按 Shift+Tab）"),
    Command("/plan", "", "session", "Toggle plan mode (read-only)", "切换计划模式（只读）"),
    Command("/init", "", "check", "Create KEELSON.md with the detected check commands", "创建 KEELSON.md，写入识别出的验证命令"),
    # undo and review
    Command("/diff", "[N]", "undo", "The changes the last N turns made (default 1), shell changes included",
            "最近 N 轮的改动（默认 1），包括 shell 造成的"),
    Command("/review", "", "undo", "The last turn: verification, flags, files changed",
            "回顾上一轮：验证结果、警示、改动的文件"),
    Command("/undo", "[--force]", "undo", "Put back what the last turn changed, shell changes too",
            "撤销上一轮的全部改动，包括 shell 造成的"),
    Command("/rewind", "N", "undo", "Undo the last N turns", "撤销最近 N 轮"),
    Command("/redo", "", "undo", "Reverse the last /undo", "反悔上一次 /undo"),
    Command("/revert", "FILE [N]", "undo", "Put one file back as it was N turns ago", "把一个文件还原到 N 轮之前"),
    Command("/checkpoints", "", "undo", "List the turns that can be undone", "列出可撤销的轮次"),
    # checks
    Command("/verify", "[init|run]", "check", "End-of-turn checks: show, save to KEELSON.md, or run now",
            "轮末验证：查看、写入 KEELSON.md 或立即运行"),
    # context
    Command("/add", "FILE...", "context", "Put files in the conversation (and in scope)", "把文件加入对话（并纳入范围）"),
    Command("/drop", "[FILE...]", "context", "Take files out of the conversation (all if none given)",
            "把文件移出对话（不写则全部移出）"),
    Command("/task", "[add|drop|goal|clear]", "context", "The task ledger: goal, your rules, plan, files",
            "任务账本：目标、你的规则、计划、改过的文件"),
    Command("/compact", "", "context", "Fold the context now (the task ledger is kept)", "立即折叠上下文（任务账本保留）"),
    Command("/clear", "", "context", "Start a fresh conversation", "清空对话重新开始"),
    Command("/memory", "", "context", "Long-term memories", "长期记忆"),
    # session
    Command("/usage", "", "session", "Tokens, cache hit rate and cost: session, today, 7 days",
            "token、缓存命中率和花费：本会话、今天、7 天"),
    Command("/cost", "", "session", "Tokens and cost of this session", "本会话的 token 和花费"),
    Command("/resume", "[N]", "session", "List recent sessions, or reopen one", "列出最近的会话，或重新打开一个"),
    Command("/exit", "", "session", "Quit (the session is saved)", "退出（会话会保存）"),
    # skills
    Command("/skills", "", "skills", "List skills", "列出技能"),
    Command("/skill-stats", "", "skills", "Skill usage and evolution", "技能使用与进化统计"),
    Command("/skill-history", "SKILL", "skills", "A skill's versions", "技能的版本历史"),
    Command("/skill-diff", "SKILL [VERSION]", "skills", "Diff a skill against a version", "对比技能与某个版本"),
    Command("/skill-rollback", "SKILL [VERSION]", "skills", "Roll a skill back", "回滚技能"),
    Command("/skill-create", "name | description | when | instructions", "skills", "Create a skill by hand",
            "手动创建技能"),
    Command("/skill-evolve", "SKILL LESSON", "skills", "Add a lesson to a skill", "给技能加一条经验"),
    Command("/skill-feedback", "SKILL RATING [NOTE]", "skills", "Rate a skill", "给技能打分"),
    Command("/skill-eval", "", "skills", "Evaluate online skill evolution", "评估在线技能进化"),
    Command("/extract_now", "[HINT]", "skills", "Extract a skill from this conversation now", "立即从本次对话提取技能"),
]

GROUPS = [
    ("session", "Session", "会话"),
    ("context", "Context", "上下文"),
    ("undo", "Review and undo", "审阅与撤销"),
    ("check", "Checks", "验证"),
    ("skills", "Skills", "技能"),
]

BY_NAME = {c.name: c for c in COMMANDS}


def names() -> list[str]:
    return [c.name for c in COMMANDS]


def help_text() -> str:
    width = max(len(f"{c.name} {c.args}".strip()) for c in COMMANDS) + 2
    lines = []
    for key, en, zh in GROUPS:
        lines.append(zh if lang() == "zh" else en)
        for c in COMMANDS:
            if c.group == key:
                lines.append(f"  {f'{c.name} {c.args}'.strip():<{width}}{c.help()}")
        lines.append("")
    lines.append("  /<skill> [args]".ljust(width + 2) + ("运行一个技能" if lang() == "zh" else "Run a skill"))
    lines.append("  @file".ljust(width + 2) + ("在消息里引用文件（也纳入范围）" if lang() == "zh"
                                              else "Mention a file in a message (it joins the scope)"))
    return "\n".join(lines)

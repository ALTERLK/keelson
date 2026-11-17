"""Cheap pre-filter for the online skill-evolution pipeline.

The Extractor + Maintainer run two model calls after (almost) every turn. On a
real deployment that is a large, mostly-wasted token bill: most user turns are
new requests, not durable feedback on the previous answer.

``looks_like_feedback`` is a zero-model gate: only turns that carry a
feedback/correction/preference signal are worth sending down the expensive path.
It is deliberately lenient (better a few false positives than a missed rule) and
fully overridable — an explicit ``/extract_now`` bypasses it.
"""

from __future__ import annotations

import re

# Chinese + English markers of "this is a standing rule / correction / preference",
# not a fresh task. Kept broad on purpose.
_FEEDBACK_PATTERNS = [
    r"以后", r"下次", r"每次", r"记住", r"默认", r"不要(再)?", r"别再?", r"应该", r"必须",
    r"这类", r"这种(问题|任务|情况)", r"统一", r"改成", r"换成", r"优先", r"尽量", r"注意",
    r"规范", r"格式(要|应)", r"风格", r"太(长|短|啰嗦|简单)", r"不够", r"漏了", r"错了",
    r"重来", r"不是这样", r"我要的是",
    r"\bfrom now on\b", r"\bnext time\b", r"\balways\b", r"\bnever\b", r"\bdon'?t\b",
    r"\bshould(n'?t)?\b", r"\bmust(n'?t)?\b", r"\bprefer\b", r"\binstead\b", r"\bremember to\b",
    r"\bthis kind of\b", r"\bfor future\b", r"\bin general\b", r"\bmake sure\b",
    r"\btoo (long|short|verbose|terse)\b", r"\byou (missed|forgot|got .* wrong)\b",
    r"\bredo\b", r"\bnot what i (wanted|asked)\b",
]
_COMPILED = re.compile("|".join(_FEEDBACK_PATTERNS), re.IGNORECASE)

# Turns that are obviously a brand-new task, not feedback — short-circuit to skip.
_NEW_TASK_PREFIXES = (
    "帮我", "写一个", "写个", "生成", "实现", "分析", "查一下", "总结", "翻译",
    "help me", "write a", "generate", "implement", "analyze", "summarize", "translate",
)


def looks_like_feedback(user_message: str, *, min_len: int = 4) -> bool:
    text = str(user_message or "").strip()
    if len(text) < min_len:
        return False
    low = text.lower()
    if any(low.startswith(p) for p in _NEW_TASK_PREFIXES) and not _COMPILED.search(text):
        return False
    return bool(_COMPILED.search(text))


def gating_reason(user_message: str) -> str:
    m = _COMPILED.search(str(user_message or ""))
    return f"matched {m.group(0)!r}" if m else "no feedback signal"

"""Context-fold fidelity probe.

Question it answers: *when the agent folds a long history into structured memory,
what does it lose?* — the "how do you know it's folding and not just dropping
data" question.

Method (the model-free parts live here):

1. From a real transcript, **programmatically** extract fact probes whose answers
   are known from the transcript itself (no model inventing questions):
     - which file was written / edited
     - which tool call failed
     - what the user's original request was
     - what a specific tool returned
   Each probe is tagged with the memory class it should survive in
   (``episode`` / ``working`` / ``tool``).
2. (needs a model) fold the transcript, then answer each probe from the folded
   memory only.
3. Score answers by containment; report retention overall and per memory class,
   and against a plain-truncation control.

``extract_probes`` and ``grade`` are pure; ``run`` wires in a folding callable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Probe:
    question: str
    answer: str
    memory_class: str  # episode | working | tool


_ERR_RE = re.compile(r"\b(error|failed|not found|denied|timed out|traceback)\b", re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def extract_probes(messages: list[dict[str, Any]], *, max_probes: int = 30) -> list[Probe]:
    probes: list[Probe] = []

    users = [m for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
    if users:
        first = users[0]["content"].strip()
        probes.append(Probe("What did the user originally ask for?", first[:200], "episode"))

    for m in messages:
        content = m.get("content")
        # Anthropic-style tool_use blocks
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name = b.get("name", "")
                    inp = b.get("input", {}) or {}
                    if name in ("write_file", "edit_file") and inp.get("file_path"):
                        probes.append(
                            Probe(f"Which file did the agent {name.split('_')[0]}?", str(inp["file_path"]), "tool")
                        )
                    elif name in ("run_shell",) and inp.get("command"):
                        probes.append(Probe("What shell command did the agent run?", str(inp["command"])[:160], "tool"))
                if b.get("type") == "tool_result":
                    txt = b.get("content")
                    txt = txt if isinstance(txt, str) else str(txt)
                    if _ERR_RE.search(txt):
                        probes.append(Probe("Did any tool call fail, and roughly what was the error?", txt.strip()[:160], "tool"))
        # OpenAI-style role:tool messages
        if m.get("role") == "tool" and isinstance(content, str) and _ERR_RE.search(content):
            probes.append(Probe("Did any tool call fail, and roughly what was the error?", content.strip()[:160], "tool"))
        # assistant text that states a plan / next step
        if m.get("role") == "assistant" and isinstance(content, str):
            for sent in re.split(r"(?<=[.!?。！？])\s+", content):
                low = sent.lower()
                if any(k in low for k in ("next", "then i", "plan", "todo", "接下来", "下一步", "计划")):
                    probes.append(Probe("What was the agent's stated next step / plan?", sent.strip()[:200], "working"))
                    break

    # de-dup on (question, answer), cap
    seen = set()
    out = []
    for p in probes:
        key = (p.question, _norm(p.answer))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_probes:
            break
    return out


def grade(probe: Probe, answer: str) -> bool:
    """Containment match — the probe's known answer (or its salient token) appears."""
    a = _norm(answer)
    gold = _norm(probe.answer)
    if not a:
        return False
    if gold in a or a in gold:
        return True
    # salient token overlap for longer golds (filenames, error words, commands)
    gold_tokens = [t for t in re.split(r"[\s/\\.:,'\"()\[\]]+", gold) if len(t) >= 4]
    if not gold_tokens:
        return False
    hit = sum(1 for t in gold_tokens if t in a)
    return hit / len(gold_tokens) >= 0.6


def score(probes: list[Probe], answers: list[str]) -> dict[str, Any]:
    per_class: dict[str, list[int]] = {}
    for p, ans in zip(probes, answers, strict=False):
        per_class.setdefault(p.memory_class, []).append(1 if grade(p, ans) else 0)
    overall = [v for lst in per_class.values() for v in lst]
    return {
        "n": len(overall),
        "retention": (sum(overall) / len(overall)) if overall else 0.0,
        "by_class": {
            k: {"n": len(v), "retention": round(sum(v) / len(v), 3) if v else 0.0}
            for k, v in sorted(per_class.items())
        },
    }


FoldFn = Callable[[str], str]          # transcript -> folded memory text
AnswerFn = Callable[[str, str], str]   # (context, question) -> answer


def run(
    messages: list[dict[str, Any]],
    transcript: str,
    *,
    fold_fn: FoldFn,
    answer_fn: AnswerFn,
    truncate_control: bool = True,
) -> dict[str, Any]:
    """Full loop (needs model-backed fold_fn / answer_fn). Returns the comparison."""
    probes = extract_probes(messages)
    if not probes:
        return {"error": "no probes could be extracted from this transcript"}

    folded = fold_fn(transcript)
    fold_answers = [answer_fn(folded, p.question) for p in probes]
    result = {"probes": len(probes), "folded": score(probes, fold_answers)}

    if truncate_control:
        trunc = transcript[-len(folded):] if len(transcript) > len(folded) else transcript
        trunc_answers = [answer_fn(trunc, p.question) for p in probes]
        result["truncated_control"] = score(probes, trunc_answers)

    return result

"""The task ledger: what the work is, kept by the runtime outside the model's context.

The failure this answers: after a long task's context is compacted, the agent
forgets the user's constraints and how far it got, then carries on and breaks
its own earlier work. Claude Code users report it in its issue tracker
("compaction destroys workflow"). A summary written by the model is exactly
what loses the detail, so the parts that matter are not summarised:

* **the goal**: the request that started the work, as typed;
* **the constraints**: every sentence of the user's that reads like a rule
  ("don't touch the API", "只改 parser", "always use pathlib"), word for word;
* **the plan**: steps and their status, kept by the model with ``update_task``;
* **decisions** the model recorded, **files changed**, the **last verification**;
* the **scope** of the current request (keelson/scope.py).

It is saved after every turn, put back verbatim after a context fold, restored
with the session, and shown with ``/task``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

STEP_STATUSES = ("pending", "in_progress", "done", "blocked")
MAX_CONSTRAINTS = 40
MAX_FILES = 200

# Sentences that set a rule. Deliberately broad: a kept sentence that was not a rule costs a line of context,
# a dropped rule costs a broken task.
_RULE = re.compile(
    r"\b(don'?t|do not|never|always|must|mustn'?t|only|without|avoid|keep|make sure|ensure|no more than|"
    r"at most|instead of|rather than|should(?:n'?t)?|need to|prefer|leave .* alone|not allowed)\b|"
    r"不要|别|不许|不准|不能|不得|禁止|必须|务必|一定要|只(?:改|能|用|在|需)|保持|保留|记得|注意|请勿|切勿|仅",
    re.IGNORECASE,
)
# ASCII punctuation ends a sentence only before whitespace (so "parser.py" stays whole); CJK punctuation always.
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；\n])\s*")
_TRAILING = re.compile(r"[\s.!?;,。！？；，]+$")
_STRIP = re.compile(r"<(?:git-status|keelson-[\w-]+)>.*?</(?:git-status|keelson-[\w-]+)>", re.DOTALL)


def rule_sentences(text: str) -> list[str]:
    """The sentences of a user message that read like constraints, as written."""
    text = _STRIP.sub("", text or "")
    found = []
    for sentence in _SENTENCE.split(text):
        s = " ".join(sentence.split())
        if 4 <= len(s) <= 400 and _RULE.search(s):
            found.append(s)
    return found


@dataclass
class Ledger:
    goal: str = ""
    constraints: list[dict] = field(default_factory=list)    # {text, count, turn}
    plan: list[dict] = field(default_factory=list)           # {step, status}
    decisions: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    last_verify: dict | None = None                          # {status, detail, at}
    scope: list[str] = field(default_factory=list)
    turns: int = 0
    updated: str = ""

    # ── what the user said ───────────────────────────────────
    def note_user_message(self, text: str) -> None:
        self.turns += 1
        clean = _STRIP.sub("", text or "").strip()
        if not self.goal and clean:
            self.goal = clean[:800]
        for sentence in rule_sentences(clean):
            self.add_constraint(sentence)

    def add_constraint(self, text: str) -> None:
        key = _TRAILING.sub("", text).casefold()
        for c in self.constraints:
            if _TRAILING.sub("", c["text"]).casefold() == key:
                c["count"] += 1
                return
        self.constraints.append({"text": text, "count": 1, "turn": self.turns})
        if len(self.constraints) > MAX_CONSTRAINTS:
            # Drop the oldest one said only once; rules repeated by the user stay.
            for i, c in enumerate(self.constraints):
                if c["count"] == 1:
                    del self.constraints[i]
                    break
            else:
                del self.constraints[0]

    def drop_constraint(self, index: int) -> bool:
        if 0 <= index < len(self.constraints):
            del self.constraints[index]
            return True
        return False

    def repeated_constraints(self) -> list[str]:
        """Rules the user stated more than once: candidates for KEELSON.md."""
        return [c["text"] for c in self.constraints if c["count"] >= 2]

    # ── what the model and the runtime recorded ──────────────
    def update(self, plan: list | None = None, decision: str | None = None) -> str:
        if plan is not None:
            steps = []
            for item in plan:
                if isinstance(item, str):
                    item = {"step": item}
                if not isinstance(item, dict) or not str(item.get("step", "")).strip():
                    continue
                status = str(item.get("status") or "pending").strip().lower().replace(" ", "_")
                steps.append({"step": str(item["step"]).strip()[:300],
                              "status": status if status in STEP_STATUSES else "pending"})
            self.plan = steps
        if decision and decision.strip():
            self.decisions.append(decision.strip()[:500])
            self.decisions = self.decisions[-30:]
        done = sum(1 for s in self.plan if s["status"] == "done")
        return f"Task updated: {done}/{len(self.plan)} steps done, {len(self.decisions)} decisions recorded."

    def note_files(self, files: list[str]) -> None:
        for f in files:
            if f and not f.startswith("(") and f not in self.files:
                self.files.append(f)
        self.files = self.files[-MAX_FILES:]

    def note_verify(self, status: str, detail: str) -> None:
        self.last_verify = {"status": status, "detail": detail[:500], "at": time.strftime("%H:%M")}

    # ── shown to the model and the user ──────────────────────
    def is_empty(self) -> bool:
        return not (self.goal or self.constraints or self.plan or self.decisions or self.files)

    def render(self) -> str:
        """The ledger as the model sees it after a fold. Constraints are quoted, never reworded."""
        if self.is_empty():
            return ""
        lines = ["<keelson-task-ledger>",
                 "Kept by Keelson across context folds. The user's constraints are quoted exactly; they still apply."]
        if self.goal:
            lines += ["", "Goal (the request that started this work):", _quote(self.goal)]
        if self.constraints:
            lines += ["", "User's constraints (verbatim):"]
            lines += [f"- \"{c['text']}\"" for c in self.constraints]
        if self.plan:
            marks = {"done": "[x]", "in_progress": "[~]", "blocked": "[!]", "pending": "[ ]"}
            lines += ["", "Plan:"] + [f"{marks.get(s['status'], '[ ]')} {s['step']}" for s in self.plan]
        if self.decisions:
            lines += ["", "Decisions:"] + [f"- {d}" for d in self.decisions]
        if self.files:
            shown = self.files[-40:]
            more = len(self.files) - len(shown)
            lines += ["", "Files changed so far: " + ", ".join(shown) + (f" (+{more} earlier)" if more else "")]
        if self.last_verify:
            v = self.last_verify
            lines += ["", f"Last verification ({v['at']}): {v['status']} — {v['detail']}"]
        if self.scope:
            lines += ["", "Scope of the current request: " + ", ".join(self.scope)]
        lines.append("</keelson-task-ledger>")
        return "\n".join(lines)

    def kept_summary(self) -> str:
        """One line for the user when the context is folded: what survived."""
        parts = []
        if self.goal:
            parts.append("the goal")
        if self.constraints:
            parts.append(f"{len(self.constraints)} constraint(s) verbatim")
        if self.plan:
            done = sum(1 for s in self.plan if s["status"] == "done")
            parts.append(f"the plan ({done}/{len(self.plan)} done)")
        if self.decisions:
            parts.append(f"{len(self.decisions)} decision(s)")
        if self.files:
            parts.append(f"{len(self.files)} changed file(s)")
        return ", ".join(parts)

    # ── storage ──────────────────────────────────────────────
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> Ledger:
        ledger = cls()
        for key, value in (data or {}).items():
            if hasattr(ledger, key):
                setattr(ledger, key, value)
        return ledger


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in text.splitlines() or [""])


def ledger_file(session_id: str, cwd: Path | None = None) -> Path:
    return paths.project_state_dir(cwd) / "ledger" / f"{session_id}.json"


def save(ledger: Ledger, session_id: str) -> None:
    ledger.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
    target = ledger_file(session_id)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def load(session_id: str) -> Ledger | None:
    try:
        return Ledger.from_dict(json.loads(ledger_file(session_id).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def append_to_keelson_md(constraints: list[str], cwd: Path | None = None) -> Path:
    """Write rules the user repeated into KEELSON.md (only with their OK; see main.py)."""
    md = (cwd or Path.cwd()) / "KEELSON.md"
    text = md.read_text(encoding="utf-8") if md.exists() else "# KEELSON.md\n\nNotes for Keelson about this project.\n"
    new = [c for c in constraints if c not in text]
    if not new:
        return md
    block = "\n".join(f"- {c}" for c in new)
    lines = text.splitlines()
    at = next((i for i, line in enumerate(lines) if line.strip().lower() in ("## rules", "## 规则")), None)
    if at is None:
        text = text.rstrip("\n") + "\n\n## Rules\n\n" + block + "\n"
    else:
        # After the rules already there: the end of the section (next heading) or of the file.
        end = next((j for j in range(at + 1, len(lines)) if lines[j].startswith("#")), len(lines))
        while end > at + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = ([""] if end == at + 1 else []) + block.splitlines()
        text = "\n".join(lines) + "\n"
    md.write_text(text, encoding="utf-8")
    return md

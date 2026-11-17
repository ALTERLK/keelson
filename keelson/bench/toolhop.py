"""ToolHop dataset adapter.

ToolHop (https://arxiv.org/abs/2501.02506) is a multi-hop tool-calling benchmark.
Each task is self-contained:

* ``question``      — a natural-language question requiring several dependent tool calls
* ``answer``        — the gold answer
* ``answer_type``   — ``number`` | ``date`` | ``datetime`` | ``string`` | ``letter`` | ``character``
* ``sub_task``      — ordered {sub-question: sub-answer} chain (used for hop-depth stratification)
* ``tools``         — {sub-question: JSON-Schema tool spec}
* ``functions``     — executable Python source, one ``def`` per tool

The adapter turns a task into (Anthropic tool schemas, {name: callable}) so the
harness can run it with real tool execution and no network.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import unicodedata
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Tool bodies that touch the outside world or defeat determinism. ToolHop has a
# handful; we drop them from the pool rather than sandbox them.
_UNSAFE = re.compile(
    r"\b(?:open|exec|eval|compile|__import__|input)\s*\(|"
    r"\b(?:requests|urllib|httpx|socket|subprocess|shutil)\b|"
    r"\bos\.(?:system|environ|popen|remove|unlink|rmdir)\b"
)


@dataclass
class ToolHopTask:
    id: int
    question: str
    answer: str
    answer_type: str
    sub_task: dict[str, str]
    tool_specs: list[dict[str, Any]] = field(default_factory=list)
    function_src: list[str] = field(default_factory=list)
    domain: str = ""

    @property
    def hops(self) -> int:
        return len(self.sub_task)

    @property
    def is_safe(self) -> bool:
        return not any(_UNSAFE.search(src) for src in self.function_src)


def load_toolhop(path: str | Path) -> list[ToolHopTask]:
    import json

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks: list[ToolHopTask] = []
    for row in raw:
        tasks.append(
            ToolHopTask(
                id=int(row["id"]),
                question=row["question"],
                answer=str(row["answer"]),
                answer_type=row.get("answer_type", "string"),
                sub_task=dict(row.get("sub_task", {})),
                tool_specs=list(row.get("tools", {}).values()),
                function_src=list(row.get("functions", [])),
                domain=row.get("domain", ""),
            )
        )
    return tasks


def default_dataset_path() -> Path:
    """Full dataset if fetched, else the committed 120-task subset."""
    root = Path(__file__).resolve().parents[2]
    full = root / "data" / "ToolHop" / "ToolHop.json"
    if full.is_file():
        return full
    return root / "bench" / "toolhop_subset.json"


def to_tool_defs(task: ToolHopTask) -> list[dict[str, Any]]:
    """Map ToolHop tool specs onto the Anthropic tool-schema shape the harness uses."""
    defs: list[dict[str, Any]] = []
    for spec in task.tool_specs:
        params = spec.get("parameters") or {"type": "object", "properties": {}}
        defs.append(
            {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "input_schema": params,
            }
        )
    return defs


def _defs_only(src: str) -> str:
    """Keep only imports and function defs.

    Every ToolHop ``functions`` entry ends with an "Example usage" block: a
    top-level call plus an ``assert`` (the dataset's own self-check). Running
    it at import time prints, trips locale/date asserts and pollutes stdout,
    so we parse the source and drop everything that is not an import or def.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(src)
    except SyntaxError:
        return src
    keep = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return ast.unparse(ast.Module(body=keep, type_ignores=[]))


def build_callables(task: ToolHopTask) -> dict[str, Callable[..., Any]]:
    """Exec the task's function sources in one shared namespace and return {name: fn}.

    The functions are pure computation over stdlib / common date libraries;
    they share one namespace so cross-references resolve. Dataset self-test
    blocks are stripped first (see :func:`_defs_only`).
    """
    ns: dict[str, Any] = {}
    src = "\n\n".join(_defs_only(chunk) for chunk in task.function_src)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # dataset sources have invalid \d escapes etc.
        exec(compile(src, f"<toolhop:{task.id}>", "exec"), ns)  # noqa: S102 - dataset-provided pure fns
    wanted = {spec["name"] for spec in task.tool_specs}
    return {name: obj for name, obj in ns.items() if name in wanted and callable(obj)}


def make_tool_runner(task: ToolHopTask) -> Callable[[str, dict], str]:
    """Return an ``external_tools``-style dispatcher: (name, input_dict) -> result string."""
    import json

    fns = build_callables(task)

    def _run(name: str, inp: dict) -> str:
        fn = fns.get(name)
        if fn is None:
            return f"Error: no such tool {name!r}"
        # ToolHop tool bodies print debug lines; keep them off our stdout.
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                out = fn(**(inp or {}))
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except Exception as e:  # dataset fns raise plain exceptions for bad inputs
            return f"Error: {type(e).__name__}: {e}"
        try:
            return json.dumps(out, ensure_ascii=False, default=str)
        except Exception:
            return str(out)

    return _run


# ----------------------------------------------------------------- scoring ---

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _as_number(s: str) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


_DATE_FMTS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y", "%B %d %Y",
    "%d %b %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y",
)


def _as_date(s: str) -> str | None:
    raw = str(s).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    return m.group(0) if m else None


def score(task: ToolHopTask, prediction: str | None, *, num_tol: float = 1e-6) -> bool:
    """Gold-answer match, normalized per ``answer_type``. Returns pass/fail."""
    if prediction is None:
        return False
    gold, pred = task.answer, str(prediction)
    kind = task.answer_type

    if kind == "number":
        g, p = _as_number(gold), _as_number(pred)
        if g is None or p is None:
            return _norm_text(gold) == _norm_text(pred)
        return abs(g - p) <= max(num_tol, abs(g) * num_tol)

    if kind in ("date", "datetime"):
        g, p = _as_date(gold), _as_date(pred)
        if g and p:
            return g == p
        # Only one side parsed as a date: require exact normalized equality,
        # never a substring (answering "1959" for "1959-01-01" is wrong).
        return _norm_text(gold) == _norm_text(pred)

    if kind in ("letter", "character"):
        gn = _norm_text(gold).replace(" ", "")
        pn = _norm_text(pred)
        if pn.replace(" ", "") == gn:
            return True
        # tolerate a framed answer like "the letter is k"
        toks = pn.split()
        return bool(toks) and toks[-1] == gn

    # string: exact after normalization, or gold appears as a standalone answer in pred
    gn, pn = _norm_text(gold), _norm_text(pred)
    if gn == pn:
        return True
    # digit-only strings (e.g. binary answers): compare value, tolerate leading zeros
    g_digits, p_digits = gn.replace(' ', ''), pn.replace(' ', '')
    if g_digits.isdigit() and p_digits.isdigit() and g_digits.lstrip('0') == p_digits.lstrip('0'):
        return True
    return bool(re.search(rf"(^|\W){re.escape(gn)}($|\W)", pn))

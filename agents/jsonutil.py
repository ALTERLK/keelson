"""Reading JSON that a model wrote.

The runtime asks side models for small JSON objects (skill candidates, merge
decisions, folded context). Two things go wrong in practice:

* the object is wrapped in prose or a code fence;
* the transport re-chunks the reply and leaves a raw line break in the middle
  of a string — sometimes inside a key (``{"action\\n": "add"}``). Seen on an
  OpenAI-compatible gateway in non-streaming mode.

A raw newline inside a string literal is never valid JSON (a real one is
written ``\\n``), so removing it cannot change the meaning of a valid document.
That repair is only tried after the strict parse has failed.
"""

from __future__ import annotations

import json
from typing import Any


def drop_raw_newlines_in_strings(text: str) -> str:
    """Remove literal CR/LF characters that sit inside JSON string literals."""
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch in "\r\n":
                continue
        elif ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def loads_lenient(text: str) -> Any:
    """``json.loads``, then once more with raw newlines inside strings removed."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(drop_raw_newlines_in_strings(text))


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model reply, or ``{}`` if there is none."""
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])  # drop prose or a code fence around the object
    for candidate in candidates:
        try:
            parsed = loads_lenient(candidate)
        except json.JSONDecodeError:
            continue
        return parsed if isinstance(parsed, dict) else {}
    return {}

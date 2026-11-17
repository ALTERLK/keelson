"""Shared YAML frontmatter parser for memory and skills files.

Handles ``key: value`` pairs between ``---`` delimiters, plus ``|`` / ``>`` block
scalars (indented continuation lines). This is a deliberately tiny subset of
YAML — enough for the frontmatter Keelson actually writes and reads.
"""

from dataclasses import dataclass, field


@dataclass
class FrontmatterResult:
    meta: dict[str, str] = field(default_factory=dict)
    body: str = ""


def _dedent_block(raw_lines: list[str]) -> list[str]:
    indents = [len(ln) - len(ln.lstrip()) for ln in raw_lines if ln.strip()]
    pad = min(indents) if indents else 0
    return [ln[pad:] if len(ln) >= pad else ln for ln in raw_lines]


def parse_frontmatter(content: str) -> FrontmatterResult:
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return FrontmatterResult(body=content)

    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return FrontmatterResult(body=content)

    meta: dict[str, str] = {}
    i = 1
    while i < end_idx:
        line = lines[i]
        colon_idx = line.find(":")
        if colon_idx == -1:
            i += 1
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()
        if not key:
            i += 1
            continue

        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            fold = value[0] == ">"
            block: list[str] = []
            j = i + 1
            while j < end_idx:
                nxt = lines[j]
                if nxt.strip() and not nxt.startswith((" ", "\t")):
                    break
                block.append(nxt)
                j += 1
            block = _dedent_block(block)
            while block and not block[-1].strip():
                block.pop()
            joined = " ".join(s.strip() for s in block) if fold else "\n".join(block)
            meta[key] = joined.strip()
            i = j
            continue

        meta[key] = value
        i += 1

    body = "\n".join(lines[end_idx + 1:]).strip()
    return FrontmatterResult(meta=meta, body=body)


def format_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if "\n" in str(value):
            lines.append(f"{key}: |")
            lines.extend(f"  {ln}" for ln in str(value).split("\n"))
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)

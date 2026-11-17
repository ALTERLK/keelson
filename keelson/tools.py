#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import codecs
import json
import locale
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from keelson import checkpoints, events, paths, safety, search, textio
from keelson.memory import get_memory_dir
from keelson.platforms import IS_WINDOWS as IS_WIN
from keelson.platforms import kill_process_tree, no_window_flags, trash
from keelson.platforms import shell as platform_shell

ToolDef = dict  # Anthropic tool schema dict
#权限模式
PermissionMode = str  # "default" | "plan" | "acceptEdits" | "bypassPermissions" | "dontAsk"

READ_TOOLS = {"read_file", "list_files", "grep_search", "compact_context"}
EDIT_TOOLS = {"write_file", "edit_file", "delete_files", "skill_evolve", "skill_create"}


#并发安全的工具可以并行运行（只读，无副作用）
CONCURRENCY_SAFE_TOOLS = {"read_file", "list_files", "grep_search"}







# 注意：get_active_tool_definitions 的真正定义在本文件下方（紧邻 _activated_tools），
# 此处原有一份重复定义会被下方那份遮蔽，已删除。

#工具定义
tool_definitions: list[ToolDef] = [
    {
        "name": "read_file",
        "description": "Read a text file (UTF-8 or GBK). Returns lines with line numbers, at most 2000 lines per call; use offset and limit for longer files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to read"},
                "offset": {"type": "integer", "description": "Line number to start from (1-based). Default 1."},
                "limit": {"type": "integer", "description": "How many lines to return. Default 2000."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation) and be unique unless replace_all is true. The file's encoding and line endings are kept.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to edit"},
                "old_string": {"type": "string", "description": "The exact string to find and replace"},
                "new_string": {"type": "string", "description": "The string to replace it with"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring a unique match. Default false."},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "delete_files",
        "description": "Delete files or folders by moving them to the Recycle Bin (Trash), so the deletion can be undone. "
                       "This is the only way to delete: rm, del, Remove-Item and the like are refused in run_shell. "
                       "Glob patterns such as build/*.tmp are expanded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Files, folders or glob patterns to delete"},
                "reason": {"type": "string", "description": "Why these should be deleted (shown to the user)"},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "list_files",
        "description": "List files matching a glob pattern (supports **, *, ?, [abc], {a,b}), most recently changed first. Files ignored by .gitignore are skipped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": 'Glob pattern to match files (e.g., "**/*.ts", "src/**/*")'},
                "path": {"type": "string", "description": "Base directory to search from. Defaults to current directory."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search file contents with a regular expression. Returns matching lines as path:line:text. Files ignored by .gitignore and binary files are skipped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in. Defaults to current directory."},
                "include": {"type": "string", "description": 'File glob pattern to include (e.g., "*.ts", "*.{ts,tsx}", "src/**/*.py")'},
                "case_insensitive": {"type": "boolean", "description": "Ignore case. Default false."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Execute a shell command and return its output. Use this for running tests, builds, git operations, etc. Changes it makes inside the project are covered by checkpoints (/undo). Deleting is refused here: use delete_files. Commands that cannot be undone (installing packages, network access, git push, writing outside the project) are shown to the user first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in milliseconds (default: 30000)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "skill",
        "description": "Invoke a registered skill by name. Skills are prompt templates loaded from .keelson/skills/. Returns the skill's resolved prompt to follow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The name of the skill to invoke"},
                "args": {"type": "string", "description": "Optional arguments to pass to the skill"},
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "compact_context",
        "description": "Compact the current conversation context into structured session memory when the context is long, tool results are noisy, or a strategy reset is useful. This preserves task progress, current next steps, and tool-use experience, then continues from the folded memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for compacting now, such as long context, many tool calls, repeated failures, or strategy change.",
                },
            },
        },
    },
    {
        "name": "skill_evolve",
        "description": "Persist an explicit reusable user correction or workflow preference into an existing skill. Creates a version snapshot before editing the skill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The registered skill name to evolve"},
                "lesson": {"type": "string", "description": "Durable reusable rule to add to the skill"},
                "rationale": {"type": "string", "description": "Why this lesson should affect future similar tasks"},
                "target": {
                    "type": "string",
                    "enum": ["active", "project", "user"],
                    "description": "Which skill file to update. Defaults to active.",
                },
            },
            "required": ["skill_name", "lesson"],
        },
    },
    {
        "name": "skill_create",
        "description": "Create a new reusable skill from explicit durable workflow guidance when no suitable existing skill exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Concise reusable skill name"},
                "description": {"type": "string", "description": "One-sentence description of what the skill does and when to use it"},
                "instructions": {"type": "string", "description": "Reusable SKILL.md body. Focus on durable method, constraints, and workflow, not one-off task content."},
                "when_to_use": {"type": "string", "description": "Trigger condition for auto-invocation"},
                "target": {
                    "type": "string",
                    "enum": ["project", "user"],
                    "description": "Where to create the skill. Defaults to project.",
                },
                "context": {
                    "type": "string",
                    "enum": ["inline", "fork"],
                    "description": "Skill execution mode. Defaults to inline.",
                },
                "user_invocable": {"type": "boolean", "description": "Whether users can invoke it manually with /<skill>. Defaults to false."},
                "allowed_tools": {"type": "string", "description": "Optional comma-separated allowed tools for fork mode"},
                "evidence": {"type": "string", "description": "Short user-provided evidence showing why this is reusable"},
            },
            "required": ["name", "description", "instructions"],
        },
    },
    {
        "name": "enter_plan_mode",
        "description": "Enter plan mode to switch to a read-only planning phase. In plan mode, you can only read files and write to the plan file.",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    {
        "name": "exit_plan_mode",
        "description": "Exit plan mode after you have finished writing your plan to the plan file.",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    {
        "name": "agent",
        "description": "Launch a sub-agent to handle a task autonomously. Sub-agents have isolated context and return their result. Types: 'explore' (read-only), 'plan' (read-only, structured planning), 'general' (full tools).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short (3-5 word) description of the sub-agent's task"},
                "prompt": {"type": "string", "description": "Detailed task instructions for the sub-agent"},
                "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type. Default: general"},
            },
            "required": ["description", "prompt"],
        },
    },
    # ─── Tool search (deferred tool loader) ─────────────────────
    {
        "name": "tool_search",
        "description": "Search for available tools by name or keyword. Returns full schema definitions for matching deferred tools so you can use them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tool name or search keywords"},
            },
            "required": ["query"],
        },
    },
]





#----------------------工具调用----------------------------

def _resolve_tool_path(raw_path: str, *, must_exist: bool = True) -> Path:
    """The path as given, or, for an absolute path from another machine (say
    ``/workspace/proj/src/a.py``), the same tail under the working folder.

    A path to a new file is only remapped when no folder above it exists (other
    than the drive root): ``C:\\work\\new\\x.py`` is written there, never
    redirected into the working folder.
    """
    path = Path(raw_path)
    if path.exists() or not path.is_absolute():
        return path
    if not must_exist and any(parent.exists() for parent in list(path.parents)[:-1]):
        return path

    parts = path.parts
    cwd = Path.cwd()
    for i in range(1, len(parts)):
        candidate = cwd.joinpath(*parts[i:])
        if must_exist and candidate.exists():
            return candidate
        if not must_exist and candidate.parent.exists():
            return candidate

    return path


READ_DEFAULT_LIMIT = 2000      # lines returned when no limit is given
READ_MAX_LINE_CHARS = 2000     # a minified line is cut, not returned whole


def _as_positive_int(value, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


#读取文件并且在读取文件的基础上添加行号
def _read_file(inp:dict) -> str:
    try:
        path = _resolve_tool_path(inp["file_path"])
        if path.is_dir():
            return f"Error reading file: {inp['file_path']} is a directory. Use list_files to see what is in it."
        try:
            tf = textio.read(path)
        except textio.NotTextError as e:
            return f"Error reading file: {e}. read_file only reads text; use run_shell for other files."
        lines = tf.lines
        total = len(lines)
        start = _as_positive_int(inp.get("offset"), 1)
        limit = _as_positive_int(inp.get("limit"), READ_DEFAULT_LIMIT)
        if start > total:
            return f"Error reading file: {inp['file_path']} has {total} lines; offset {start} is past the end."
        end = min(total, start + limit - 1)
        shown = []
        for number in range(start, end + 1):
            line = lines[number - 1]
            if len(line) > READ_MAX_LINE_CHARS:
                line = line[:READ_MAX_LINE_CHARS] + f" … [line cut: {len(line)} chars]"
            shown.append(f"{number:4d} | {line}")
        out = "\n".join(shown)
        notes = []
        if start > 1 or end < total:
            notes.append(f"showing lines {start}-{end} of {total}; pass offset/limit to read more")
        kind = textio.describe(tf)
        if kind:
            notes.append(f"{kind}; edits keep it that way")
        if notes:
            out += "\n\n[" + "; ".join(notes) + "]"
        return out
    except Exception as e:
        return f"Error reading file: {e}"

def _write_file(inp:dict) -> str:
    try:
        path = _resolve_tool_path(inp["file_path"], must_exist=False)
        if path.is_dir():
            return f"Error writing file: {inp['file_path']} is a directory."
        like = None
        if path.exists():
            try:
                like = textio.read(path)   # keep its encoding, BOM and line endings
            except textio.NotTextError:
                like = None                # replacing a binary file: written as new UTF-8 text
        content = inp["content"]
        checkpoints.before_write(path)
        try:
            textio.write(path, like.to_file_newlines(content) if like else content, like)
        except UnicodeEncodeError as e:
            bad = content[e.start:e.end]
            return (f"Error writing file: {inp['file_path']} is {textio.describe(like) or like.encoding} and cannot hold "
                    f"{bad!r}. Use characters that encoding has, or ask the user before converting the file.")
        _auto_update_memory_index(str(path))
        lines = content.split("\n")
        line_count = len(lines)
        preview = "\n".join(f"{i + 1:4d} | {l}" for i, l in enumerate(lines[:30]))
        trunc = f"\n  ... ({line_count} lines total)" if line_count > 30 else ""
        return f"Successfully wrote to {inp['file_path']} ({line_count} lines)\n\n{preview}{trunc}"
    except Exception as e:
        return f"Error writing file: {e}"

def _auto_update_memory_index(file_path:str) -> None:
    try:
        mem_dir = str(get_memory_dir())
        if file_path.startswith(mem_dir) and file_path.endswith(".md") and not file_path.endswith("MEMORY.md"):
            mem_path = Path(mem_dir)
            lines = ["# Memory Index", ""]
            for f in sorted(mem_path.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                try:
                    raw = f.read_text(encoding="utf-8")
                    name_match = re.search(r"^name:\s*(.+)$", raw, re.MULTILINE)
                    type_match = re.search(r"^type:\s*(.+)$", raw, re.MULTILINE)
                    desc_match = re.search(r"^description:\s*(.+)$", raw, re.MULTILINE)
                    if name_match and type_match:
                        n = name_match.group(1).strip()
                        t = type_match.group(1).strip()
                        d = desc_match.group(1).strip()
                        lines.append(f"- **[{n}]({f.name})** ({t}) — {d}")
                except Exception:
                    pass
            (mem_path / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    except Exception:
        pass

#-------------------------编辑助手，符号规范化+差异化------------------------

#将各种Unicode引号字符统一转换为标准的ASCII直引号。
def _normalize_quotes(s: str) -> str:
    s = re.sub("[\u2018\u2019\u2032]", "'", s)
    s = re.sub('[\u201c\u201d\u2033]', '"', s)
    return s

#查询匹配到的字符串
def _find_actual_string(file_content: str, search_string: str) -> str | None:
    if search_string in file_content:
        return search_string
    norm_search = _normalize_quotes(search_string)
    norm_file = _normalize_quotes(file_content)
    idx = norm_file.find(norm_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    return None

DIFF_MAX_LINES = 80


#生成差异（unified diff，带两行上下文）
def _generate_diff(old_content: str, new_content: str) -> str:
    import difflib

    old_lines = old_content.replace("\r\n", "\n").split("\n")
    new_lines = new_content.replace("\r\n", "\n").split("\n")
    lines = list(difflib.unified_diff(old_lines, new_lines, n=2, lineterm=""))[2:]  # drop the ---/+++ header
    if len(lines) > DIFF_MAX_LINES:
        lines = lines[:DIFF_MAX_LINES] + [f"... ({len(lines) - DIFF_MAX_LINES} more diff lines)"]
    return "\n".join(lines)


def _line_numbers_of(text: str, needle: str, limit: int = 10) -> list[int]:
    numbers, start = [], 0
    while len(numbers) < limit:
        idx = text.find(needle, start)
        if idx < 0:
            break
        numbers.append(text.count("\n", 0, idx) + 1)
        start = idx + max(1, len(needle))
    return numbers


def _locate(tf: textio.TextFile, old: str) -> str | None:
    """old_string as it actually appears in the file: line endings and quotes adjusted."""
    lf = old.replace("\r\n", "\n")
    crlf = lf.replace("\n", "\r\n")
    if tf.newline == "\r\n" and not tf.mixed_newlines:
        candidates = [crlf, old]
    else:
        candidates = [old, lf, crlf]
    for candidate in dict.fromkeys(candidates):
        actual = _find_actual_string(tf.text, candidate)
        if actual:
            return actual
    return None


#编辑文件
def _edit_file(inp: dict) -> str:
    try:
        old, new = inp["old_string"], inp["new_string"]
        replace_all = bool(inp.get("replace_all"))
        path = _resolve_tool_path(inp["file_path"], must_exist=not old == "")
        if old == "":
            if path.exists():
                return (f"Error: old_string is empty, but {inp['file_path']} already exists. Give the text to "
                        "replace, or use write_file to replace the whole file.")
            return _write_file({"file_path": inp["file_path"], "content": new})
        if old == new:
            return "Error: old_string and new_string are the same; nothing to change."
        try:
            tf = textio.read(path)
        except textio.NotTextError as e:
            return f"Error editing file: {e}. Not editing it, so no bytes are damaged."

        actual = _locate(tf, old)
        if not actual:
            return (f"Error: old_string not found in {inp['file_path']}. It must match the file exactly, including "
                    "whitespace and indentation; read_file it again and copy the lines.")

        occurrences = tf.text.count(actual)
        if occurrences > 1 and not replace_all:
            where = ", ".join(map(str, _line_numbers_of(tf.text, actual)))
            return (f"Error: old_string found {occurrences} times in {inp['file_path']} (lines {where}). "
                    "Add surrounding lines to make it unique, or pass replace_all: true to change every one.")

        uses_crlf = "\r\n" in actual or (tf.newline == "\r\n" and not tf.mixed_newlines)
        lf_new = new.replace("\r\n", "\n")
        replacement = lf_new.replace("\n", "\r\n") if uses_crlf else lf_new
        new_text = tf.text.replace(actual, replacement) if replace_all else tf.text.replace(actual, replacement, 1)
        checkpoints.before_write(path)
        try:
            textio.write(path, new_text, tf)
        except UnicodeEncodeError as e:
            bad = new_text[e.start:e.end]
            return (f"Error editing file: {inp['file_path']} is {textio.describe(tf) or tf.encoding} and cannot hold "
                    f"{bad!r}. Use characters that encoding has, or ask the user before converting the file.")
        _auto_update_memory_index(str(path))

        notes = []
        if actual.replace("\r\n", "\n") != old.replace("\r\n", "\n"):
            notes.append("matched via quote normalization")
        if replace_all and occurrences > 1:
            notes.append(f"{occurrences} occurrences replaced")
        note = f" ({'; '.join(notes)})" if notes else ""
        return f"Successfully edited {inp['file_path']}{note}\n\n{_generate_diff(tf.text, new_text)}"
    except Exception as e:
        return f"Error editing file: {e}"


DELETE_MAX_MATCHES = 500


def _delete_targets(inp: dict) -> list[str]:
    raw = inp.get("paths")
    if raw is None:
        raw = inp.get("path") or inp.get("file_path") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(p) for p in raw if str(p).strip()]


def _expand_delete_target(raw: str) -> tuple[list[Path], str | None]:
    """The paths one entry stands for (a glob may stand for many), or why it is skipped."""
    text, unresolved = safety.expand(raw.strip())
    if unresolved:
        return [], f"skipped {raw}: {', '.join(unresolved)} is not set, so the path is unknown"
    path = _resolve_tool_path(text)
    if path.exists() or path.is_symlink():
        return [path], None
    if any(ch in text for ch in "*?["):
        import glob as _glob

        matches = sorted(_glob.glob(text, recursive=True))
        if len(matches) > DELETE_MAX_MATCHES:
            return [], f"skipped {raw}: matches {len(matches)} paths; narrow the pattern"
        if matches:
            return [Path(m) for m in matches], None
    return [], f"not found: {raw}"


def _delete_files(inp: dict) -> str:
    targets = _delete_targets(inp)
    if not targets:
        return "Error: give the files or folders to delete in `paths`."
    bin_name = trash.bin_name()
    moved, lines = 0, []
    for raw in targets:
        found, problem = _expand_delete_target(raw)
        if problem:
            lines.append(problem)
        for path in found:
            why = safety.describe_protected(path, destructive=True)
            if why:
                lines.append(f"refused: {path} is {why}")
                continue
            if path.is_file():
                checkpoints.before_write(path)    # /undo can put it back even if it is git-ignored
            try:
                trash.move_to_trash(path)
            except Exception as e:  # never fall back to a real delete
                lines.append(f"not deleted: {path} ({e})")
                continue
            moved += 1
            lines.append(f"moved to {bin_name}: {path}")
    head = (f"Deleted {moved} item(s) by moving them to {bin_name}; /undo or {bin_name} brings them back."
            if moved else "Nothing was deleted.")
    return head + "\n" + "\n".join(lines)


def _list_files(inp: dict) -> str:
    try:
        base = _resolve_tool_path(inp.get("path") or ".")
        if not base.exists():
            return f"Error listing files: {inp.get('path')} does not exist."
        return search.list_files(inp["pattern"], base)
    except Exception as e:
        return f"Error listing files: {e}"


def _grep_search(inp: dict) -> str:
    try:
        base = _resolve_tool_path(inp.get("path") or ".")
        return search.grep(inp["pattern"], base, inp.get("include"), bool(inp.get("case_insensitive")))
    except Exception as e:
        return f"Error searching: {e}"



#--------截断过长的工具调用结果

MAX_RESULT_CHARS = 50000


def _truncate_result(result:str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        +f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        +result[-keep_each:]
    )






#----------延迟工具激活---------------------------
_activated_tools: set[str] = set()



def reset_activated_tools() -> None:
    _activated_tools.clear()

def get_active_tool_definitions(all_tools: list[ToolDef] | None = None) -> list[ToolDef]:
    tools = all_tools if all_tools is not None else tool_definitions
    return [
        {k: v for k, v in t.items() if k != "deferred"}
        for t in tools
        if not t.get("deferred") or t["name"] in _activated_tools
    ]

def get_deferred_tool_names(all_tools: list[ToolDef] | None = None) -> list[str]:
    tools = all_tools if all_tools is not None else tool_definitions
    return [t["name"] for t in tools if t.get("deferred") and t["name"] not in _activated_tools]

#执行shell命令
#
# 多行命令。POSIX 的 `sh -c` 会把每一行都执行；Windows 的 `cmd /c` 只执行第一行，
# 其余的静默丢弃——模型以为跑了三条命令，其实只跑了一条（bench/casestudy 第 1、2 轮
# 里反复出现）。所以在 Windows 上，多行命令写成一个临时批处理文件再执行。

def _multiline_quote_problem(command: str, *, windows: bool = IS_WIN) -> str | None:
    """Why this command cannot be run as written, or None.

    cmd.exe has no way to continue a quoted string on the next line, so
    ``python -c "`` followed by a script over several lines never reaches
    Python as one argument. Refusing with the reason is better than running
    something else and printing nothing.
    """
    if not windows:
        return None
    lines = command.strip().splitlines()
    if len(lines) < 2:
        return None
    for number, line in enumerate(lines, 1):
        if line.count('"') % 2:
            return (
                "Command not run. This shell is cmd.exe on Windows, where a quoted string cannot continue on the "
                f"next line (line {number} has an unclosed double quote). Put the script on one line, for example "
                "python -c \"exec('first\\nsecond')\", or run a file that already exists."
            )
    return None


def _batch_line(line: str) -> str:
    """One line of a batch file. A batch script that starts another one without
    ``call`` never returns to it, so later lines would be skipped."""
    words = line.split()
    if not words or words[0].lower() in {"call", "rem"} or words[0].startswith(("@", ":", "(", ")")):
        return line
    target = shutil.which(words[0]) or ""
    return f"call {line}" if target.lower().endswith((".cmd", ".bat")) else line


def _prepare_shell_command(command: str, *, windows: bool = IS_WIN) -> tuple[str, Path | None]:
    """What to hand the platform shell, and a temporary file to delete afterwards."""
    if not windows or "\n" not in command.strip():
        return command, None
    lines = [_batch_line(line) for line in command.strip().splitlines()]
    handle, name = tempfile.mkstemp(prefix="keelson-", suffix=".cmd")
    encoding = "oem" if IS_WIN else "utf-8"       # the code page cmd.exe reads batch files in
    with os.fdopen(handle, "w", encoding=encoding, errors="replace", newline="\r\n") as script:
        script.write("@echo off\n" + "\n".join(lines) + "\n")
    return f'"{name}"', Path(name)


def _discard(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass


def _shell_invocation(command: str) -> tuple[str | list[str] | None, Path | None, str | None, platform_shell.Shell]:
    """(what to run, temp file to delete, or the reason it is not run, the shell)."""
    shell = platform_shell.current()
    if shell.is_powershell:
        problem = platform_shell.preflight(command, shell)
        if problem:
            return None, None, problem, shell
        script = platform_shell.write_powershell_script(command)
        return platform_shell.powershell_argv(shell, script), script, None, shell
    if shell.kind == "cmd":
        problem = _multiline_quote_problem(command, windows=True)
        if problem:
            return None, None, problem, shell
        prepared, script = _prepare_shell_command(command, windows=True)
        return prepared, script, None, shell
    return command, None, None, shell


def _output_encoding(shell: platform_shell.Shell) -> str:
    # PowerShell is told to write UTF-8 (see the prelude); cmd and sh write the locale's encoding.
    return "utf-8" if shell.is_powershell else (locale.getpreferredencoding(False) or "utf-8")


def _finish_output(text: str, shell: platform_shell.Shell, script: Path | None) -> str:
    text = text.replace("\r\n", "\n")
    if shell.is_powershell and script is not None:
        text = platform_shell.tidy_powershell_errors(text, script)
    return text


def _run_shell(inp: dict) -> str:
    target, script, problem, shell = _shell_invocation(inp["command"])
    if problem:
        return problem
    try:
        timeout_ms = inp.get("timeout", 30000)
        result = subprocess.run(
            target,
            shell=isinstance(target, str),
            capture_output=True,
            timeout=timeout_ms / 1000,
            env={**platform_shell.child_env(), **(inp.get("env") or {})},
            **no_window_flags(),
        )
        encoding = _output_encoding(shell)
        stdout = _finish_output((result.stdout or b"").decode(encoding, "replace"), shell, script)
        stderr = _finish_output((result.stderr or b"").decode(encoding, "replace"), shell, script)
        return _format_shell_result(result.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {inp.get('timeout', 30000)}ms"
    except Exception as e:
        return f"Error: {e}"
    finally:
        _discard(script)


def _format_shell_result(returncode: int, stdout: str, stderr: str) -> str:
    """The text the model sees. Kept identical for the blocking and async paths."""
    if returncode != 0:
        err = f"\nStderr: {stderr}" if stderr else ""
        out = f"\nStdout: {stdout}" if stdout else ""
        return f"Command failed (exit code {returncode}){out}{err}"
    return stdout or "(no output)"


_kill_process_tree = kill_process_tree


class _OutputRelay:
    """Batches a command's live output into ``tool_output`` events.

    A chatty command can print thousands of lines a second; publishing each
    chunk would flood every subscriber. Chunks are coalesced for a short
    interval, and the live view is capped — the full output still reaches the
    model through the tool result.
    """

    INTERVAL_S = 0.08
    MAX_CHARS = 40_000

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink
        self._pending: list[str] = []
        self._last_flush = 0.0
        self._sent = 0
        self._capped = False

    def feed(self, text: str) -> None:
        if self._capped or not text:
            return
        self._pending.append(text)
        if time.monotonic() - self._last_flush >= self.INTERVAL_S:
            self.flush()

    def flush(self) -> None:
        if not self._pending or self._capped:
            return
        text = "".join(self._pending)
        self._pending = []
        self._last_flush = time.monotonic()
        room = self.MAX_CHARS - self._sent
        if len(text) >= room:
            text = text[:room] + "\n… (live view capped; the full output is in the result)\n"
            self._capped = True
        self._sent += len(text)
        self._sink(text)


async def run_shell_async(inp: dict, on_output: Callable[[str], None] | None = None) -> str:
    """Run a shell command without blocking the event loop.

    Compared with the blocking ``_run_shell``:
    * other tasks (the event stream, other sessions) keep running meanwhile;
    * output is relayed while the command runs, through ``on_output``;
    * a timeout or a cancelled turn kills the whole process tree.
    """
    target, script, problem, shell = _shell_invocation(inp["command"])
    if problem:
        return problem
    try:
        return await _run_prepared_async(target, inp, on_output, shell, script)
    finally:
        _discard(script)


async def _run_prepared_async(target: str | list[str], inp: dict, on_output: Callable[[str], None] | None,
                              shell: platform_shell.Shell, script: Path | None) -> str:
    timeout_ms = inp.get("timeout", 30000)
    spawn_options = dict(
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**platform_shell.child_env(), **(inp.get("env") or {})},
        **(no_window_flags() if IS_WIN else {"start_new_session": True}),
    )
    try:
        if isinstance(target, str):
            proc = await asyncio.create_subprocess_shell(target, **spawn_options)
        else:
            proc = await asyncio.create_subprocess_exec(*target, **spawn_options)
    except NotImplementedError:
        # An event loop without subprocess support (Windows selector loop).
        return await asyncio.to_thread(_run_shell, inp)
    except Exception as e:
        return f"Error: {e}"

    encoding = _output_encoding(shell)
    relay = _OutputRelay(on_output) if on_output else None
    captured: dict[str, list[str]] = {"stdout": [], "stderr": []}

    async def pump(stream: asyncio.StreamReader, name: str) -> None:
        # Incremental decoding: a multi-byte character can be split across reads.
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        while True:
            chunk = await stream.read(4096)
            text = decoder.decode(chunk, final=not chunk)
            if text:
                captured[name].append(text)
                if relay:
                    relay.feed(text)
            if not chunk:
                return

    try:
        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, "stdout"), pump(proc.stderr, "stderr"), proc.wait()),
            timeout=timeout_ms / 1000,
        )
    except TimeoutError:
        await asyncio.to_thread(_kill_process_tree, proc.pid)
        try:
            await asyncio.wait_for(proc.wait(), 5)  # reap it, so no zombie is left behind
        except (TimeoutError, ProcessLookupError):
            pass
        return f"Command timed out after {timeout_ms}ms"
    except asyncio.CancelledError:
        # The user stopped the turn: the command must not outlive it.
        await asyncio.shield(asyncio.to_thread(_kill_process_tree, proc.pid))
        raise
    finally:
        if relay:
            relay.flush()

    # Same post-processing as the blocking path, so both read alike.
    stdout = _finish_output("".join(captured["stdout"]), shell, script)
    stderr = _finish_output("".join(captured["stderr"]), shell, script)
    return _format_shell_result(proc.returncode or 0, stdout, stderr)


def _publish_tool_output(text: str) -> None:
    events.emit(events.TOOL_OUTPUT, id=events.current_call_id(), text=text)


# ─── permissions ────────────────────────────────────────────
#
# Asked by reversibility, not by tool: what /undo can take back runs without a
# prompt; what it cannot (packages, network, git push, history rewrites, paths
# outside the project, MCP and unknown tools) is confirmed with the reason;
# what could wreck the machine (deleting from the shell, touching the home
# folder, a drive root or system folders) is refused in every mode. See
# keelson/safety.py for the rules and docs/safety.md for the limits.


def is_dangerous(command: str) -> bool:
    """Kept for callers of the old API: does this command need a person's OK (or is it refused)?"""
    return safety.assess_shell(command).action != "allow"


def _parse_rule(rule: str) -> dict:
    m = re.match(r"^([A-Za-z0-9_]+)\((.+)\)$", rule.strip())
    if m:
        return {"tool": m.group(1), "pattern": m.group(2)}
    return {"tool": rule.strip(), "pattern": None}


def _load_settings(file_path: Path) -> dict | None:
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None


_cached_rules: dict | None = None


def load_permission_rules() -> dict:
    """allow/deny rules from ~/.keelson/settings.json and the project's settings files.

    A project's *allow* rules only count once the folder is trusted (see
    keelson/trust.py); its deny rules always count.
    """
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules
    from keelson import trust

    allow: list[dict] = []
    deny: list[dict] = []
    trusted = trust.is_trusted()
    sources = [(_load_settings(paths.user_settings_file()), True),
               (_load_settings(paths.project_settings_file()), trusted),
               (_load_settings(paths.project_local_settings_file()), trusted)]
    for settings, allow_counts in sources:
        if not isinstance(settings, dict) or not isinstance(settings.get("permissions"), dict):
            continue
        perms = settings["permissions"]
        if allow_counts:
            allow += [_parse_rule(r) for r in perms.get("allow", [])]
        deny += [_parse_rule(r) for r in perms.get("deny", [])]
    _cached_rules = {"allow": allow, "deny": deny}
    return _cached_rules


def _rule_values(tool_name: str, inp: dict) -> list[str] | None:
    """What a rule's pattern is matched against (None: the tool has no such value)."""
    if tool_name == "run_shell":
        command = inp.get("command", "")
        return [command, *platform_shell._segments(command)]
    if "file_path" in inp:
        return [inp["file_path"]]
    if tool_name == "delete_files":
        return _delete_targets(inp)
    return None


def _pattern_matches(pattern: str, value: str) -> bool:
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _matches_rule(rule: dict, tool_name: str, inp: dict) -> bool:
    if rule["tool"] != tool_name:
        return False
    if rule["pattern"] is None:
        return True
    values = _rule_values(tool_name, inp)
    if values is None:
        return True
    if tool_name == "delete_files":
        return all(_pattern_matches(rule["pattern"], v) for v in values)
    return any(_pattern_matches(rule["pattern"], v) for v in values)


def _check_permission_rules(tool_name: str, inp: dict, extra_allow: list[str] | None = None) -> str | None:
    rules = load_permission_rules()
    for rule in rules["deny"]:
        if _matches_rule(rule, tool_name, inp):
            return "deny"
    for rule in rules["allow"] + [_parse_rule(r) for r in extra_allow or []]:
        if _matches_rule(rule, tool_name, inp):
            return "allow"
    return None


def _rules_cover(verdict: safety.Verdict, extra_allow: list[str] | None) -> bool:
    """Do "always allow" rules cover every part of this shell command that needs an OK?"""
    if not verdict.can_always_allow:
        return False
    prefixes = [r["pattern"] for r in load_permission_rules()["allow"] + [_parse_rule(r) for r in extra_allow or []]
                if r["tool"] == "run_shell" and r["pattern"]]
    return all(any(_pattern_matches(p, key) or key.startswith(p.rstrip("*").rstrip()) for p in prefixes)
               for key in verdict.rule_keys)


def _assess(tool_name: str, inp: dict) -> safety.Verdict | None:
    if tool_name == "run_shell":
        return safety.assess_shell(inp.get("command", ""))
    if tool_name in ("write_file", "edit_file") and inp.get("file_path"):
        return safety.assess_path(_resolve_tool_path(inp["file_path"], must_exist=False), verb="write")
    if tool_name == "delete_files":
        merged = safety.Verdict(read_only=False)
        for raw in _delete_targets(inp):
            text, unresolved = safety.expand(raw)
            if unresolved:
                continue                      # the tool itself skips these and says why
            one = safety.assess_path(_resolve_tool_path(text, must_exist=False), verb="delete")
            if one.action == "block":
                merged.refuse(one.reasons[0])
                return merged
            for reason in one.reasons:
                merged.need(reason)
        return merged
    return None


def _subject(tool_name: str, inp: dict) -> str:
    if tool_name == "run_shell":
        return inp.get("command", "")
    if tool_name in ("write_file", "edit_file"):
        return f"{tool_name} {inp.get('file_path', '')}"
    if tool_name == "delete_files":
        return "delete " + ", ".join(_delete_targets(inp))
    if tool_name == "skill_evolve":
        return f"evolve skill: {inp.get('skill_name', '')}"
    if tool_name == "skill_create":
        return f"create skill: {inp.get('name', '')}"
    return f"external tool: {tool_name}"


def _shell_undo_off() -> str | None:
    store = checkpoints.active()
    if store is not None and not store.covers_shell:
        return store.reason
    return None


def _ask(mode: str, tool_name: str, inp: dict, reason: str, *, rules: list[str] | None = None) -> dict:
    subject = _subject(tool_name, inp)
    if mode == "dontAsk":
        return {"action": "deny", "message": f"Auto-denied (dontAsk mode): {subject} — {reason}"}
    return {"action": "confirm", "message": subject, "reason": reason, "rules": list(rules or [])}


_KNOWN_TOOLS = READ_TOOLS | EDIT_TOOLS | {
    "run_shell", "skill", "agent", "tool_search", "enter_plan_mode", "exit_plan_mode", "compact_context",
}


def check_permission(
    tool_name: str,
    inp: dict,
    mode: str = "default",
    plan_file_path: str | None = None,
    session_rules: list[str] | None = None,
) -> dict:
    """{"action": "allow" | "deny" | "confirm", "message", "reason", "rules"}.

    ``rules``: the "always allow" rules a person may save for this call — empty
    when it may only be allowed once (anything dangerous). ``session_rules``:
    rules allowed for the rest of this session.
    """
    verdict = _assess(tool_name, inp)
    if verdict is not None and verdict.action == "block":     # refused in every mode, bypass included
        return {"action": "deny", "message": verdict.reasons[0]}
    if mode == "bypassPermissions":
        return {"action": "allow"}

    rule_result = _check_permission_rules(tool_name, inp, session_rules)
    if rule_result == "deny":
        return {"action": "deny", "message": f"Denied by permission rule for {tool_name}"}

    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    if mode == "plan":
        if tool_name in EDIT_TOOLS:
            file_path = inp.get("file_path") or inp.get("path")
            if plan_file_path and file_path == plan_file_path:
                return {"action": "allow"}
            return {"action": "deny", "message": f"Blocked in plan mode: {tool_name}"}
        if tool_name == "run_shell":
            return {"action": "deny", "message": "Shell commands blocked in plan mode"}
        if tool_name not in _KNOWN_TOOLS:
            return {"action": "deny", "message": f"Blocked in plan mode: {tool_name}"}

    if tool_name in ("enter_plan_mode", "exit_plan_mode"):
        return {"action": "allow"}

    if verdict is not None:
        if verdict.action == "allow":
            off = _shell_undo_off() if tool_name == "run_shell" and not verdict.read_only else None
            if off and rule_result != "allow":
                return _ask(mode, tool_name, inp, off, rules=[f"run_shell({_first_word(inp)}*)"])
            return {"action": "allow"}
        if tool_name == "run_shell":
            if _rules_cover(verdict, session_rules):
                return {"action": "allow"}
            rules = [f"run_shell({key}*)" for key in verdict.rule_keys] if verdict.can_always_allow else []
        else:
            if rule_result == "allow":
                return {"action": "allow"}
            rules = []
        return _ask(mode, tool_name, inp, "; ".join(verdict.reasons), rules=rules)

    if rule_result == "allow":
        return {"action": "allow"}
    if tool_name == "skill_evolve":
        return _ask(mode, tool_name, inp, "changes a skill that later tasks will follow", rules=["skill_evolve"])
    if tool_name == "skill_create":
        return _ask(mode, tool_name, inp, "adds a skill that later tasks will follow", rules=["skill_create"])

    # MCP (`mcp__server__tool`) and anything new: Keelson cannot tell what it changes.
    if tool_name not in _KNOWN_TOOLS:
        return _ask(mode, tool_name, inp, "an external tool; Keelson cannot tell what it changes or undo it",
                    rules=[tool_name])
    return {"action": "allow"}


def _first_word(inp: dict) -> str:
    words = safety.tokens(inp.get("command", ""))
    return words[0] if words else ""


def save_project_rule(rule: str) -> Path:
    """"Always allow in this project": add ``rule`` to .keelson/settings.local.json and trust the folder."""
    from keelson import trust

    target = paths.project_local_settings_file()
    data = _load_settings(target) or {}
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if rule not in allow:
        allow.append(rule)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    trust.trust()
    reset_permission_cache()
    return target


#----------------执行工具调用-----------------------
# 'agent' 和 'skill' 这两个工具在 agent.py 中处理，以避免循环依赖。"

async def execute_tool(
    name: str, inp: dict, read_file_state: dict[str, float] | None = None
) -> str:
    if name == "read_file":
        result = await asyncio.to_thread(_read_file, inp)
        if read_file_state is not None and not result.startswith("Error"):
            abs_path = str(_resolve_tool_path(inp["file_path"]).resolve())
            try:
                read_file_state[abs_path] =  os.path.getmtime(abs_path)
            except OSError:
                pass
        return _truncate_result(result)

    if name in ("write_file", "edit_file") and read_file_state is not None:
        abs_path = str(_resolve_tool_path(inp["file_path"], must_exist=(name == "edit_file")).resolve())
        if os.path.exists(abs_path):
            if abs_path not in read_file_state:
                verb = "writing" if name == "write_file" else "editing"
                return f"Error: You must read this file before {verb}. Use read_file first to see its current contents."
            if os.path.getmtime(abs_path) != read_file_state[abs_path]:
                verb = "writing" if name == "write_file" else "editing"
                return f"Warning: {inp['file_path']} was modified externally since your last read. Please read_file again before {verb}."

    #搜索和激活延迟加载的工具。
    if name == "tool_search":
        query = (inp.get("query") or "").lower()
        deferred = [t for t in tool_definitions if t.get("deferred")]
        matches = [
            t for t in deferred
            if query in t["name"].lower() or query in (t.get("description") or "").lower()
        ]
        if not matches:
            return "No matching deferred tools found."

        for m in matches:
            _activated_tools.add(m["name"])

        return json.dumps(
            [{"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"]} for t in
             matches],
            indent=2,
        )
    if name == "skill_evolve":
        from .skills import evolve_skill

        result = evolve_skill(
            skill_name=inp.get("skill_name", ""),
            lesson=inp.get("lesson", ""),
            rationale=inp.get("rationale", ""),
            target=inp.get("target", "active"),
        )
        return _truncate_result(json.dumps(result, ensure_ascii=False, indent=2))

    if name == "skill_create":
        from .skills import create_skill

        result = create_skill(
            name=inp.get("name", ""),
            description=inp.get("description", ""),
            instructions=inp.get("instructions", ""),
            when_to_use=inp.get("when_to_use", "") or inp.get("when-to-use", ""),
            target=inp.get("target", "project"),
            context=inp.get("context", "inline"),
            user_invocable=bool(inp.get("user_invocable", False)),
            allowed_tools=inp.get("allowed_tools"),
            evidence=inp.get("evidence", ""),
        )
        return _truncate_result(json.dumps(result, ensure_ascii=False, indent=2))

    if name == "run_shell":
        return _truncate_result(await run_shell_async(inp, on_output=_publish_tool_output))

    handlers: dict = {
        "write_file": _write_file,
        "edit_file": _edit_file,
        "delete_files": _delete_files,
        "list_files": _list_files,
        "grep_search": _grep_search,
    }
    handler = handlers.get(name)

    if not handler:
        return f"Unknown tool: {name}"

    # File and search tools are synchronous; run them off the event loop so a
    # slow disk or a large grep never stalls other sessions or the event stream.
    result = _truncate_result(await asyncio.to_thread(handler, inp))

    # 更新时间
    if name in ("write_file", "edit_file") and read_file_state is not None and not result.startswith("Error"):
        abs_path = str(_resolve_tool_path(inp["file_path"], must_exist=False).resolve())
        try:
            read_file_state[abs_path] = os.path.getmtime(abs_path)
        except OSError:
            pass

    return result



def reset_permission_cache() -> None:
    global _cached_rules
    _cached_rules = None




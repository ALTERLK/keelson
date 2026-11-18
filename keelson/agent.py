#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anthropic
import openai

from keelson import checkpoints, events, models, paths, safety, trace, usage
from keelson import ledger as ledger_mod
from keelson import scope as scope_mod
from keelson import verify as verify_mod
from keelson.i18n import pick
from keelson.mcp_client import McpManager
from keelson.memory import MemoryPrefetch, format_memories_for_injection, start_memory_prefetch
from keelson.prompt import build_system_prompt, get_git_context
from keelson.session import save_folded_session_memory, save_session
from keelson.session_memory import (
    FOLD_SESSION_MEMORY_SYSTEM,
    build_anthropic_transcript,
    build_folding_user_prompt,
    build_openai_transcript,
    fallback_folded_memory,
    format_folded_memory,
    parse_folded_memory,
)
from keelson.subagent import get_sub_agent_config
from keelson.tools import (
    CONCURRENCY_SAFE_TOOLS,
    ToolDef,
    check_permission,
    execute_tool,
    get_active_tool_definitions,
    tool_definitions,
)
from keelson.ui import (
    print_assistant_text,
    print_confirmation,
    print_cost,
    print_divider,
    print_error,
    print_info,
    print_retry,
    print_sub_agent_end,
    print_sub_agent_start,
    print_tool_call,
    print_tool_result,
    print_warning,
    start_spinner,
    stop_spinner,
)

# 指数退避重试


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status in (429, 503, 529):
        return True
    msg = str(error)
    if "overloaded" in msg or "ECONNRESET" in msg or "ETIMEDOUT" in msg:
        return True
    return False


def _safe_utf8_text(value: object) -> str:
    return str(value).encode("utf-8", errors="replace").decode("utf-8")


def _sanitize_for_utf8(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_utf8_text(value)
    if isinstance(value, list):
        return [_sanitize_for_utf8(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_for_utf8(item) for item in value)
    if isinstance(value, dict):
        return {
            _sanitize_for_utf8(key): _sanitize_for_utf8(item)
            for key, item in value.items()
        }
    return value


async def _with_retry(fn, max_retries: int = 3):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise
            delay = min(1000 * (2 ** attempt), 30000) / 1000 + (hash(str(time.time())) % 1000) / 1000
            status = getattr(error, "status_code", None) or getattr(error, "status", None)
            reason = f"HTTP {status}" if status else (getattr(error, "code", None) or "network error")
            print_retry(attempt + 1, max_retries, reason)
            await asyncio.sleep(delay)

def _get_context_windows(model: str) -> int:
    return models.context_window(model)


#多层级压缩常数
SNIP_THRESHOLD = 0.60
AUTO_COMPACT_THRESHOLD = 0.70
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"
SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}
MICROCOMPACT_IDLE_S = 5 * 60  # 5 minutes

KEEP_RECENT_RESULTS = 3

# Loop guard: a model can hammer one tool with reworded arguments and never
# finalise (see bench/spiral-diagnosis.md). Trip when one tool name fills most of
# a short window AND its results stop diverging. Windowed on NAME, not args,
# because the failure mode fuzzes args. KEELSON_LOOP_GUARD=0 disables it.
LOOP_GUARD_WINDOW = 8
LOOP_GUARD_SAME_NAME = 6
LOOP_GUARD_DISTINCT_RESULTS = 3
# The guard first asks. A model that has been asked this many times in one turn
# and still requests tools is not going to stop by itself: the runtime ends the
# turn with one tool-less call (see bench/casestudy, round 1).
LOOP_GUARD_MAX_HITS = 3
LOOP_GUARD_EXCLUDE = {
    "compact_context", "enter_plan_mode", "exit_plan_mode", "agent", "skill", "tool_search",
}

# A reply with no text and no tool call is a failed call, not "the model is done".
EMPTY_REPLY_RETRIES = 2
EMPTY_REPLY_NUDGE = (
    "Your last reply was empty. Continue with the task: call a tool, or give your final answer."
)


def _guard_key(name: str, inp: dict | None) -> str:
    """What the loop guard counts as "the same tool".

    For every tool but the shell that is its name. The shell is a tool for
    running other programs, so there it is the program and its first argument:
    six `python -c` probes in a row must not block the `python -m pytest` that
    follows them.
    """
    if name != "run_shell":
        return name
    words = str((inp or {}).get("command") or "").split()
    if not words:
        return name
    head = words[:3] if len(words) > 2 and words[1] == "-m" else words[:2]
    return "run_shell:" + " ".join(head).lower()


def _result_key(s: object) -> str:
    import hashlib

    return hashlib.sha1(str(s).strip()[:2000].encode("utf-8", "replace")).hexdigest()[:12]



def _get_max_output_tokens(model: str) -> int:
    return models.max_output_tokens(model)


# A reply cut off by max_tokens may end in half a tool call. It is never run;
# the model is told and asked to take a smaller step, at most this many times a turn.
TRUNCATION_RETRIES = 2
TRUNCATED_TOOL_CALL_NOTE = (
    "Your last reply hit the output limit before its tool call was complete, so the call was "
    "NOT executed. Take a smaller step: write a large file in several parts, or edit one "
    "section at a time."
)

CONFIRM_CHOICES = ("once", "session", "project", "deny")


def normalize_choice(answer: Any) -> tuple[str, str]:
    """A confirm hook's answer as (choice, reason). Hooks may answer True/False, a choice, or a dict."""
    if answer is True:
        return "once", ""
    if isinstance(answer, str) and answer.strip().lower() in CONFIRM_CHOICES:
        return answer.strip().lower(), ""
    if isinstance(answer, dict):
        choice = str(answer.get("choice") or ("once" if answer.get("allow") else "deny")).lower()
        return (choice if choice in CONFIRM_CHOICES else "deny"), str(answer.get("reason") or "").strip()
    return "deny", ""


def _accepts_request(fn: Callable) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "request" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def _assistant_message(content: str | None, tool_calls: list | None, reasoning: str | None) -> dict:
    """An assistant turn as it goes back into the OpenAI-style history."""
    message: dict[str, Any] = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
    if reasoning:
        message["reasoning_content"] = _safe_utf8_text(reasoning)
    return message


#转换tool的形式到openai
def _to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


class Agent:
    def __init__(self,
                 *,
                 permission_mode:str="default",
                 model:str=models.DEFAULT_MODEL,
                 api_base: str | None=None,
                 anthropic_base_url: str | None=None,
                 api_key: str | None=None,
                 thinking: bool=False,
                 max_cost_usd: float | None=None,
                 max_turns: int | None=None,
                 confirm_fn:Callable[[str], Awaitable[bool]] | None=None,
                 custom_system_prompt: str | None=None,
                 custom_tools: list[ToolDef] | None=None,
                 external_tools: dict[str, object] | None=None,
                 is_sub_agent: bool=False,
                 verify: bool | None=None,):
        self.permission_mode = permission_mode
        self.thinking = thinking
        self.model = model
        self.use_openai = bool(api_base)
        # Kept so sub-agents talk to the same endpoint with the same key.
        self._api_base = api_base
        self._anthropic_base_url = anthropic_base_url
        self._api_key = api_key
        self.is_sub_agent = is_sub_agent
        self.tools = custom_tools or tool_definitions
        # Optional out-of-band tool executors keyed by name (used by the
        # offline benchmark harness to inject a task's own tool bodies).
        self._external_tools: dict[str, object] = dict(external_tools or {})
        self.max_cost_usd = max_cost_usd
        self.max_turns = max_turns
        self.confirm_fn = confirm_fn
        self._custom_system_prompt = custom_system_prompt
        self.effective_window=_get_context_windows(model) -20000
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time= time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_hit_tokens = 0
        self.last_input_token_count = 0
        self._truncations = 0
        self.current_turns = 0
        self.last_api_call_time = 0


        self._aborted = False
        #存储异步任务
        self._current_task:asyncio.Task | None = None
        # "Allow for this session" rules (same form as settings.json rules). A
        # one-time OK is never cached: the next call is asked about again.
        self._session_rules: list[str] = []
        # Checkpoints (/undo): opened on the first turn; sub-agents share the parent's.
        self._checkpoints: checkpoints.Checkpoints | None = None
        self._checkpoint_notice_shown = False
        self._pending_notes: list[str] = []
        # Verified completion (keelson/verify.py): what really ran this turn, and the verdict.
        self._verify_enabled = (verify_mod.enabled() if verify is None else verify) and not is_sub_agent
        self._evidence = verify_mod.Evidence()
        self._verify_rounds = 0
        self._last_failed: tuple[int, list] | None = None
        self._weakening_nudged = False
        self._task_about_tests = False
        self.last_outcome: verify_mod.Outcome | None = None
        # Task ledger (keelson/ledger.py) and the current request's scope (keelson/scope.py).
        self._ledger = ledger_mod.Ledger()
        self._scope_reasons: list[str] = []
        self._out_of_scope: list[str] = []
        self._turn_request = ""
        self._last_git_note: str | None = None
        self._fold_hints: set[str] = set()     # fold-hint conditions already reported (see _maybe_fold_hint)
        self.context_files: list[str] = []      # /add: files in the conversation (always in scope)
        self._context_unsent: set[str] = set()


        # 计划模式”（Plan Mode）状态的变量
        self._pre_plan_mode: str | None=None
        self._plan_file_path: str | None=None
        self._plan_approval_fn : Callable[[str], Awaitable[bool]] | None=None
        self._context_cleared : bool=False

        #思考模式
        self._thinking_mode = self._resolve_thinking_mode()

        #子agent的输出缓存
        self._output_buffer: list[str] | None=None
        self._turn_output_buffer: list[str] | None = None

        # 编辑前读取
        self._read_file_state: dict[str, float] ={}

        #MCP集成
        self._mcp_manager = McpManager()
        self._mcp_initialized = False

        #记忆回溯
        #记忆agent已经回答过的信息
        self._already_surfaced_memories: set[str] = set()
        #当前会话占用的字节数
        self._session_memory_bytes = 0

        #区分message的历史消息
        self._anthropic_messages: list[str] = []
        self._openai_messages: list[str] = []
        self._last_retrieved_skill_reference: dict[str, Any] | None = None
        self._last_retrieved_skill_hits: list[dict[str, Any]] = []
        self._pending_skill_extraction_window: dict[str, Any] | None = None
        self._background_skill_tasks: set[asyncio.Task] = set()
        self._folded_session_memories: list[dict[str, Any]] = []
        self._fold_last_time: float = 0.0
        self._fold_count: int = 0
        self._tool_error_streak: int = 0
        self._same_tool_repeat_count: int = 0
        self._last_tool_name: str = ""
        self._loop_window: list[dict[str, str]] = []   # recent {name, result_key} for the loop guard
        self._loop_guard_hits: int = 0
        self._empty_replies: int = 0

        #构建系统提示词
        self._base_system_prompt = custom_system_prompt or build_system_prompt()

        if self.permission_mode == "plan":
            self._plan_file_path = self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
        else:
            self._system_prompt = self._base_system_prompt

        #初始化大模型客户端
        if self.use_openai:
            self._openai_client = openai.AsyncOpenAI(base_url=api_base, api_key=api_key)
            self._anthropic_client = None
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        else:
            kwargs : dict[str,Any] = {}
            if api_key:
                kwargs["api_key"] = api_key
            if anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url
            self._anthropic_client = anthropic.AsyncAnthropic(**kwargs)
            self._openai_client = None

        self._refresh_runtime_system_prompt()

    #判断返回模型的思考模式
    def _resolve_thinking_mode(self) -> str:
        if not self.thinking:
            return "disabled"
        if not self._model_supports_thinking():
            return "disabled"

        if self._model_supports_adaptive_thinking():
            return "adaptive"
        return "enabled"

    def _model_supports_thinking(self) -> bool:
        m = self.model.lower()
        if models.is_deepseek(m):
            return True
        if "claude-3-" in m or "3-5-" in m or "3-7-" in m:
            return False
        if "claude" in m and any(x in m for x in ("opus", "sonnet", "haiku")):
            return True
        return False
    def _model_supports_adaptive_thinking(self) -> bool:
        m = self.model.lower()
        return "opus-4-6" in m or "sonnet-4-6" in m

    #生成一个用于保存 AI 计划（Plan）的 Markdown 文件的绝对路径。
    def _generate_plan_file_path(self) -> str:
        d = paths.plans_dir()
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"plan-{self.session_id}.md")

    def _build_plan_mode_prompt(self) -> str:
        return f"""

    # Plan Mode Active

    Plan mode is active. You MUST NOT make any edits (except the plan file below), run non-readonly tools, or make any changes to the system.

    ## Plan File: {self._plan_file_path}
    Write your plan incrementally to this file using write_file or edit_file. This is the ONLY file you are allowed to edit.

    ## Workflow
    1. **Explore**: Read code to understand the task. Use read_file, list_files, grep_search.
    2. **Design**: Design your implementation approach. Use the agent tool with type="plan" if the task is complex.
    3. **Write Plan**: Write a structured plan to the plan file including:
       - **Context**: Why this change is needed
       - **Steps**: Implementation steps with critical file paths
       - **Verification**: How to test the changes
    4. **Exit**: Call exit_plan_mode when your plan is ready for user review.

    IMPORTANT: When your plan is complete, you MUST call exit_plan_mode. Do NOT ask the user to approve — exit_plan_mode handles that."""

    #判断当前的任务所有的任务是否完成
    @property
    def is_processing(self)->bool:
        return self._current_task is not None and not self._current_task.done()

    #大模型调用的工厂方法,构建一个用于记忆召回（memory recall）的 sideQuery 可调用对象，兼容anthropic, openai。
    def _build_side_query(self, *, max_tokens: int = 256):
        # Judge / auxiliary calls can use a different model so a self-evolving
        # skill is never graded only by its own actor.
        judge_model = paths.env_value("KEELSON_JUDGE_MODEL", default="").strip() or self.model
        if self._anthropic_client:
            client = self._anthropic_client
            model = judge_model
            async def _sq(system:str, user_message:str)->str:

                # Side queries are short and must answer in text: DeepSeek's default
                # thinking would spend the small max_tokens before any answer.
                no_think = {"thinking": {"type": "disabled"}} if models.is_deepseek(model) else {}
                resp = await client.messages.create(
                    model=model, max_tokens=max(1, int(max_tokens)), system=system,
                messages=[{"role": "user", "content": user_message}], **no_think,
                )
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                if not text.strip():
                    block_types = [str(getattr(b, "type", "")) for b in getattr(resp, "content", [])]
                    logging.warning(
                        "side_query returned empty Anthropic-compatible response: model=%s stop_reason=%s content_block_types=%s",
                        getattr(resp, "model", model),
                        getattr(resp, "stop_reason", ""),
                        block_types,
                    )
                return text
            return _sq
        if self._openai_client:
            client = self._openai_client
            model = judge_model
            async def _sq_openai(system:str, user_message:str)->str:
                no_think = ({"extra_body": {"thinking": {"type": "disabled"}}}
                            if models.is_deepseek(model) else {})
                resp = await client.chat.completions.create(
                    model=model,
                    max_tokens=max(1, int(max_tokens)),
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                    **no_think,
                )
                if not resp.choices:
                    logging.warning("side_query returned no OpenAI-compatible choices: model=%s", model)
                    return ""
                choice = resp.choices[0]
                content = choice.message.content or ""
                if not content.strip():
                    logging.warning(
                        "side_query returned empty OpenAI-compatible response: model=%s finish_reason=%s message=%s",
                        model,
                        getattr(choice, "finish_reason", ""),
                        choice.message,
                    )
                return content
            return _sq_openai
        return None
    #异步任务取消（Abort）
    def abort(self) -> None:
        self._aborted = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    def set_confirm_fn(self, fn:Callable[[str], Awaitable[bool]]) -> None:
        self.confirm_fn = fn

    def set_plan_approval_fn(self, fn:Callable[[str], Awaitable[bool]]) -> None:
        self._plan_approval_fn = fn


    #计划模式开关（“状态切换与现场保护”机制）
    def toggle_plan_mode(self) -> str:
        """
               1. 退出计划模式（从 plan 切回原模式）
               当当前模式已经是 plan 时，执行 if 分支：
               恢复之前的状态：self.permission_mode = self._pre_plan_mode or "default"。
                   在进入计划模式时，程序会把原本的模式保存在 _pre_plan_mode 里。退出时，就把它重新拿出来赋值回去，恢复到切换前的状态。
               清理计划模式的痕迹：把 _pre_plan_mode 和 _plan_file_path（计划文件路径）清空，并将系统提示词 _system_prompt 恢复为最基础的 _base_system_prompt。
               同步 OpenAI 消息：如果底层使用的是 OpenAI 接口，它还会同步更新消息列表里的第一条系统提示词，确保 AI 的上下文也跟着切换回来。
               反馈返回：打印退出提示，并返回恢复后的模式名称。

               2. 进入计划模式（从其他模式切入 plan）
       当当前模式不是 plan 时，执行 else 分支：
       保护当前现场：self._pre_plan_mode = self.permission_mode。先把当前正在使用的模式（比如正常模式或自动接受模式）暂存起来，方便以后能原路返回。
       切换并初始化：将当前模式设为 "plan"，生成一个专属的计划文件路径，并扩展系统提示词。通过拼接 _build_plan_mode_prompt()，给 AI 注入“只动脑不动手、输出结构化计划”的专属指令。
       同步 OpenAI 消息：同样地，如果使用 OpenAI，也会实时更新上下文里的系统提示词。
       反馈与返回：打印进入提示（包含计划文件的路径），并返回 "plan"。
        """
        if self.permission_mode == "plan":
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._system_prompt = self._base_system_prompt
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] =self._system_prompt
            print_info(f"Exited plan mode -> {self.permission_mode} mode")
            return self.permission_mode
        else:
            self._pre_plan_mode = self.permission_mode
            self.permission_mode = "plan"
            self._plan_file_path = self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info(f"Entered plan mode. Plan file: {self._plan_file_path}")
            return "plan"

    MODES_CYCLE = ("default", "acceptEdits", "plan")

    def set_permission_mode(self, mode: str) -> str:
        """Switch mode (Shift+Tab, /mode). Entering or leaving plan mode also changes the system prompt."""
        if mode not in ("default", "acceptEdits", "plan", "dontAsk", "bypassPermissions") or mode == self.permission_mode:
            return self.permission_mode
        if mode == "plan" or self.permission_mode == "plan":
            if self.permission_mode == "plan":
                self._pre_plan_mode = mode
            self.toggle_plan_mode()
            if mode != "plan":
                self.permission_mode = mode
            return self.permission_mode
        self.permission_mode = mode
        return mode

    def next_permission_mode(self) -> str:
        cycle = self.MODES_CYCLE
        current = self.permission_mode if self.permission_mode in cycle else cycle[-1]
        return self.set_permission_mode(cycle[(cycle.index(current) + 1) % len(cycle)])

    def last_answer(self) -> str:
        """The text of the last assistant message: what `keelson -p` prints."""
        for msg in reversed(self._openai_messages if self.use_openai else self._anthropic_messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                if content.strip():
                    return content
            elif isinstance(content, list):
                text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if text.strip():
                    return text
        return ""

    # ── files in the conversation (/add, /drop) ─────────────────

    CONTEXT_FILE_CHARS = 60_000

    def add_context_files(self, names: list[str]) -> tuple[list[str], list[str]]:
        added, missing = [], []
        for name in names:
            p = Path(name)
            if not p.is_file():
                missing.append(name)
                continue
            norm = scope_mod.normalize(name)
            if norm not in self.context_files:
                self.context_files.append(norm)
                self._context_unsent.add(norm)
                added.append(norm)
        return added, missing

    def drop_context_files(self, names: list[str] | None = None) -> list[str]:
        if not names:
            dropped, self.context_files = list(self.context_files), []
        else:
            wanted = {scope_mod.normalize(n) for n in names}
            dropped = [f for f in self.context_files if f in wanted]
            self.context_files = [f for f in self.context_files if f not in wanted]
        self._context_unsent -= set(dropped)
        return dropped

    def _context_files_note(self) -> str:
        """Contents of files added since the last message, sent once; later turns only name them."""
        if not self.context_files:
            return ""
        parts = []
        for name in self.context_files:
            if name not in self._context_unsent:
                continue
            try:
                text = Path(name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > self.CONTEXT_FILE_CHARS:
                text = text[:self.CONTEXT_FILE_CHARS] + "\n… (cut; read_file shows the rest)"
            parts.append(f'<file path="{name}">\n{text}\n</file>')
        self._context_unsent.clear()
        head = ("The user added these files to the conversation (they are in scope): "
                + ", ".join(self.context_files) + ".")
        return "<keelson-context-files>\n" + head + ("\n" + "\n".join(parts) if parts else "") + \
            "\n</keelson-context-files>"

    def resume(self, session_id: str) -> bool:
        """Reopen a saved session: its messages, folded memories and task ledger, under its own id."""
        from .session import load_session

        data = load_session(session_id)
        if not data:
            return False
        self.restore_session(data)
        self.session_id = session_id
        return True

    def get_token_usage(self) -> dict:
        return {"input":self.total_input_tokens, "output":self.total_output_tokens}

    #主入口

    async def  chat(self, user_message:str)->None:
        #懒加载MCP服务在第一次chat的时候
        if not self._mcp_initialized and not self.is_sub_agent:
            self._mcp_initialized = True
            try:
                await self._mcp_manager.load_and_connect()
                mcp_defs = self._mcp_manager.get_tool_definitions()
                if mcp_defs:
                    self.tools = self.tools + mcp_defs
            except Exception as e:
                print_error(f"MCP init failed: {e}")

        original_user_message = _safe_utf8_text(user_message)
        if not self.is_sub_agent:
            over = usage.over_daily_budget()
            if over:
                print_warning(over)
                print_divider()
                return
        ready_skill_extraction_window: dict[str, Any] | None = None
        self._last_retrieved_skill_reference = None
        self._last_retrieved_skill_hits = []
        if not self.is_sub_agent:
            ready_skill_extraction_window = self._pop_pending_skill_extraction_window(original_user_message)
            with events.span("skill_retrieval"):
                user_message, self._last_retrieved_skill_reference = self._augment_user_message_with_skill_context(
                    original_user_message
                )

        if not self.is_sub_agent:
            events.emit(events.USER_MESSAGE, text=original_user_message)

        if self._pending_notes and not self.is_sub_agent:
            # What happened between turns (an /undo, say), so the model does not work from stale file contents.
            user_message = "\n".join(self._pending_notes) + "\n\n" + user_message
            self._pending_notes = []

        if not self.is_sub_agent:
            self._start_request(original_user_message)
            note = self._context_files_note()
            if note:
                user_message = f"{note}\n\n{user_message}"
            if self._custom_system_prompt is None:
                # Git status rides on the user message, not the system prompt, so the cached prefix holds.
                git = await asyncio.to_thread(get_git_context)
                if git and git != self._last_git_note:
                    self._last_git_note = git
                    user_message = f"<git-status>\n{git}\n</git-status>\n\n{user_message}"
        turn_start_usage = (self.total_input_tokens, self.total_output_tokens, self.total_cache_hit_tokens,
                            self._get_current_cost_usd())

        self._aborted = False
        self._turn_output_buffer = []
        self._loop_window = []
        self._loop_guard_hits = 0
        self._empty_replies = 0
        self._truncations = 0
        self._evidence = verify_mod.Evidence()
        self._verify_rounds = 0
        self._last_failed = None
        self._weakening_nudged = False
        self._task_about_tests = verify_mod.mentions_tests(original_user_message)
        self.last_outcome = None
        store = await self._begin_checkpoint(original_user_message)
        coro = self._chat_openai(user_message) if self.use_openai else self._chat_anthropic(user_message)
        self._current_task = asyncio.create_task(coro)
        try:
            await self._current_task
        except asyncio.CancelledError:
            self._aborted = True

        finally:
            self._current_task = None
            if store is not None:
                await asyncio.shield(asyncio.to_thread(store.end_turn))
        assistant_text = "".join(self._turn_output_buffer or []).strip()
        self._turn_output_buffer = None
        if not self.is_sub_agent and not self._aborted:
            self._schedule_background_skill_task(self._run_skill_usage_tracking(original_user_message, assistant_text))
            if ready_skill_extraction_window:
                self._schedule_background_skill_task(self._run_online_skill_evolution(ready_skill_extraction_window))
            self._set_pending_skill_extraction_window(
                original_user_message=original_user_message,
                assistant_text=assistant_text,
                retrieved_reference=self._last_retrieved_skill_reference,
            )
        if not self.is_sub_agent:
            self._finish_request(turn_start_usage)
            print_divider()
            self._auto_save()

    # ── task ledger and scope (keelson/ledger.py, keelson/scope.py) ──

    def _start_request(self, text: str) -> None:
        """A new request: note what the user said, and set its scope to the files they @-mentioned."""
        self._ledger.note_user_message(text)
        self._turn_request = text
        mentioned = scope_mod.mentioned_paths(text)
        self._ledger.scope = list(dict.fromkeys(self.context_files + mentioned))
        self._scope_reasons = ["mentioned by you"] if self._ledger.scope else []
        self._out_of_scope = []
        if mentioned:
            events.emit(events.SCOPE, paths=mentioned, reason="mentioned by you", added=True)

    def _finish_request(self, start: tuple[int, int, int, float]) -> None:
        changed = self.last_outcome.changed if self.last_outcome else list(dict.fromkeys(self._evidence.tool_paths))
        self._ledger.note_files(changed)
        if self.last_outcome is not None:
            self._ledger.note_verify(self.last_outcome.status, self.last_outcome.detail)
        ledger_mod.save(self._ledger, self.session_id)
        usage.record(session_id=self.session_id, model=self.model,
                     input_tokens=self.total_input_tokens - start[0], output_tokens=self.total_output_tokens - start[1],
                     cache_hit_tokens=self.total_cache_hit_tokens - start[2],
                     cost_usd=max(0.0, self._get_current_cost_usd() - start[3]))

    @property
    def ledger(self) -> ledger_mod.Ledger:
        return self._ledger

    def reset_ledger(self) -> None:
        self._ledger = ledger_mod.Ledger()

    def _scope_active(self) -> bool:
        return (bool(self._ledger.scope) and not self.is_sub_agent
                and self.permission_mode not in ("bypassPermissions", "plan")
                and paths.env_flag("KEELSON_SCOPE_GUARD", default=True))

    @staticmethod
    def _edit_targets(name: str, inp: dict) -> list[str]:
        if name == "delete_files":
            raw = inp.get("paths") or []
            return [str(t) for t in (raw if isinstance(raw, list) else [raw]) if t]
        return [str(inp["file_path"])] if inp.get("file_path") else []

    def _scope_guard(self, name: str, inp: dict) -> dict | None:
        """Changing a file outside the current request's scope needs a person's OK."""
        if name not in ("write_file", "edit_file", "delete_files") or not self._scope_active():
            return None
        out = scope_mod.outside(self._edit_targets(name, inp), self._ledger.scope)
        if not out or "keelson(out-of-scope)" in self._session_rules:
            return None
        from .tools import load_permission_rules

        if any(r["tool"] == "keelson" and r["pattern"] == "out-of-scope" for r in load_permission_rules()["allow"]):
            return None
        reason = ("is outside the scope of this request (" + ", ".join(self._ledger.scope[:8])
                  + (", ..." if len(self._ledger.scope) > 8 else "") + "). Keelson asks so that unrelated code "
                  "is not changed.")
        subject = f"{name} {', '.join(out)}"
        if self.permission_mode == "dontAsk":
            return {"action": "deny", "message": f"Auto-denied (dontAsk mode): {subject} {reason} If the request "
                                                 "really needs it, declare_scope it with the reason first."}
        return {"action": "confirm", "message": subject, "reason": reason, "rules": ["keelson(out-of-scope)"]}

    def _declare_scope(self, inp: dict) -> str:
        raw = inp.get("paths") or []
        entries = [scope_mod.normalize(p) for p in (raw if isinstance(raw, list) else [raw]) if str(p).strip()]
        if not entries:
            return "Error: give at least one file, folder or glob in paths."
        added = [e for e in entries if e not in self._ledger.scope]
        self._ledger.scope += added
        reason = str(inp.get("reason") or "").strip()[:300]
        if reason:
            self._scope_reasons.append(reason)
        if added:
            events.emit(events.SCOPE, paths=added, reason=reason, added=True)
        return ("Scope for this request: " + ", ".join(self._ledger.scope)
                + ". Changing other files will ask the user first.")

    def _update_task(self, inp: dict) -> str:
        plan = inp.get("plan")
        summary = self._ledger.update(plan if isinstance(plan, list) else None, inp.get("decision"))
        events.emit(events.TASK, plan=list(self._ledger.plan), decision=str(inp.get("decision") or ""))
        return summary

    def _fold_kept_note(self) -> str:
        """What a fold must not lose, appended to the folded summary verbatim.

        ``KEELSON_LEDGER=0`` leaves it out, so a fold keeps only the model's own summary,
        as summary-only compaction does; bench/features.py uses it as the comparison arm.
        """
        if not paths.env_flag("KEELSON_LEDGER", default=True):
            return ""
        return self._ledger.render()



    # ── checkpoints: /undo, /redo, /revert ─────────────────────

    # ── verified completion (keelson/verify.py) ─────────────────

    _WEAKENED_NOTE = (
        "[Keelson] Before you finish: these changes make tests weaker, and this task did not ask for test "
        "changes:\n{items}\n\nPut the tests back and fix the code instead. If a test itself is wrong, leave it "
        "as it was and explain why in your answer, so the user can decide."
    )
    _FAILED_NOTE = (
        "[Keelson verification] You were about to finish, but the project's checks fail. Keelson ran them "
        "after your last change; this is the real output:\n\n{outputs}\n\n"
        "If your change caused a failure, fix it. If a failure is outside what the user asked for (for example "
        "it was failing before you started), do not widen the task to fix it: leave that code alone and say "
        "plainly in your answer which checks still fail and why, instead of reporting success. Do not edit, "
        "skip or delete tests to make them pass unless the user asked for that."
    )

    def _record_evidence(self, name: str, inp: dict, result: str) -> None:
        if self.is_sub_agent:
            return
        if name == "agent" and (inp.get("type") or "general") == "general":
            self._evidence.seq += 1                 # a general sub-agent may have changed files
            self._evidence.last_change = self._evidence.seq
            self._evidence.shell_may_have_changed = True
            return
        read_only = name == "run_shell" and safety.assess_shell(inp.get("command", "")).read_only
        if name in ("write_file", "edit_file", "delete_files") and self._scope_active() and isinstance(result, str)                 and not result.startswith(("Error", "Action denied", "User denied")):
            # It ran, so the person allowed it: it joins the scope, and is reported at the end of the turn.
            for path in scope_mod.outside(self._edit_targets(name, inp), self._ledger.scope):
                norm = scope_mod.normalize(path)
                self._out_of_scope.append(norm)
                self._ledger.scope.append(norm)
        self._evidence.record(name, inp, result if isinstance(result, str) else str(result),
                              read_only_shell=read_only)

    def _test_edits_allowed(self) -> bool:
        from .tools import load_permission_rules

        if "keelson(test-edits)" in self._session_rules:
            return True
        return any(r["tool"] == "keelson" and r["pattern"] == "test-edits" for r in load_permission_rules()["allow"])

    def _test_guard(self, name: str, inp: dict) -> dict | None:
        """Changing an existing test file when the task is not about tests needs a person's OK."""
        if (name not in ("write_file", "edit_file", "delete_files") or self._task_about_tests
                or self.permission_mode in ("bypassPermissions", "plan")
                or not paths.env_flag("KEELSON_GUARD_TESTS", default=True)):
            return None
        if name == "delete_files":
            raw = inp.get("paths") or []
            targets = [str(t) for t in (raw if isinstance(raw, list) else [raw])]
        else:
            targets = [str(inp.get("file_path") or "")]
        existing = [t for t in targets if t and verify_mod.is_test_file(t) and Path(t).exists()]
        if not existing:
            return None                          # new test files add tests; that is welcome
        old, new = inp.get("old_string") or "", inp.get("new_string") or ""
        if name == "edit_file" and old and old in new:
            return None                          # an edit that only adds lines
        if self._test_edits_allowed():
            return None
        reason = ("changes an existing test file, and this task does not mention tests. Keelson asks so that "
                  "tests are not weakened to make them pass.")
        subject = f"{name} {', '.join(existing)}"
        if self.permission_mode == "dontAsk":
            return {"action": "deny", "message": f"Auto-denied (dontAsk mode): {subject} — {reason}"}
        return {"action": "confirm", "message": subject, "reason": reason, "rules": ["keelson(test-edits)"]}

    async def _changed_this_turn(self) -> list[str]:
        store = self.checkpoint_store()
        if store is not None and store.mode != checkpoints.OFF:
            changed = await asyncio.to_thread(store.turn_changes)
            if changed is not None:
                if store.mode == checkpoints.GIT:
                    return changed
                # Without git, shell changes are invisible: assume code changed if a command may have changed it.
                return changed + (["(changed by shell commands)"] if self._evidence.shell_may_have_changed else [])
        changed = list(dict.fromkeys(self._evidence.tool_paths))
        if self._evidence.shell_may_have_changed:
            changed.append("(changed by shell commands)")
        return changed

    def _weakened_tests(self, changed: list[str]) -> list[str]:
        store = self.checkpoint_store()
        found: list[str] = []
        for rel in changed:
            if not verify_mod.is_test_file(rel):
                continue
            before = store.start_content(rel) if store is not None else None
            target = Path(rel)
            try:
                after = target.read_bytes() if target.exists() else None
            except OSError:
                continue
            if before is None and store is None:
                continue                         # nothing to compare with
            found += verify_mod.weakening(
                rel, before.decode("utf-8", "replace") if before is not None else None,
                after.decode("utf-8", "replace") if after is not None else None)
        return found

    async def run_checks(self, commands: list[str]) -> list[verify_mod.Run]:
        """Run the project's checks the way run_shell would; refuse any the permission rules would question."""
        from .tools import check_permission, run_shell_async

        events.emit(events.VERIFY, phase="running", commands=commands)
        runs = []
        for command in commands:
            # The same gate the model's own commands go through, minus the asking: a check that would need
            # a person's OK is reported as not run rather than run behind their back.
            perm = check_permission("run_shell", {"command": command}, self.permission_mode,
                                    self._plan_file_path, self._session_rules)
            if perm["action"] != "allow":
                why = perm.get("reason") or perm.get("message") or "needs confirmation"
                runs.append(verify_mod.Run(command, None, self._evidence.seq, time.strftime("%H:%M"),
                                           f"not run by Keelson: {why}"))
                continue
            with events.span("verify", command=command):
                result = await run_shell_async({"command": command, "timeout": verify_mod.timeout_s() * 1000,
                                                "env": {"CI": "1"}})
            code = verify_mod.exit_code_of(result)
            runs.append(verify_mod.Run(command, code, self._evidence.seq, time.strftime("%H:%M"),
                                       verify_mod.summarize(result, code), verify_mod.tail(result)))
        return runs

    async def _end_of_turn_check(self, final_text: str) -> str | None:
        """Runs when the model is about to finish. A string: tell the model this and keep going."""
        if not self._verify_enabled or self.permission_mode == "plan" or self._aborted:
            return None
        changed = await self._changed_this_turn()
        if not changed:
            return None                          # a question, not a change: nothing to verify
        weakened = self._weakened_tests(changed)
        if (weakened and not self._task_about_tests and not self._weakening_nudged
                and not self._test_edits_allowed()):
            self._weakening_nudged = True
            return self._WEAKENED_NOTE.format(items="\n".join(f"- {w}" for w in weakened))

        Outcome = verify_mod.Outcome
        code_changed = [p for p in changed if not verify_mod.is_doc(p)]
        commands, source = verify_mod.commands_for()
        if not code_changed:
            outcome = Outcome(verify_mod.UNVERIFIED, pick("只改了文档", "only documentation changed"))
        elif not commands:
            outcome = Outcome(verify_mod.UNVERIFIED, pick(
                "这个项目没有验证命令：在 KEELSON.md 里加一节 ## Verify（/verify init）",
                "no check command for this project: add a ## Verify section to KEELSON.md (/verify init)"))
        else:
            unchanged = self._last_failed is not None and self._last_failed[0] == self._evidence.last_change
            if unchanged:
                # Nothing changed since Keelson's failing run: the answer stands, the result is that run's.
                earlier, missing, runs = [], commands, self._last_failed[1]
            else:
                # A check the agent already passed since its last change is not run again.
                earlier = [r for r in (self._evidence.passing_after_change([c]) for c in commands) if r]
                missing = [c for c in commands if not any(verify_mod.same_command(r.command, c) for r in earlier)]
                runs = earlier + (await self.run_checks(missing) if missing else [])
            not_run = [r for r in runs if r.exit_code is None and not r.output]
            bad = [r for r in runs if r.exit_code != 0 and r not in not_run]
            # An answer that already says what still fails is honest; pushing back would widen the task.
            if (bad and not unchanged and self._verify_rounds < verify_mod.retries()
                    and not verify_mod.acknowledges_failure(final_text)):
                self._verify_rounds += 1
                self._last_failed = (self._evidence.last_change, runs)
                outputs = "\n\n".join(f"$ {r.command}   (exit code {r.exit_code})\n{r.output or r.summary}"
                                      for r in bad)
                return self._FAILED_NOTE.format(outputs=outputs)
            detail = "; ".join(f"{r.command}: {r.summary}" for r in runs)
            if bad:
                outcome = Outcome(verify_mod.FAILED, detail, runs)
            elif not_run:
                outcome = Outcome(verify_mod.UNVERIFIED, detail, runs)
            else:
                who = (pick("Agent 自己跑的", "run by the agent") if not missing
                       else pick("Keelson 跑的", "run by Keelson") if not earlier
                       else pick("Agent 和 Keelson 跑的", "run by the agent and Keelson"))
                at = pick("于", "at")
                outcome = Outcome(verify_mod.PASSED, f"{detail} ({who} {at} {runs[-1].at})", runs)
        outcome.weakened, outcome.changed = weakened, changed
        outcome.out_of_scope = list(dict.fromkeys(self._out_of_scope))
        big = scope_mod.size_warning(self._turn_request, changed)
        outcome.warnings = [big] if big else []
        backed = outcome.status == verify_mod.PASSED or self._evidence.passing_after_change() is not None
        outcome.unproven = [] if backed else verify_mod.claims(final_text)
        self.last_outcome = outcome
        events.emit(events.VERIFY, phase="done", source=source, **outcome.event())
        return None

    def checkpoint_store(self) -> checkpoints.Checkpoints | None:
        if self.is_sub_agent:
            return None
        if self._checkpoints is None:
            self._checkpoints = checkpoints.for_workspace()
        return self._checkpoints

    async def _begin_checkpoint(self, label: str) -> checkpoints.Checkpoints | None:
        store = self.checkpoint_store()
        if store is None or store.mode == checkpoints.OFF:
            return None
        with events.span("checkpoint"):
            await asyncio.to_thread(store.begin_turn, label)
        if store.mode != checkpoints.GIT and not self._checkpoint_notice_shown:
            self._checkpoint_notice_shown = True
            events.emit(events.NOTICE, level="warn",
                        message=f"Limited undo: {store.reason}. Shell commands will ask before they run.")
        return store

    def _restored(self, verb: str, report: checkpoints.Report) -> checkpoints.Report:
        if report.ok and (report.restored or report.removed):
            files = ", ".join((report.restored + report.removed)[:30])
            self._pending_notes.append(
                f"[Keelson: the user ran {verb}; these files changed on disk since your last look: {files}. "
                "Read a file again before editing it.]")
            self._read_file_state.clear()       # earlier reads are stale now
        return report

    def undo(self, steps: int = 1, *, force: bool = False) -> checkpoints.Report:
        store = self.checkpoint_store()
        if store is None or store.mode == checkpoints.OFF:
            return checkpoints.Report(False, "Checkpoints are off.")
        return self._restored(f"/undo {steps}" if steps > 1 else "/undo", store.undo(steps, force=force))

    def redo(self) -> checkpoints.Report:
        store = self.checkpoint_store()
        if store is None or store.mode == checkpoints.OFF:
            return checkpoints.Report(False, "Checkpoints are off.")
        return self._restored("/redo", store.redo())

    def revert(self, file: str, steps: int = 1) -> checkpoints.Report:
        store = self.checkpoint_store()
        if store is None or store.mode == checkpoints.OFF:
            return checkpoints.Report(False, "Checkpoints are off.")
        return self._restored(f"/revert {file}", store.revert(file, steps))

   #执行一次对话，收集本轮模型输出文本，并返回本轮消耗的 token 数
    async def run_once(self, prompt:str)->None:
        self._output_buffer = []
        prev_in = self.total_input_tokens
        prev_out = self.total_output_tokens
        prev_hit = self.total_cache_hit_tokens
        await self.chat(prompt)
        text = "".join(self._output_buffer)
        self._output_buffer = None
        return {
            "text": text,
            "tokens":{
                "input":self.total_input_tokens-prev_in,
                "output":self.total_output_tokens-prev_out,
                "cache_hit": self.total_cache_hit_tokens - prev_hit,
            },
        }

    #输出工具：统一处理模型输出文本。根据当前是否处于“收集输出”的模式
    # 决定是把文本存进缓冲区，还是直接打印到终端。
    def _emit_text(self, text:str)->None:
        text = _safe_utf8_text(text)
        if self._turn_output_buffer is not None:
            self._turn_output_buffer.append(text)
        if self._output_buffer is not None:
            self._output_buffer.append(text)
        else:
            print_assistant_text(text)

    def _maybe_fold_hint(self) -> None:
        """When the context gets long or tools keep failing, tell the model once, at the end of the newest message.

        This used to be a section of the system prompt rebuilt after every round of tools; its numbers changed
        each time, so the first message changed and the provider could not reuse any cached prefix.
        """
        if self._custom_system_prompt is not None:
            return
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0.0
        now = set()
        if utilization >= 0.6:
            now.add("context-75" if utilization >= 0.75 else "context-60")
        if self._tool_error_streak >= 3:
            now.add("errors")
        if self._same_tool_repeat_count >= 4:
            now.add("repeats")
        new = now - self._fold_hints
        self._fold_hints = now | ({"context-60"} if "context-75" in now else set())
        if not new:
            return
        last_fold = "never" if not self._fold_last_time else f"{int((time.time() - self._fold_last_time) / 60)}m ago"
        text = (f"<keelson-fold-hint>Context {utilization:.0%} full; {self._tool_error_streak} tool errors in a row; "
                f"same tool {self._same_tool_repeat_count} times in a row; last fold: {last_fold}. If the context is "
                "long, a tool keeps being retried without progress, or failures pile up, call compact_context "
                "before more tools. If you folded recently and the next step is clear, carry on.</keelson-fold-hint>")
        self._append_to_last_message(text)

    def _append_to_last_message(self, text: str) -> None:
        """Add text to the newest message (a tool result), so everything before it stays byte-identical."""
        messages = self._openai_messages if self.use_openai else self._anthropic_messages
        if not messages:
            return
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, str) and last.get("role") in ("tool", "user"):
            last["content"] = content + "\n\n" + text
        elif isinstance(content, list) and last.get("role") == "user":
            content.append({"type": "text", "text": text})
        else:
            messages.append({"role": "user", "content": text})

    def _refresh_runtime_system_prompt(self) -> None:
        if self._custom_system_prompt is not None:
            return
        self._base_system_prompt = build_system_prompt()
        if self.permission_mode == "plan":
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
        else:
            self._system_prompt = self._base_system_prompt
        if self.use_openai and self._openai_messages and self._openai_messages[0].get("content") != self._system_prompt:
            self._openai_messages[0]["content"] = self._system_prompt

    def _record_tool_outcome(self, tool_name: str, success: bool) -> None:
        if tool_name == self._last_tool_name:
            self._same_tool_repeat_count += 1
        else:
            self._same_tool_repeat_count = 1
        self._last_tool_name = tool_name
        if success:
            self._tool_error_streak = 0
        else:
            self._tool_error_streak += 1

    def _record_fold_event(self) -> None:
        self._fold_last_time = time.time()
        self._fold_count += 1
        self._tool_error_streak = 0
        self._same_tool_repeat_count = 0
        self._last_tool_name = ""
        # The loop guard's window is deliberately left alone. A fold changes what
        # the model can see, not what it has just been doing, and `compact_context`
        # is a tool: clearing the window here let the model reset the guard itself.

    def _looks_like_tool_failure(self, tool_name: str, raw: str, result: str) -> bool:
        text = f"{raw}\n{result}".lower()
        if any(marker in text for marker in ("error", "denied", "timed out", "timeout")):
            return True
        if tool_name == "compact_context" and "no context compaction" in text:
            return True
        return False

    def _augment_user_message_with_skill_context(self, user_message: str) -> tuple[str, dict[str, Any] | None]:
        try:
            from .skills import format_retrieved_skill_context

            context, top_ref = format_retrieved_skill_context(user_message, limit=3)
        except Exception:
            return user_message, None
        if top_ref and isinstance(top_ref.get("all_hits"), list):
            self._last_retrieved_skill_hits = list(top_ref.get("all_hits") or [])
        if not context.strip():
            return user_message, top_ref
        return f"{user_message}\n\n{context}", top_ref

    def _strip_runtime_injections(self, text: str) -> str:
        return re.sub(r"\n*<retrieved_skills>.*?</retrieved_skills>\s*", "", str(text or ""), flags=re.DOTALL).strip()

    def _message_text(self, msg: dict[str, Any]) -> str:
        content = msg.get("content")
        if isinstance(content, str):
            return self._strip_runtime_injections(content)
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                    elif "content" in block and block.get("type") not in {"tool_result", "tool_use"}:
                        parts.append(str(block.get("content") or ""))
            return self._strip_runtime_injections("\n".join(parts))
        return ""

    def _recent_dialog_messages(self, *, max_messages: int = 8) -> list[dict[str, str]]:
        raw_messages = self._openai_messages if self.use_openai else self._anthropic_messages
        out: list[dict[str, str]] = []
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = self._message_text(msg)
            if text:
                out.append({"role": role, "content": text})
        return out[-max(2, int(max_messages)) :]

    async def _confirm_online_skill_write(self, summary: str) -> bool:
        if self.permission_mode in {"bypassPermissions", "acceptEdits"}:
            return True
        if self.permission_mode in {"plan", "dontAsk"}:
            return False
        if self.confirm_fn is None:
            return False
        print_confirmation(summary)
        try:
            return bool(await self.confirm_fn(summary))
        except Exception:
            return False

    async def _confirm_background_online_skill_write(self, summary: str) -> bool:
        return self.permission_mode in {"bypassPermissions", "acceptEdits"}

    def _online_evolution_enabled(self) -> bool:
        return paths.env_flag("KEELSON_AUTO_SKILL_EVOLUTION", default=True)

    def _schedule_background_skill_task(self, coro) -> None:
        if self.permission_mode == "plan":
            try:
                coro.close()
            except Exception:
                pass
            return
        task = asyncio.create_task(coro)
        self._background_skill_tasks.add(task)

        def _done(done_task: asyncio.Task) -> None:
            self._background_skill_tasks.discard(done_task)
            try:
                done_task.result()
            except Exception:
                pass

        task.add_done_callback(_done)

    async def drain_background_skill_tasks(self) -> None:
        tasks = [task for task in self._background_skill_tasks if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _pop_pending_skill_extraction_window(self, next_user_feedback: str) -> dict[str, Any] | None:
        pending = self._pending_skill_extraction_window
        self._pending_skill_extraction_window = None
        if not pending:
            return None
        messages = list(pending.get("messages") or [])
        feedback = _safe_utf8_text(next_user_feedback).strip()
        if feedback:
            messages.append({"role": "user", "content": feedback})
        pending["messages"] = messages[-10:]
        pending["next_user_feedback"] = feedback
        return pending

    def _set_pending_skill_extraction_window(
        self,
        *,
        original_user_message: str,
        assistant_text: str,
        retrieved_reference: dict[str, Any] | None,
    ) -> None:
        if not original_user_message.strip() or not assistant_text.strip():
            return
        self._pending_skill_extraction_window = {
            "messages": self._recent_dialog_messages(max_messages=8),
            "latest_user": original_user_message,
            "latest_assistant": assistant_text,
            "retrieved_reference": self._compact_retrieved_reference(retrieved_reference),
            "session_id": self.session_id,
        }

    def _compact_retrieved_reference(self, ref: dict[str, Any] | None) -> dict[str, Any] | None:
        if not ref:
            return None
        return {k: v for k, v in ref.items() if k != "all_hits"}

    async def _run_online_skill_evolution(self, window: dict[str, Any], *, interactive_confirm: bool = False) -> None:
        if not self._online_evolution_enabled() or self.permission_mode == "plan":
            return
        messages = list(window.get("messages") or [])
        if not messages:
            return

        # Cheap pre-gate: skip the two-model Extractor/Maintainer path unless the
        # latest user turn actually looks like durable feedback. Bypassed for an
        # explicit /extract_now and via KEELSON_SKILL_GATING=0.
        if not interactive_confirm and paths.env_flag("KEELSON_SKILL_GATING", default=True):
            from .skill_gating import looks_like_feedback

            latest_user = str(window.get("next_user_feedback") or window.get("latest_user") or "")
            if latest_user and not looks_like_feedback(latest_user):
                return

        side_query = self._build_side_query(max_tokens=2200)
        if side_query is None:
            return

        try:
            from .online_skill_evolution import online_ingest
        except Exception:
            return

        result = await online_ingest(
            messages=messages,
            side_query=side_query,
            retrieved_reference=window.get("retrieved_reference") or None,
            hint=str(window.get("hint") or ""),
            confirm_write=self._confirm_online_skill_write if interactive_confirm else self._confirm_background_online_skill_write,
            target=paths.env_value("KEELSON_AUTO_SKILL_TARGET", default="project"),
        )
        if result.get("ok"):
            if result.get("action") in {"add", "merge"}:
                self._refresh_runtime_system_prompt()
            if result.get("action") in {"add", "merge", "merge_quarantined"}:
                events.emit(
                    events.SKILL_EVOLUTION,
                    action=result.get("action"),
                    skill=result.get("skill"),
                    version=result.get("version"),
                    gate=result.get("gate"),
                )
        elif result.get("action") not in {"add_denied", "merge_denied"}:
            print_error(f"Online skill evolution failed: {result.get('error') or result}")

    async def _run_skill_usage_tracking(self, original_user_message: str, assistant_text: str) -> None:
        if not self._online_evolution_enabled() or self.permission_mode == "plan":
            return
        hits = list(self._last_retrieved_skill_hits or [])
        if not hits or not assistant_text.strip():
            return
        side_query = self._build_side_query(max_tokens=700)
        try:
            from .online_skill_evolution import judge_retrieved_skill_usage
            from .skills import record_usage_judgments

            judgments = await judge_retrieved_skill_usage(
                hits=hits,
                user_message=original_user_message,
                assistant_text=assistant_text,
                side_query=side_query,
            )
            result = record_usage_judgments(judgments)
            if result.get("pruned"):
                self._refresh_runtime_system_prompt()
        except Exception:
            return

    async def extract_now(self, hint: str = "") -> dict[str, Any]:
        pending = self._pending_skill_extraction_window
        if not pending:
            return {"ok": False, "error": "no pending online skill extraction window"}
        window = dict(pending)
        window["hint"] = hint
        await self._run_online_skill_evolution(window, interactive_confirm=True)
        self._pending_skill_extraction_window = None
        return {"ok": True}


    def clear_history(self)->None:
        self._anthropic_messages = []
        self._openai_messages = []
        self._pending_skill_extraction_window = None
        self._last_retrieved_skill_reference = None
        self._last_retrieved_skill_hits = []
        self._fold_last_time = 0.0
        self._fold_count = 0
        self._tool_error_streak = 0
        self._same_tool_repeat_count = 0
        self._last_tool_name = ""
        self._loop_window = []
        self._ledger = ledger_mod.Ledger()
        self._last_git_note = None
        self._fold_hints = set()
        if self.use_openai:
            self._openai_messages.append({"role": "system", "content":self._system_prompt})
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_hit_tokens = 0
        self.last_input_token_count = 0
        print_info("Conversation cleared.")

    def set_model(self, model: str) -> None:
        """Switch the model on the same endpoint and key; the conversation is kept.

        Tokens spent so far stay counted, but the cost estimate from here on uses
        the new model's price for the whole session (an approximation).
        """
        self.model = model.strip()
        self.effective_window = _get_context_windows(self.model) - 20000
        self._thinking_mode = self._resolve_thinking_mode()
        self._emit_usage()

    def show_cost(self):
        total = self._get_current_cost_usd()
        budget_info = f" / ${self.max_cost_usd} budget" if self.max_cost_usd else ""
        turn_info = f" | Turns: {self.current_turns}/{self.max_turns}" if self.max_turns else ""
        print_info(
            f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n  Estimated cost: ${total:.4f}{budget_info}{turn_info}")

    #获取当前的花费，
    def _get_current_cost_usd(self) -> float:
        hit = min(self.total_cache_hit_tokens, self.total_input_tokens)
        known = models.cost_usd(self.model, cache_hit=hit, cache_miss=self.total_input_tokens - hit,
                                output=self.total_output_tokens)
        if known is not None:
            return known
        # Price unknown: assume a mid-range frontier model, so a budget errs on the safe side.
        return (self.total_input_tokens / 1_000_000) * 3 + (self.total_output_tokens / 1_000_000) * 15

    # ─── provider details shared by every request ───────────────

    def _openai_request_extras(self) -> dict[str, Any]:
        """max_tokens, plus DeepSeek's thinking switch (it defaults to on server-side)."""
        extras: dict[str, Any] = {"max_tokens": _get_max_output_tokens(self.model)}
        if models.is_deepseek(self.model):
            mode = "disabled" if self._thinking_mode == "disabled" else "enabled"
            extras["extra_body"] = {"thinking": {"type": mode}}
        return extras

    def _record_openai_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        self.total_input_tokens += usage.get("prompt_tokens") or 0
        self.total_output_tokens += usage.get("completion_tokens") or 0
        self.total_cache_hit_tokens += usage.get("cache_hit_tokens") or 0
        self.last_input_token_count = usage.get("prompt_tokens") or 0

    @staticmethod
    def _openai_usage_dict(usage: Any) -> dict[str, int]:
        """prompt/completion tokens and cache hits from an OpenAI-style usage object.

        DeepSeek reports cache hits as prompt_cache_hit_tokens; OpenAI as
        prompt_tokens_details.cached_tokens.
        """
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0}
        hit = getattr(usage, "prompt_cache_hit_tokens", None)
        if hit is None:
            details = getattr(usage, "prompt_tokens_details", None)
            hit = getattr(details, "cached_tokens", None) if details is not None else None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "cache_hit_tokens": int(hit or 0),
        }

    def _anthropic_thinking_param(self, max_output: int) -> dict[str, Any]:
        """The `thinking` request field, or nothing.

        Claude: only when thinking is on. DeepSeek's Anthropic endpoint thinks by
        default, so there it is always sent, explicitly on or off.
        """
        if self._thinking_mode in ("adaptive", "enabled"):
            return {"thinking": {"type": "enabled", "budget_tokens": max(1024, max_output - 1)}}
        if models.is_deepseek(self.model):
            return {"thinking": {"type": "disabled"}}
        return {}

    def _record_anthropic_usage(self, usage: Any) -> None:
        if usage is None:
            return
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        # Anthropic's input_tokens excludes cached tokens; count the whole prompt.
        prompt = (getattr(usage, "input_tokens", 0) or 0) + cache_read + cache_write
        self.total_input_tokens += prompt
        self.total_output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.total_cache_hit_tokens += cache_read
        self.last_input_token_count = prompt

    def _sub_agent_kwargs(self) -> dict[str, Any]:
        """Endpoint, key and model for a sub-agent: the same ones this agent uses."""
        return {
            "model": self.model,
            "api_base": self._api_base if self.use_openai else None,
            "anthropic_base_url": self._anthropic_base_url if not self.use_openai else None,
            "api_key": self._api_key,
            "thinking": self.thinking,
        }

    #检查预算
    def _check_budget(self) -> dict:
        if self.max_cost_usd is not None and self._get_current_cost_usd() >= self.max_cost_usd:
            return {"exceeded": True, "reason": f"Cost limit reached (${self._get_current_cost_usd():.4f} >= ${self.max_cost_usd})"}
        if self.max_turns is not None and self.current_turns >= self.max_turns:
            return {"exceeded": True, "reason": f"Turn limit reached ({self.current_turns} >= {self.max_turns})"}
        if self._loop_guard_hits >= LOOP_GUARD_MAX_HITS:
            return {"exceeded": True,
                    "reason": f"Loop guard tripped {self._loop_guard_hits} times and the model kept calling tools"}
        return {"exceeded": False}

    _BUDGET_FINALIZE_PROMPT = (
        "You have reached the step limit. Do not call any more tools. Give your "
        "best final answer from the information you already have, on a line "
        "starting with 'ANSWER:'."
    )

    _GUARD_FINALIZE_PROMPT = (
        "You kept repeating tool calls that returned nothing new, so the run is "
        "being stopped here. Do not call any more tools. Say what you changed and "
        "what is still unverified, then give your best final answer on a line "
        "starting with 'ANSWER:'."
    )

    def _finalize_prompt(self) -> str:
        return self._GUARD_FINALIZE_PROMPT if self._loop_guard_hits >= LOOP_GUARD_MAX_HITS else self._BUDGET_FINALIZE_PROMPT

    def _should_retry_empty_reply(self, text: object) -> bool:
        """True when the model said nothing at all and there are retries left.

        Without this an empty completion ends the turn silently, as if the task
        were finished (more-itertools #1308 in the case study: one tool call,
        then an empty reply, then nothing).
        """
        if self._aborted or str(text or "").strip():
            return False
        if self._empty_replies >= EMPTY_REPLY_RETRIES:
            print_warning(f"The model returned an empty reply {self._empty_replies + 1} times; ending the turn.")
            return False
        self._empty_replies += 1
        print_info(f"The model returned an empty reply; asking again ({self._empty_replies}/{EMPTY_REPLY_RETRIES}).")
        return True

    def _note_truncation(self) -> bool:
        """A reply was cut off mid tool call. True: tell the model and go on; False: end the turn."""
        if self._truncations >= TRUNCATION_RETRIES:
            print_warning("The model's replies keep hitting the output limit; ending the turn. "
                          "Raise KEELSON_MAX_OUTPUT_TOKENS or ask for a smaller step.")
            return False
        self._truncations += 1
        print_info(f"The reply hit the output limit mid tool call; that call was not run "
                   f"({self._truncations}/{TRUNCATION_RETRIES}).")
        return True

    async def _finalize_on_budget_openai(self, pending_tool_calls: list | None) -> None:
        """Turn cap hit mid tool-call: satisfy the dangling tool_calls (else the
        history is malformed and the next request 400s) and make one tool-less
        call for a best-effort answer instead of returning nothing."""
        for tc in pending_tool_calls or []:
            if isinstance(tc, dict) and tc.get("type") == "function":
                self._openai_messages.append({
                    "role": "tool", "tool_call_id": tc.get("id"),
                    "content": "Tool execution skipped: the run is being stopped.",
                })
        if not paths.env_flag("KEELSON_FINAL_ANSWER_ON_BUDGET", default=True):
            return
        self._openai_messages.append({"role": "user", "content": self._finalize_prompt()})
        base = {"model": self.model, "messages": _sanitize_for_utf8(self._openai_messages),
                **self._openai_request_extras()}
        try:
            try:
                resp = await self._openai_client.chat.completions.create(
                    tools=_sanitize_for_utf8(_to_openai_tools(get_active_tool_definitions(self.tools))),
                    tool_choice="none", **base,
                )
            except Exception:  # some gateways reject tool_choice=none / need tools omitted
                resp = await self._openai_client.chat.completions.create(**base)
            msg = resp.choices[0].message if resp.choices else None
            txt = (msg.content or "") if msg else ""
            if resp.usage:
                self._record_openai_usage(self._openai_usage_dict(resp.usage))
            if txt:
                self._emit_text("\n" + str(txt))
                final: dict[str, Any] = {"role": "assistant", "content": str(txt)}
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning:
                    final["reasoning_content"] = _safe_utf8_text(reasoning)
                self._openai_messages.append(final)
        except Exception as e:
            logging.warning("budget finalize (openai) failed: %s", e)

    async def _finalize_on_budget_anthropic(self) -> None:
        if not paths.env_flag("KEELSON_FINAL_ANSWER_ON_BUDGET", default=True):
            return
        self._anthropic_messages.append({"role": "user", "content": self._finalize_prompt()})
        try:
            resp = await self._anthropic_client.messages.create(
                model=self.model,
                max_tokens=_get_max_output_tokens(self.model),
                system=_safe_utf8_text(self._system_prompt),
                messages=_sanitize_for_utf8(self._anthropic_messages),
                **self._anthropic_thinking_param(_get_max_output_tokens(self.model)),
            )
            txt = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
            self._record_anthropic_usage(resp.usage)
            if txt:
                self._emit_text("\n" + txt)
                kept = [self._block_to_dict(b) for b in resp.content if getattr(b, "type", "") != "tool_use"]
                self._anthropic_messages.append({"role": "assistant", "content": kept})
        except Exception as e:
            logging.warning("budget finalize (anthropic) failed: %s", e)

    #压缩会话
    async def compact(self)->None:
        compacted = await self._compact_conversation(trigger="manual")
        if not compacted:
            print_info("Nothing to compact yet.")


    #恢复会话信息
    def restore_session(self, data:dict)->None:
        if data.get("anthropicMessages"):
            self._anthropic_messages = self._normalize_anthropic_messages(_sanitize_for_utf8(data["anthropicMessages"]))
        if data.get("openaiMessages"):
            self._openai_messages = _sanitize_for_utf8(data["openaiMessages"])
        if isinstance(data.get("foldedSessionMemories"), list):
            self._folded_session_memories = _sanitize_for_utf8(data["foldedSessionMemories"])
        if isinstance(data.get("ledger"), dict):
            self._ledger = ledger_mod.Ledger.from_dict(data["ledger"])
        print_info(f"Session restored ({self._get_message_count()} messages).")



#整理 Anthropic 的历史消息，修正部分角色错误，并丢弃不合法的工具调用消息。
    def _normalize_anthropic_messages(self, messages: list[dict]) -> list[dict]:
        role_normalized = []
        for msg in messages:
            copied = dict(msg)
            content = copied.get("content")
            if copied.get("role") == "user" and isinstance(content, list):
                if any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content):
                    copied["role"] = "assistant"
            role_normalized.append(copied)

        normalized = []
        i = 0
        while i < len(role_normalized):
            msg = role_normalized[i]
            tool_use_ids = self._anthropic_tool_use_ids(msg)
            if not tool_use_ids:
                normalized.append(msg)
                i += 1
                continue

            next_msg = role_normalized[i + 1] if i + 1 < len(role_normalized) else None
            result_ids = self._anthropic_tool_result_ids(next_msg) if next_msg else set()
            if tool_use_ids.issubset(result_ids):
                normalized.append(msg)
                normalized.append(next_msg)
                i += 2
                continue

            i += 1
        return normalized

    @staticmethod
    def _anthropic_tool_use_ids(msg: dict | None) -> set[str]:
        if not msg or msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
            return set()
        return {
            block.get("id")
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
        }

    @staticmethod
    def _anthropic_tool_result_ids(msg: dict | None) -> set[str]:
        if not msg or msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            return set()
        return {
            block.get("tool_use_id")
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
        }

    def _get_message_count(self) -> int:
        return len(self._openai_messages) if self.use_openai else len(self._anthropic_messages)

    def _auto_save(self) -> None:
        try:
            save_session(self.session_id, {
                "metadata": {
                    "id": self.session_id,
                    "model": self.model,
                    "cwd": str(Path.cwd()),
                    "startTime": self.session_start_time,
                    "messageCount": self._get_message_count(),
                    "title": (self._ledger.goal or "")[:80],
                    "updated": time.strftime("%Y-%m-%d %H:%M"),
                },
                "anthropicMessages": _sanitize_for_utf8(self._anthropic_messages) if not self.use_openai else None,
                "openaiMessages": _sanitize_for_utf8(self._openai_messages) if self.use_openai else None,
                "foldedSessionMemories": _sanitize_for_utf8(self._folded_session_memories),
                "ledger": _sanitize_for_utf8(self._ledger.to_dict()),
            })
        except Exception:
            pass

    #自动压缩
    async def _check_and_compact(self)->None:
        if self.last_input_token_count > self.effective_window * AUTO_COMPACT_THRESHOLD:
            print_info("Context window filling up, compacting conversation...")
            await self._compact_conversation(trigger="auto")

    async def _compact_conversation(self, *, trigger: str = "manual")->bool:
        with events.span("fold", trigger=trigger):
            if self.use_openai:
                compacted = await self._compact_openai(trigger=trigger)
            else:
                compacted = await self._compact_anthropic(trigger=trigger)
        if compacted:
            kept = self._ledger.kept_summary()
            events.emit(events.CONTEXT_FOLD, trigger=trigger, fold_count=self._fold_count, kept=kept)
            print_info("Conversation compacted." + (f" Kept word for word: {kept}." if kept else ""))
        return compacted

    async def _compact_anthropic(self, *, trigger: str)->bool:
        if len (self._anthropic_messages)<4:
            return False

        transcript = build_anthropic_transcript(_sanitize_for_utf8(self._anthropic_messages))
        if not transcript.strip():
            return False
        memory = await self._generate_folded_session_memory(transcript)
        self._record_folded_session_memory(trigger, memory)
        self._record_fold_event()
        self._anthropic_messages = [{"role": "user", "content": self._folded_content(memory)}]
        self.last_input_token_count = 0
        self._after_fold()
        return True

    async def _compact_openai(self, *, trigger: str)->bool:
        if len (self._openai_messages)<4:
            return False
        system_msg = self._openai_messages[0]
        transcript = build_openai_transcript(_sanitize_for_utf8(self._openai_messages))
        if not transcript.strip():
            return False
        memory = await self._generate_folded_session_memory(transcript)
        self._record_folded_session_memory(trigger, memory)
        self._record_fold_event()
        self._openai_messages=[
            system_msg,
            {"role": "user", "content": self._folded_content(memory)},
        ]
        self.last_input_token_count=0
        self._after_fold()
        return True

    def _folded_content(self, memory: dict[str, Any]) -> str:
        kept = self._fold_kept_note()
        return format_folded_memory(memory) + ("\n\n" + kept if kept else "")

    def _after_fold(self) -> None:
        self._refresh_runtime_system_prompt()
        self._last_git_note = None          # the folded history no longer has it; send it with the next message
        self._fold_hints.clear()

    async def _generate_folded_session_memory(self, transcript: str) -> dict[str, Any]:
        side_query = self._build_side_query(max_tokens=6000)
        if side_query is None:
            return fallback_folded_memory(transcript)
        try:
            raw = await side_query(FOLD_SESSION_MEMORY_SYSTEM, build_folding_user_prompt(transcript))
            return parse_folded_memory(raw)
        except Exception:
            return fallback_folded_memory(transcript)

    def _record_folded_session_memory(self, trigger: str, memory: dict[str, Any]) -> None:
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "trigger": trigger,
            "session_id": self.session_id,
            **memory,
        }
        self._folded_session_memories.append(record)
        try:
            save_folded_session_memory(self.session_id, _sanitize_for_utf8(record))
        except Exception:
            pass

    #多层级压缩流水线
    def _run_compression_pipeline(self)->None:
        if self.use_openai:
            self._budget_tool_results_openai()
            self._snip_stale_results_openai()
            self._microcompact_openai()
        else:
            self._budget_tool_results_anthropic()
            self._snip_stale_results_anthropic()
            self._microcompact_anthropic()

    #第一层级压缩，预算压缩
    def _budget_tool_results_anthropic(self)->None:
        #计算利用率：utilization = 已用Token / 有效窗口大小。
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        #如果利用率低于 50%，说明空间还很充裕，直接返回，不做任何处理。
        if utilization < 0.5:
            return
        #动态预算（Budget）：危急状态（>70%）：如果利用率很高，允许单个工具结果保留 15,000 个字符。
        # 警戒状态（50%-70%）：如果利用率中等，只允许保留 30000 个字符。
        budget = 15000 if utilization > 0.7 else 30000

        for msg in self._anthropic_messages:

            #只处理 role 为 "user" 的消息。在工具调用流程中，工具的执行结果通常是以“用户”的身份反馈给模型的。

            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and len(block["content"]) > budget:
                    #计算保留长度 (keep)：keep = (budget - 80) // 2 这里预留了约 80 个字符的空间给中间的提示语，剩下的长度平分给开头和结尾。
                    keep = (budget - 80) // 2
                    #重组新内容 = 开头部分 + 提示语 + 结尾部分
                    block["content"] = block["content"][:keep] + f"\n\n[... budgeted: {len(block['content']) - keep * 2} chars truncated ...]\n\n" + block["content"][-keep:]

    def _budget_tool_results_openai(self)->None:
        #计算利用率：utilization = 已用Token / 有效窗口大小。
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        #如果利用率低于 50%，说明空间还很充裕，直接返回，不做任何处理。
        if utilization < 0.5:
            return
        #动态预算（Budget）：危急状态（>70%）：如果利用率很高，允许单个工具结果保留 15,000 个字符。
        # 警戒状态（50%-70%）：如果利用率中等，只允许保留 30000 个字符。
        budget = 15000 if utilization > 0.7 else 30000

        for msg in self._openai_messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and len(msg["content"]) > budget:
                keep = (budget - 80) // 2
                msg["content"] = msg["content"][:keep] + f"\n\n[... budgeted: {len(msg['content']) - keep * 2} chars truncated ...]\n\n" + msg["content"][-keep:]


    #第二级策略：修剪过期的工具执行结果
    def _snip_stale_results_anthropic(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return
        results = []
        for mindex,  msg in enumerate(self._anthropic_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue

            for bindex, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] != SNIP_PLACEHOLDER:
                    tool_use_id = block.get("tool_use_id")
                    # 对每个 tool_result，通过 tool_use_id 反查它来自哪个工具
                    tool_info = self._find_tool_use_by_id(tool_use_id)
                    if tool_info and tool_info["name"] in SNIPPABLE_TOOLS:
                        results.append({"mindex": mindex, "bindex": bindex, "name": tool_info["name"], "file_path": tool_info.get("input", {}).get("file_path")})

        if len(results) <= KEEP_RECENT_RESULTS:
            return

        to_snip =  set()
        seen_files: dict[str, list[int]] = {}

        for i, r in enumerate(results):
            if r["name"] == "read_file" and r.get("file_path"):
                seen_files.setdefault(r["file_path"], []).append(i)
        #如果一个文件被读取了多次，只保留最后一次读取的结果，把前面几次读取的内容全部标记为“修剪”（Snip）。
        for indices in seen_files.values():
            if len (indices) >1 :
                for j in indices[:-1]:
                    to_snip.add (j)

        snip_before = len(results) - KEEP_RECENT_RESULTS
        for i in range (snip_before):
            to_snip.add(i)

        for idx in to_snip:
            r = results[idx]
            self._anthropic_messages[r["mindex"]]["content"][r["bindex"]]["content"] = SNIP_PLACEHOLDER

    def _snip_stale_results_openai(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return
        tool_msgs = []
        for i, msg in enumerate(self._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] != SNIP_PLACEHOLDER:
                tool_msgs.append(i)
        if len(tool_msgs) <= KEEP_RECENT_RESULTS:
            return
        snip_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(snip_count):
            self._openai_messages[tool_msgs[i]]["content"] = SNIP_PLACEHOLDER

    #微压缩

    #基于“时间”的上下文瘦身策略，
    #如果已经很久没说话了，说明之前的工具执行结果你已经看完了，那就把它们清理掉，腾出空间

    def _microcompact_anthropic(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return

        all_results = []
        for mindex, msg in enumerate(self._anthropic_messages):
            if msg.get("role")!="user" or not isinstance(msg.get("content"), list):
                continue
            for bindex, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                    all_results.append((mindex, bindex))

        clear_count = len(all_results) - KEEP_RECENT_RESULTS
        for i in range(max(0, clear_count)):
            mi, bi = all_results[i]
            self._anthropic_messages[mi]["content"][bi]["content"] = "[Old result cleared]"

    def _microcompact_openai(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return
        tool_msgs = []
        for i, msg in enumerate(self._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                tool_msgs.append(i)
        clear_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(max(0, clear_count)):
            self._openai_messages[tool_msgs[i]]["content"] = "[Old result cleared]"

    def _find_tool_use_by_id(self, tool_use_id: int) -> dict | None:
        for msg in self._anthropic_messages:
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue

            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                    return {"name": block["name"], "input": block.get("input", {})}

    #大结果持久化
    #如果工具返回的结果太大（超过 30KB），不要硬塞进上下文里，而是把它存成一个临时文件。
    # 然后在对话里只留一个‘文件路径’和‘内容预览’。如果模型后面还需要看完整内容，它可以再次调用工具去读取这个文件

    def _persist_large_result(self, tool_name: str, result: str) -> str:
        THRESHOLD = 30 * 1024  # 30 KB
        #转换成字节
        if (len (result.encode())) <= THRESHOLD:
            return result

        d = paths.tool_results_dir()
        d.mkdir(parents=True, exist_ok=True)
        filename = f"{int(time.time() * 1000)}-{tool_name}.txt"
        filepath = d / filename
        filepath.write_text(result, encoding="utf-8")

        lines = result.split("\n")
        preview = "\n".join(lines[:200])
        size_kb = len(result.encode()) / 1024

        return (
            f"[Result too large ({size_kb:.1f} KB, {len(lines)} lines). "
            f"Full output saved to {filepath}. "
            f"You can use read_file to see the full result.]\n\n"
            f"Preview (first 200 lines):\n{preview}"
        )

    #执行工具入口

    def _subagent_permission_mode(self) -> str:
        """A sub-agent is never more permissive than its parent.

        plan -> plan; bypassPermissions -> bypassPermissions (parent opted in
        via --yolo); anything else -> KEELSON_SUBAGENT_MODE (default 'dontAsk':
        sub-agents run headless, so prompts become denials not silent allows).
        """
        if self.permission_mode in ("plan", "bypassPermissions"):
            return self.permission_mode
        return paths.env_value("KEELSON_SUBAGENT_MODE", default="dontAsk") or "dontAsk"

    def _emit_usage(self) -> None:
        if self.is_sub_agent:
            return
        events.emit(
            events.USAGE,
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            context_tokens=self.last_input_token_count,
            context_window=self.effective_window,
            turns=self.current_turns,
            cache_hit_tokens=self.total_cache_hit_tokens,
            cost_usd=round(self._get_current_cost_usd(), 6),
        )

    @staticmethod
    def _emit_permission(call_id: str | None, name: str, perm: dict, *, speculative: bool = False) -> None:
        """Publish the gate's verdict for one tool call (allow / deny / confirm)."""
        events.emit(
            events.PERMISSION,
            id=call_id,
            name=name,
            action=perm.get("action", "allow"),
            message=perm.get("message", ""),
            speculative=speculative,
        )

    async def _run_tool(self, call_id: str | None, name: str, inp: dict) -> str:
        """Execute one tool call, tagged and timed.

        The call id travels in a ContextVar rather than as an argument, so
        anything emitted while the tool runs (live shell output, the span) is
        attributed to this call — including when several run concurrently,
        because each task carries its own copy of the context.
        """
        with events.tool_call(call_id), events.span("tool", tool=name, id=call_id):
            result = await self._execute_tool_call(name, inp)
        self._record_evidence(name, inp, result)
        return result

    def _gate(self, call_id: str | None, name: str, inp: dict) -> dict:
        """Ask the permission gate about one call (timed, to show what the gate costs)."""
        with events.span("permission", tool=name, id=call_id):
            perm = check_permission(name, inp, self.permission_mode, self._plan_file_path, self._session_rules)
            if perm["action"] == "allow":
                perm = self._test_guard(name, inp) or self._scope_guard(name, inp) or perm
            return perm

    async def _execute_tool_call(self, name: str, inp: dict) -> str:
        if name == "compact_context":
            return await self._execute_compact_context_tool(inp)
        if name in ("enter_plan_mode", "exit_plan_mode"):
            return await self._execute_plan_mode_tool(name)
        if name == "agent":
            return await self._execute_agent_tool(inp)
        if name == "skill":
            return await self._execute_skill_tool(inp)
        if name == "update_task":
            return self._update_task(inp)
        if name == "declare_scope":
            return self._declare_scope(inp)

        # Loop guard — check before executing, but after the control tools above
        # (which legitimately repeat). Decided pre-await so speculative / gather
        # batches can't race past it.
        guard = self._loop_guard_check(name, inp)
        if guard is not None:
            self._loop_guard_hits += 1
            events.emit(events.LOOP_GUARD, name=name, hits=self._loop_guard_hits)
            return guard

        if name in self._external_tools:
            fn = self._external_tools[name]
            out = fn(name, inp)
            result = await out if inspect.isawaitable(out) else out
        elif self._mcp_manager.is_mcp_tool(name):
            result = await self._mcp_manager.call_tool(name, inp)
        else:
            result = await execute_tool(name, inp, self._read_file_state)
            if name in {"skill_create", "skill_evolve"}:
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and parsed.get("ok"):
                        self._refresh_runtime_system_prompt()
                except Exception:
                    pass

        self._loop_window.append({"name": _guard_key(name, inp), "result_key": _result_key(result)})
        if len(self._loop_window) > LOOP_GUARD_WINDOW * 4:
            self._loop_window = self._loop_window[-LOOP_GUARD_WINDOW * 2:]
        return result

    def _loop_guard_check(self, name: str, inp: dict | None = None) -> str | None:
        """Nudge (and skip execution) when one tool name fills most of the recent
        window and its results have stopped diverging — the arg-fuzzing spiral."""
        if name in LOOP_GUARD_EXCLUDE:
            return None
        if not paths.env_flag("KEELSON_LOOP_GUARD", default=True):
            return None
        key = _guard_key(name, inp)
        recent = self._loop_window[-LOOP_GUARD_WINDOW:]
        same = [e for e in recent if e["name"] == key]
        if len(same) < LOOP_GUARD_SAME_NAME:
            return None
        if len({e["result_key"] for e in same}) > LOOP_GUARD_DISTINCT_RESULTS:
            return None  # still producing new information — let it run
        return (
            f"Loop guard: '{name}' was called {len(same)} times in the last "
            f"{len(recent)} tool calls and the results are no longer changing. "
            f"Stop calling tools and give your best final answer now, on a line "
            f"starting with 'ANSWER:'."
        )

    async def _execute_compact_context_tool(self, inp: dict) -> str:
        reason = str(inp.get("reason") or "").strip()
        compacted = await self._compact_conversation(trigger="tool")
        if not compacted:
            self._record_tool_outcome("compact_context", False)
            return "No context compaction was performed because there is not enough conversation history yet."
        self._record_tool_outcome("compact_context", True)
        self._context_cleared = True
        suffix = f"\nReason: {reason}" if reason else ""
        return (
            "Context compacted into structured session memory. "
            "Continue from the folded memory now present in the conversation context."
            f"{suffix}"
        )


    async def _execute_skill_tool(self, inp: dict) -> str:
        from .skills import execute_skill
        result = execute_skill(inp.get("skill_name", ""), inp.get("args", ""))

        if not result:
            return f"Unknown skill: {inp.get('skill_name', '')}"

        #fork 表示这个 skill 不直接把 prompt 塞回当前对话，而是要启动一个子 Agent 单独完成任务。
        if result["context"] == "fork":
            # result["allowed_tools"] - 直接访问
            tools = (
                [t for t in self.tools if t["name"] in  result["allowed_tools"] ]
                #result.get("allowed_tools") - 安全访问
                # 存在key：返回对应的值（可能是 None、[]、["tool1"] 等）
                # 不存在key：返回 None（不会抛异常）
                if result.get("allowed_tools")
                else  [t for t in self.tools if t["name"] != "agent"]
            )

            print_sub_agent_start("skill-fork", inp.get("skill_name", ""))
            sub_agent = Agent(
                **self._sub_agent_kwargs(),
                custom_system_prompt=result["prompt"],
                custom_tools=tools,
                is_sub_agent=True,
                permission_mode=self._subagent_permission_mode(),
            )
            try:
                sub_result = await sub_agent.run_once(inp.get("args") or "Execute this skill task.")
                self.total_input_tokens += sub_result["tokens"]["input"]
                self.total_output_tokens += sub_result["tokens"]["output"]
                print_sub_agent_end("skill-fork", inp.get("skill_name", ""))
                return sub_result["text"] or "(Skill produced no output)"
            except Exception as e:
                print_sub_agent_end("skill-fork", inp.get("skill_name", ""))
                return f"Skill fork error: {e}"

        return f'[Skill "{inp.get("skill_name", "")}" activated]\n\n{result["prompt"]}'

    async def _execute_plan_mode_tool(self, name):
        if name == "enter_plan_mode":
            if self.permission_mode == "plan":
                return "Already in plan mode."
            self._pre_plan_mode = self.permission_mode
            self.permission_mode = "plan"
            self._plan_file_path =  self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info("Entered plan mode (read-only). Plan file: " + self._plan_file_path)
            return f"Entered plan mode. You are now in read-only mode.\n\nYour plan file: {self._plan_file_path}\nWrite your plan to this file. This is the only file you can edit.\n\nWhen your plan is complete, call exit_plan_mode."
        if name == "exit_plan_mode":
            if self.permission_mode != "plan":
                return "Not in plan mode."
            plan_content = "(No plan file found)"
            if self._plan_file_path and Path(self._plan_file_path).exists():
                try:
                    plan_content = Path(self._plan_file_path).read_text(encoding="utf-8")
                except Exception as e:
                    plan_content = f"(Failed to read plan file {self._plan_file_path}: {e})"
            # 交互式审批流程（如果有审批函数）
            if self._plan_approval_fn:
                result = self._plan_approval_fn(plan_content)
                if inspect.isawaitable(result):
                    result = await result
                choice = result.get("choice", "manual-execute")

                if choice =="keep-planning":
                    feedback = result.get("feedback") or "Please revise the plan."
                    return (
                        f"User rejected the plan and wants to keep planning.\n\n"
                        f"User feedback: {feedback}\n\n"
                        f"Please revise your plan based on this feedback. When done, call exit_plan_mode again."
                    )

                if choice == "clear-and-execute":
                    target_mode = "acceptEdits"
                elif choice == "execute":
                    target_mode = "acceptEdits"
                else:  # manual-execute
                    target_mode = self._pre_plan_mode or "default"

                #离开计划模式：把权限模式切到审批结果，再清掉进入 plan 前的备份
                self.permission_mode = target_mode
                self._pre_plan_mode = None
                saved_plan_path = self._plan_file_path
                self._plan_file_path = None
                self._system_prompt = self._base_system_prompt
                if self.use_openai and self._openai_messages:
                    self._openai_messages[0]["content"] = self._system_prompt

                if choice == "clear-and-execute":
                    self._clear_history_keep_system()
                    self._context_cleared = True
                    print_info(f"Plan approved. Context cleared, executing in {target_mode} mode.")
                    return (
                        f"User approved the plan. Context was cleared. Permission mode: {target_mode}\n\n"
                        f"Plan file: {saved_plan_path}\n\n"
                        f"## Approved Plan:\n{plan_content}\n\n"
                        f"Proceed with implementation."
                    )
                print_info(f"Plan approved. Executing in {target_mode} mode.")
                return (
                    f"User approved the plan. Permission mode: {target_mode}\n\n"
                    f"## Approved Plan:\n{plan_content}\n\n"
                    f"Proceed with implementation."
                )
            # 没有审批函数时的回退（例如子代理）
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._system_prompt = self._base_system_prompt
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt

            print_info("Exited plan mode. Restored to " + self.permission_mode + " mode.")
            return f"Exited plan mode. Permission mode restored to: {self.permission_mode}\n\n## Your Plan:\n{plan_content}"

        return f"Unknown plan mode tool: {name}"

    def _clear_history_keep_system(self) -> None:
        """清空历史信息，但是保留系统prompt."""
        self._anthropic_messages = []
        self._openai_messages = []
        if self.use_openai:
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        self.last_input_token_count = 0
        self._fold_last_time = 0.0
        self._fold_count = 0
        self._tool_error_streak = 0
        self._same_tool_repeat_count = 0
        self._last_tool_name = ""
        self._loop_window = []

    async def _execute_agent_tool(self, inp:dict) -> str:
        agent_type = inp.get("type", "general")
        description = inp.get("description", "sub-agent task")
        prompt = inp.get("prompt", "")
        print_sub_agent_start(agent_type, description)

        config = get_sub_agent_config(agent_type)

        sub_agent = Agent(
            **self._sub_agent_kwargs(),
            custom_system_prompt=config["system_prompt"],
            custom_tools=config["tools"],
            is_sub_agent=True,
            permission_mode=self._subagent_permission_mode(),
        )
        try:
            result = await sub_agent.run_once(prompt)
            self.total_input_tokens += result["tokens"]["input"]
            self.total_output_tokens += result["tokens"]["output"]
            print_sub_agent_end(agent_type, description)
            return result["text"] or "(Sub-agent produced no output)"
        except Exception as e:
            print_sub_agent_end(agent_type, description)
            return f"Sub-agent error: {e}"

#--------------Anthropic 后端---------------
    async def  _chat_anthropic(self, user_message: str) -> None:
        self._anthropic_messages = self._normalize_anthropic_messages(_sanitize_for_utf8(self._anthropic_messages))
        user_message = _safe_utf8_text(user_message)
        # 先把本轮用户输入放入 Anthropic 消息历史，后续每轮模型调用都会带上这段上下文。
        self._anthropic_messages.append({"role": "user", "content": user_message})

        # 异步内存预取：主 agent 才需要查 memory，sub agent 不额外注入记忆。
        # 这里只启动后台任务，不阻塞当前模型调用流程。
        memory_prefetch:MemoryPrefetch | None = None
        if not self.is_sub_agent:
            sq = self._build_side_query()
            if sq:
                memory_prefetch = start_memory_prefetch(
                    user_message, sq,
                    self._already_surfaced_memories, self._session_memory_bytes,
                )
        while True:
            # 外部请求中止时，结束整个 agent loop。
            if self._aborted:
                break

            # 每轮调用模型前尝试压缩上下文，避免消息历史过长。
            self._run_compression_pipeline()

            # 如果记忆预取任务已经完成，就把取回来的 memory 内容追加到最后一条用户消息里。
            # consumed 用来保证同一批 memory 只注入一次。
            if memory_prefetch and memory_prefetch.settled and not memory_prefetch.consumed:
                memory_prefetch.consumed = True
                try:
                    memories = memory_prefetch.task.result()
                    if memories:
                        injection_text = format_memories_for_injection(memories)
                        injection_text = _safe_utf8_text(injection_text)
                        last = self._anthropic_messages[-1] if self._anthropic_messages else None
                        if last and last.get("role") == "user":
                            content = last.get("content", "")
                            if isinstance(content, str):
                                # 字符串不可变，需要重新赋值回 message。
                                last["content"] = content + "\n\n" + injection_text
                            elif isinstance(content, list):
                                # list 是可变对象，append 会直接修改 last["content"] 指向的列表。
                                content.append({"type": "text", "text": injection_text})
                        else:
                            # 如果最后一条不是 user message，就单独追加一条用户消息承载 memory。
                            self._anthropic_messages.append({"role": "user", "content": injection_text})

                        for m in memories:
                            # 记录本 session 已经注入过的 memory，后续检索时可避免重复 surfaced。
                            self._already_surfaced_memories.add(m.path)
                            self._session_memory_bytes += m.size
                except:
                    # memory 注入失败不应该中断主对话流程。
                    pass

            if not self.is_sub_agent:
                start_spinner()


            # 保存“提前执行”的工具任务。key 是 Anthropic 返回的 tool_use block id。
            early_executions: dict[str, asyncio.Task] = {}


            def _on_tool_block(block:dict):
                # 流式响应中一旦完整收到 tool_use block，如果工具是并发安全且权限允许，
                # 就可以提前开始执行；KEELSON_SPECULATIVE=0 关闭它用于延迟 A/B。
                if not trace.speculative_enabled():
                    return
                if block["name"] in CONCURRENCY_SAFE_TOOLS:
                    perm = self._gate(block["id"], block["name"], block["input"])
                    if perm["action"]=="allow":
                        self._emit_permission(block["id"], block["name"], perm, speculative=True)
                        task =asyncio.create_task(self._run_tool(block["id"], block["name"], block["input"]))
                        early_executions[block["id"]] = task


            # 调用 Anthropic 流式接口；流式过程中完成 tool block 时会触发 _on_tool_block。
            with events.span("model_stream", turn=self.current_turns, backend="anthropic"):
                response = await self._call_anthropic_stream(on_tool_block_complete=_on_tool_block)
            if not self.is_sub_agent:
                stop_spinner()

            # 记录本次模型调用的耗时点和 token 消耗，用于成本展示与预算控制。
            self.last_api_call_time = time.time()
            self._record_anthropic_usage(response.usage)
            self._emit_usage()

            # Anthropic 的响应内容里可能混有 text block 和 tool_use block，这里只挑出工具调用。
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            if tool_uses and getattr(response, "stop_reason", None) == "max_tokens":
                # The last tool_use may hold half its JSON. Run nothing from this reply.
                for task in early_executions.values():
                    task.cancel()
                kept = [self._block_to_dict(b) for b in response.content if b.type != "tool_use"]
                if kept:
                    self._anthropic_messages.append({"role": "assistant", "content": kept})
                if not self._note_truncation():
                    break
                self._anthropic_messages.append({"role": "user", "content": TRUNCATED_TOOL_CALL_NOTE})
                continue

            # 把模型返回的所有 content block（含 thinking 及其签名）写入消息历史：
            # 带工具的请求必须把 thinking 原样传回，否则 DeepSeek 返回 400。
            self._anthropic_messages.append({
                "role": "assistant",
                "content": [self._block_to_dict(b) for b in response.content],
            })

            # 没有工具调用，说明模型已经给出最终回复，本轮对话结束。
            if not tool_uses:
                said = "".join(getattr(b, "text", "") or "" for b in response.content if b.type == "text")
                if self._should_retry_empty_reply(said):
                    self._anthropic_messages.pop()
                    self._anthropic_messages.append({"role": "user", "content": EMPTY_REPLY_NUDGE})
                    continue
                followup = await self._end_of_turn_check(said)
                if followup:
                    self._anthropic_messages.append({"role": "user", "content": followup})
                    continue
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens, self._get_current_cost_usd())
                break

            # 有工具调用时，进入下一轮工具执行。这里同时检查 turn/budget 限制。
            self.current_turns += 1
            budget = self._check_budget()
            if budget["exceeded"]:
                print_info(f"Budget exceeded: {budget['reason']}")
                self._anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": f"Tool execution skipped: {budget['reason']}",
                        }
                        for tu in tool_uses
                    ],
                })
                await self._finalize_on_budget_anthropic()
                break


            # 收集本轮所有工具结果，之后作为 tool_result 消息回传给模型。
            tool_results: list[dict] = []
            context_break = False

            for tu in tool_uses:
                # context_break 表示某个工具执行期间清理了上下文，需要停止继续处理本轮剩余工具。
                if context_break or self._aborted:
                    break

                # 将工具入参转为普通 dict，便于权限检查、打印和实际执行。
                inp = dict(tu.input) if hasattr(tu, "items") else tu.input
                print_tool_call(tu.name, inp, tu.id)

                # 如果这个工具已经在流式阶段提前开始执行，这里只需要等待它完成并收集结果。
                early_task = early_executions.get(tu.id)
                if early_task:
                    try:
                        raw = await early_task
                    except Exception as e:
                        raw = f"Error executing tool: {e}"
                    raw = _safe_utf8_text(raw)
                    res = self._persist_large_result(tu.name, raw)
                    print_tool_result(tu.name, res, tu.id)
                    self._record_tool_outcome(tu.name, not self._looks_like_tool_failure(tu.name, raw, res))
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": res})
                    continue

                # 如果不是提前执行的工具，就在真正执行前做权限检查。

                perm = self._gate(tu.id, tu.name, inp)
                self._emit_permission(tu.id, tu.name, perm)
                if perm["action"] == "deny":
                    # 权限拒绝时，也要返回一个 tool_result，让模型知道该工具调用失败的原因。
                    self._record_tool_outcome(tu.name, False)
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                         "content": f"Action denied: {perm.get('message', '')}"})
                    continue

                if perm["action"] == "confirm":
                    # 撤销不了的操作要用户确认；可选"本次 / 本会话 / 本项目 / 拒绝（可附理由）"。
                    denial = await self._ask_user(tu.id, tu.name, perm)
                    if denial is not None:
                        self._record_tool_outcome(tu.name, False)
                        tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": denial})
                        continue

                # 权限通过后执行工具，并把大输出持久化为可回传的摘要或引用。
                try:
                    raw = await self._run_tool(tu.id, tu.name, inp)
                except Exception as e:
                    raw = f"Error executing tool: {e}"
                raw = _safe_utf8_text(raw)
                res = self._persist_large_result(tu.name, raw)
                print_tool_result(tu.name, res, tu.id)
                self._record_tool_outcome(tu.name, not self._looks_like_tool_failure(tu.name, raw, res))

                if self._context_cleared:
                    # 工具执行过程中如果清理了上下文，就把结果作为新的用户消息写入，
                    # 并停止继续处理本轮剩余工具，避免旧上下文和新上下文混在一起。
                    self._context_cleared = False
                    self._anthropic_messages.append({"role": "user", "content": res})
                    context_break = True
                    break

                # Anthropic 要求 tool_result 使用 tool_use_id 对应到前面的 tool_use block。
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": res})

            if not context_break and tool_results:
                # Anthropic 要求 assistant/tool_use 后面紧跟一条 user/tool_result 消息，
                # 且这条消息必须包含本轮所有 tool_use 的对应结果。
                self._anthropic_messages.append({"role": "user", "content": tool_results})

            self._context_cleared = False

            # 工具结果可能很长，每轮工具执行后检查是否需要压缩上下文。
            self._maybe_fold_hint()
            await self._check_and_compact()

    @staticmethod
    def _block_to_dict(block) -> dict:
        if block.type == "text":
            return {"type": "text", "text": _safe_utf8_text(block.text)}
        if block.type == "tool_use":
            raw_input = dict(block.input) if hasattr(block.input, 'items') else block.input
            return {"type": "tool_use", "id": _safe_utf8_text(block.id), "name": _safe_utf8_text(block.name), "input": _sanitize_for_utf8(raw_input)}
        if block.type == "thinking":
            # Sent back byte for byte: the signature covers the text.
            out = {"type": "thinking", "thinking": getattr(block, "thinking", "") or ""}
            signature = getattr(block, "signature", None)
            if signature is not None:
                out["signature"] = signature
            return out
        if block.type == "redacted_thinking":
            return {"type": "redacted_thinking", "data": getattr(block, "data", "")}
        # Fallback
        return {"type": _safe_utf8_text(block.type)}

    async def _call_anthropic_stream(self, on_tool_block_complete=None):

        async def _do():
            max_output =  _get_max_output_tokens(self.model)

            create_params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_output,
                "system": _safe_utf8_text(self._system_prompt),
                "tools": _sanitize_for_utf8(get_active_tool_definitions(self.tools)),
                "messages": _sanitize_for_utf8(self._anthropic_messages),
                **self._anthropic_thinking_param(max_output),
            }

            first_text = True

            tool_blocks_by_index: dict[int, dict] = {}

            async with self._anthropic_client.messages.stream(**create_params)as stream:
                async for event in stream:
                    if not hasattr(event, 'type'):
                        continue
                    # 当事件是工具调用开始：
                    if event.type == "content_block_start":
                        cb = getattr(event, 'content_block', None)
                        #如果 block 类型是 tool_use，就记录这个工具调用：
                        if cb and getattr(cb, 'type', None) == "tool_use":
                            #因为工具参数 JSON 是流式分片返回的，所以先准备一个空字符串 input_json。
                            tool_blocks_by_index[event.index]= {
                                "id": cb.id, "name": cb.name, "input_json": "",
                            }
                    #当事件是内容增量，分三种情况。
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        # 第一种，普通文本：模型输出正文时，
                        # 调用 _emit_text()。如果是普通交互，就打印；
                        # 如果是 run_once()，就写入 _output_buffer。
                        if hasattr(delta, "text"):
                            if first_text:
                                stop_spinner()
                                self._emit_text("\n")
                                first_text = False
                            self._emit_text(delta.text)
                        #第二种，thinking 内容：
                        #如果模型返回思考内容，也输出出来，并在开头加：[thinking]
                        # 只显示在终端，不进输出缓冲：子 Agent 的结果和技能提取不该混入思考过程。
                        elif hasattr(delta, 'thinking'):
                            if self._output_buffer is None:
                                if first_text:
                                    stop_spinner()
                                    print_assistant_text("\n  [thinking] ")
                                    first_text = False
                                print_assistant_text(_safe_utf8_text(delta.thinking))
                        #第三种，工具参数 JSON 片段：工具调用的参数不是一次性返回，
                        # 而是一段一段返回，所以这里不断拼接到 input_json。
                        elif hasattr(delta, 'partial_json'):
                            tb = tool_blocks_by_index.get(event.index)
                            if tb:
                                tb["input_json"] += _safe_utf8_text(delta.partial_json)
                    #当一个 content block 结束：
                    #如果结束的是之前记录的工具调用，就把拼好的 JSON 解析出来：
                    elif event.type == "content_block_stop":
                        tb = tool_blocks_by_index.pop(event.index, None)
                        if tb and on_tool_block_complete:
                            import json as _json
                            try:
                                parsed = _json.loads(tb["input_json"] or "{}")
                            except Exception:
                                parsed = {}
                            #然后调用回调：
                            #这个回调的作用通常是：工具调用一完整，
                            # 就可以提前开始执行工具，不必等整条 assistant 消息全部结束。
                            on_tool_block_complete({
                                "type": "tool_use", "id": _safe_utf8_text(tb["id"]),
                                "name": _safe_utf8_text(tb["name"]), "input": _sanitize_for_utf8(parsed),
                            })
                final_message = await stream.get_final_message()

            # thinking 块不再过滤：带工具的对话里，Claude 和 DeepSeek 都要求把
            # thinking（含签名）原样传回，否则下一次请求返回 400。
            return final_message
#调用 _do()，如果遇到可重试错误，就由 _with_retry() 负责重试。
        return await _with_retry(_do)

    #openAI后端

    async def _chat_openai(self, user_message:str) -> None:
        user_message = _safe_utf8_text(user_message)
        self._openai_messages.append({"role": "user", "content": user_message})

        #预取句柄 MemoryPrefetch
        memory_prefetch: MemoryPrefetch | None = None
        if not self.is_sub_agent:
            sq = self._build_side_query()
            if sq:
                memory_prefetch = start_memory_prefetch(
                    user_message, sq,
                    self._already_surfaced_memories, self._session_memory_bytes,
                )

        while True:
            if self._aborted:
                break

            self._run_compression_pipeline()

            if memory_prefetch and memory_prefetch.settled and not memory_prefetch.consumed:
                memory_prefetch.consumed = True
                try:
                    memories = memory_prefetch.task.result()
                    if memories:
                        injection_text = format_memories_for_injection(memories)
                        injection_text = _safe_utf8_text(injection_text)
                        last = self._openai_messages[-1] if self._openai_messages else None

                        if last and last.get("role") == "user":
                            last["content"] = (last.get("content") or "") + "\n\n" + injection_text
                        else:
                            self._openai_messages.append({"role": "user", "content": injection_text})

                        for m in memories:
                            self._already_surfaced_memories.add(m.path)
                            self._session_memory_bytes += len(m.content.encode())
                except Exception:
                    pass

            if not self.is_sub_agent:
                start_spinner()

            with events.span("model_stream", turn=self.current_turns, backend="openai"):
                response = await self._call_openai_stream()

            if not self.is_sub_agent:
                stop_spinner()

            self.last_api_call_time = time.time()

            if response.get("usage"):
                self._record_openai_usage(response["usage"])
                self._emit_usage()

            choice = response.get("choices", [{}])[0] if response.get("choices") else {}
            message = choice.get("message", {})

            if message.get("tool_calls") and choice.get("finish_reason") == "length":
                # Cut off by max_tokens: the last call's arguments may be half a JSON
                # document. Keep the text and reasoning, run nothing.
                message = {k: v for k, v in message.items() if k != "tool_calls"}
                if message.get("content") or message.get("reasoning_content"):
                    message["content"] = message.get("content") or ""
                    self._openai_messages.append(message)
                if not self._note_truncation():
                    break
                self._openai_messages.append({"role": "user", "content": TRUNCATED_TOOL_CALL_NOTE})
                continue

            self._openai_messages.append(message)

            tool_calls = message.get("tool_calls")

            if not tool_calls:
                if self._should_retry_empty_reply(message.get("content")):
                    self._openai_messages.pop()          # an empty assistant message is not worth keeping
                    self._openai_messages.append({"role": "user", "content": EMPTY_REPLY_NUDGE})
                    continue
                followup = await self._end_of_turn_check(message.get("content") or "")
                if followup:
                    self._openai_messages.append({"role": "user", "content": followup})
                    continue
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens, self._get_current_cost_usd())
                break

            self.current_turns += 1
            budget = self._check_budget()
            if budget["exceeded"]:
                print_info(f"Budget exceeded: {budget['reason']}")
                await self._finalize_on_budget_openai(tool_calls)
                break

            oai_checked: list[dict] = []
            for tc in tool_calls:
                if self._aborted:
                    break

                if tc.get("type") != "function":
                    continue

                fn_name = tc["function"]["name"]
                try:
                    inp = json.loads(tc["function"]["arguments"])
                except Exception:
                    inp = {}

                print_tool_call(fn_name, inp, tc.get("id"))

                perm = self._gate(tc.get("id"), fn_name, inp)
                self._emit_permission(tc.get("id"), fn_name, perm)

                if perm["action"] == "deny":
                    self._record_tool_outcome(fn_name, False)
                    oai_checked.append({"tc": tc, "fn": fn_name, "inp": inp, "allowed": False,
                                        "result": f"Action denied: {perm.get('message', '')}"})
                    continue
                if perm["action"] == "confirm":
                    denial = await self._ask_user(tc.get("id"), fn_name, perm)
                    if denial is not None:
                        self._record_tool_outcome(fn_name, False)
                        oai_checked.append({"tc": tc, "fn": fn_name, "inp": inp, "allowed": False,
                                            "result": denial})
                        continue
                oai_checked.append({"tc": tc, "fn": fn_name, "inp": inp, "allowed": True})

            oai_batches: list[dict] = []
            for ct in oai_checked:
                safe = ct["allowed"] and ct["fn"] in CONCURRENCY_SAFE_TOOLS
                if safe and oai_batches and oai_batches[-1]["concurrent"]:
                    oai_batches[-1]["items"].append(ct)
                else:
                    oai_batches.append({"concurrent": safe, "items": [ct]})

            oai_context_break = False
            for batch in oai_batches:
                if oai_context_break or self._aborted:
                    break

                if batch["concurrent"]:
                    async def _run_oai_safe(ct_item: dict) -> tuple[dict, str]:
                        raw = await self._run_tool(ct_item["tc"].get("id"), ct_item["fn"], ct_item["inp"])
                        raw = _safe_utf8_text(raw)
                        res = self._persist_large_result(ct_item["fn"], raw)
                        print_tool_result(ct_item["fn"], res, ct_item["tc"].get("id"))
                        return ct_item, res

                    results = await asyncio.gather(*[_run_oai_safe(ct) for ct in batch["items"]])
                    for ct_item, res in results:
                        self._record_tool_outcome(
                            ct_item["fn"],
                            not self._looks_like_tool_failure(ct_item["fn"], "", res),
                        )
                        self._openai_messages.append(
                            {"role": "tool", "tool_call_id": ct_item["tc"]["id"], "content": res})
                else:
                    for ct in batch["items"]:
                        if not ct["allowed"]:
                            self._openai_messages.append(
                                {"role": "tool", "tool_call_id": ct["tc"]["id"], "content": ct["result"]})
                            continue

                        raw = await self._run_tool(ct["tc"].get("id"), ct["fn"], ct["inp"])
                        raw = _safe_utf8_text(raw)
                        res = self._persist_large_result(ct["fn"], raw)
                        print_tool_result(ct["fn"], res, ct["tc"].get("id"))
                        self._record_tool_outcome(
                            ct["fn"],
                            not self._looks_like_tool_failure(ct["fn"], raw, res),
                        )

                        if self._context_cleared:
                            self._context_cleared = False
                            self._openai_messages.append({"role": "user", "content": res})
                            oai_context_break = True
                            break

                        self._openai_messages.append(
                            {"role": "tool", "tool_call_id": ct["tc"]["id"], "content": res})

            self._context_cleared = False
            self._maybe_fold_hint()
            await self._check_and_compact()

    async def _call_openai_nonstream(self) -> dict:
        async def _do():
            return await self._openai_client.chat.completions.create(
                model=self.model,
                tools=_sanitize_for_utf8(_to_openai_tools(get_active_tool_definitions(self.tools))),
                messages=_sanitize_for_utf8(self._openai_messages),
                **self._openai_request_extras(),
            )

        resp = await _with_retry(_do)
        ch = resp.choices[0]
        msg = ch.message
        tcs = None
        if msg.tool_calls:
            tcs = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                                "arguments": tc.function.arguments or ""}}
                for tc in msg.tool_calls
            ]
        if msg.content:
            self._emit_text('\n' + str(msg.content))
        return {
            "choices": [{"message": _assistant_message(msg.content, tcs, getattr(msg, "reasoning_content", None)),
                         "finish_reason": ch.finish_reason or "stop"}],
            "usage": self._openai_usage_dict(resp.usage),
        }

    async def _call_openai_stream(self) -> dict:
        # Some OpenAI-compatible gateways (Gemini proxies) do not emit
        # incremental delta.tool_calls when streaming, so tool calls vanish.
        # KEELSON_OPENAI_STREAM=0 forces a non-streaming call.
        if not paths.env_flag("KEELSON_OPENAI_STREAM", default=True):
            return await self._call_openai_nonstream()

        async def _do():
            stream = await self._openai_client.chat.completions.create(
                model=self.model,
                tools=_sanitize_for_utf8(_to_openai_tools(get_active_tool_definitions(self.tools))),
                messages=_sanitize_for_utf8(self._openai_messages),
                stream=True,
                stream_options={"include_usage": True},
                **self._openai_request_extras(),
            )

            content = ""
            reasoning = ""
            first_text = True
            tool_calls: dict[int, dict] = {}
            finish_reason = ""
            usage = None

            async for chunk in stream:
                if chunk.usage:
                    usage = self._openai_usage_dict(chunk.usage)

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # DeepSeek thinking mode (and some local servers) stream the reasoning
                # separately. It is kept and sent back: DeepSeek rejects a tool
                # conversation whose earlier reasoning_content is missing.
                piece = getattr(delta, "reasoning_content", None) if delta else None
                if piece:
                    reasoning += _safe_utf8_text(piece)

                if delta and delta.content:
                    if first_text:
                        stop_spinner()
                        self._emit_text("\n")
                        first_text = False
                    self._emit_text(delta.content)
                    content += _safe_utf8_text(delta.content)

                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        existing = tool_calls.get(tc.index)
                        if existing:
                            if tc.function and tc.function.arguments:
                                existing["arguments"] += _safe_utf8_text(tc.function.arguments)
                        else:
                            tool_calls[tc.index] = {
                                "id": _safe_utf8_text(tc.id or ""),
                                "name": _safe_utf8_text((tc.function.name if tc.function else "") or ""),
                                "arguments": _safe_utf8_text((tc.function.arguments if tc.function else "") or ""),
                            }

                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

            assembled = None
            if tool_calls:
                assembled = [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for _, tc in sorted(tool_calls.items())
                ]

            return {
                "choices": [{
                    "message": _assistant_message(content, assembled, reasoning),
                    "finish_reason": finish_reason or "stop",
                }],
                "usage": usage or self._openai_usage_dict(None),
            }

        return await _with_retry(_do)

    async def _confirm_dangerous(self, command: str, request: dict | None = None) -> Any:
        """Ask the person; returns what the confirm hook answered (bool, a choice, or {choice, reason})."""
        request = request or {"message": command, "reason": "", "options": ["once", "deny"], "rules": []}
        print_confirmation(command, request.get("reason", ""), request.get("options"))
        if self.confirm_fn:
            # Time spent waiting for a person is reported separately, so it is
            # never mistaken for model or tool latency.
            with events.span("confirm_wait"):
                if _accepts_request(self.confirm_fn):
                    return await self.confirm_fn(command, request=request)
                return await self.confirm_fn(command)
        # Fallback: blocking input
        try:
            answer = input("  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False

    async def _ask_user(self, call_id: str | None, name: str, perm: dict) -> str | None:
        """Confirm one call. None: go ahead. Otherwise the tool result that tells the model it was refused."""
        rules = list(perm.get("rules") or [])
        options = ["once", "session", "project", "deny"] if rules else ["once", "deny"]
        request = {"message": perm.get("message", ""), "reason": perm.get("reason", ""), "tool": name,
                   "rules": rules, "options": options}
        choice, reason = normalize_choice(await self._confirm_dangerous(perm.get("message", ""), request))
        if choice == "session" and rules:
            self._session_rules += [r for r in rules if r not in self._session_rules]
        elif choice == "project" and rules:
            from .tools import save_project_rule

            for rule in rules:
                save_project_rule(rule)
            self._session_rules += [r for r in rules if r not in self._session_rules]
        allowed = choice in ("once", "session", "project")
        self._emit_permission(call_id, name, {
            "action": "allow" if allowed else "deny",
            "message": {"once": "approved by user (once)", "session": "approved by user for this session",
                        "project": "approved by user for this project"}.get(choice, "denied by user"),
        })
        if allowed:
            return None
        return f"User denied this action. Their reason: {reason}" if reason else "User denied this action."

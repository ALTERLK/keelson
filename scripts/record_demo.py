"""Record the demo traces under demo/traces/ by running real tasks.

Each scenario runs the real Agent against the configured model inside a
throwaway workspace (a copy of one sandbox benchmark task) with an isolated
KEELSON_HOME, so nothing on the machine or in the repo is touched. The bus
events are written to a JSONL trace that the web console can replay.

Permission prompts are answered by a script, through the same Session.confirm
path the browser uses, after a short pause so the prompt is visible on replay.

    python scripts/record_demo.py --list
    python scripts/record_demo.py fix-failing-test
    python scripts/record_demo.py --all

Needs an API key in .env. Local paths are rewritten to /workspace before the
trace is saved, and the script refuses to save a trace that still contains one.

``--scripted`` records without a model: the assistant turns come from the
``script`` written into the scenario, while everything the runtime does with
them — the permission gate, tool execution, file edits, running pytest — is
real. The trace header says so, and token counts are character-based estimates.
Use it for a deterministic demo or when no API key is at hand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "demo" / "traces"
CONFIRM_PAUSE_S = 1.6

SCENARIOS: dict[str, dict] = {
    "fix-failing-test": {
        "order": 1,
        "title": "修复一个失败的测试 · Fix a failing test",
        "description": "读代码、定位 off-by-one、修改、跑测试验证 —— 每次工具调用都先过权限门。",
        "task": "off-by-one-pagination",
        "mode": "default",
        "confirm": "allow",
        "turns": [
            "test_pager.py 里有测试失败了。请找出 pager.py 中的 bug 并修复，然后运行测试确认全部通过。",
        ],
        "script": [
            {"text": "我先看一下测试和被测的实现。",
             "tools": [("read_file", {"file_path": "test_pager.py"}), ("read_file", {"file_path": "pager.py"})]},
            {"text": "问题在 `len(items) // page_size`：整除把最后不足一页的那部分丢掉了，所以 "
                     "`test_partial_last_page` 等用例失败。改成按步长切片，就不需要先算页数。",
             "tools": [("edit_file", {
                 "file_path": "pager.py",
                 "old_string": "    n = len(items) // page_size          # <-- integer division drops the remainder page\n"
                               "    for p in range(n):\n"
                               "        pages.append(items[p * page_size:(p + 1) * page_size])",
                 "new_string": "    for start in range(0, len(items), page_size):\n"
                               "        pages.append(items[start:start + page_size])",
             })]},
            {"text": "改好了，跑一遍测试确认。", "tools": [("run_shell", {"command": "python -m pytest -q"})]},
            {"text": "已修复，4 个测试全部通过。\n\n"
                     "- **原因**：`paginate` 用 `len(items) // page_size` 计算页数，整除丢掉了最后不足一页的元素。\n"
                     "- **修改**：改为 `range(0, len(items), page_size)` 按步长切片，最后一页自然保留。\n"
                     "- **验证**：`python -m pytest -q` 通过；没有改动测试文件。"},
        ],
    },
    "permission-gate": {
        "order": 2,
        "title": "删除只进回收站 · Deletes go to the Recycle Bin",
        "description": "用户要求用 shell 删文件。Keelson 里 shell 的删除命令一律拒绝，工具说明也写明了这一点，"
                       "所以模型直接改用 delete_files：文件进了回收站，/undo 或回收站都能找回。",
        "task": "handle-empty-input",
        "mode": "default",
        "confirm": "deny",
        "turns": [
            "这个目录里的测试文件我不想要了，用 shell 命令把 test_stats.py 删掉，然后告诉我目录里还剩什么。",
        ],
        "script": [
            {"text": "好的，我用 shell 命令删除它。", "tools": [("run_shell", {"command": "rm test_stats.py"})]},
            {"text": "shell 里的删除被拒绝了，运行时要求改用 delete_files，它会把文件移到回收站。",
             "tools": [("delete_files", {"paths": ["test_stats.py"]})]},
            {"text": "再看看目录里还剩什么。", "tools": [("list_files", {"pattern": "*"})]},
            {"text": "`test_stats.py` 已移到回收站，需要时可以从回收站还原。目录里现在只剩 `stats.py`。"},
        ],
    },
    "undo-shell": {
        "order": 4,
        "title": "撤销连 shell 的改动一起撤 · /undo covers shell changes",
        "description": "这一轮全部用 shell 命令改文件：新建目录和文件、复制、往已有文件里追加。随后一次撤销，全部还原，包括 shell 新建的文件。",
        "task": "handle-empty-input",
        "mode": "default",
        "confirm": "allow",
        "solved": True,              # start from passing tests, so the badge is about this turn's changes
        "undo_after": True,
        "turns": [
            "只用 shell 命令（不要用编辑文件的工具）做三件事：新建 build 目录并在里面生成 report.txt，内容写 stats.py 的行数；"
            "把 stats.py 复制一份为 stats_backup.py；在 stats.py 末尾追加一行注释 `# reviewed`。做完列出改动。",
        ],
    },
    "skill-evolution": {
        "order": 3,
        "title": "从反馈中沉淀技能 · A skill extracted from feedback",
        "description": "第二轮给出一条长期有效的反馈，运行时在后台把它提取成技能；第三轮同类任务自动套用。",
        "task": "word-frequency-counter",
        "mode": "acceptEdits",
        "confirm": "allow",
        "env": {"KEELSON_AUTO_SKILL_EVOLUTION": "1", "KEELSON_AUTO_SKILL_TARGET": "project"},
        "turns": [
            "读一下 wordcount.py，告诉我它是做什么的。",
            "以后解释代码时，一律用要点列表，每条不超过一行，先说用途再说关键细节。",
            "按刚才的要求，再解释一下 test_wordcount.py。",
        ],
    },
    "ask-irreversible": {
        "order": 5,
        "title": "撤销不了的才问 · Only what undo cannot reverse asks",
        "description": "装包会改动项目外的环境，/undo 撤不回来，所以运行时停下来问，并写明原因；用户拒绝后，模型换了不需要装包的做法。",
        "task": "handle-empty-input",
        "mode": "default",
        "confirm": "deny",
        "solved": True,
        "turns": [
            "用 pip 给这个环境装上 numpy，然后把 stats.py 的 average 改成用 numpy.mean 实现。",
        ],
    },
}


def _script_model(agent, steps: list[dict]) -> None:
    """Replace the model call with the scenario's script (see --scripted)."""
    remaining = iter(steps)
    call_no = 0

    async def scripted_call() -> dict:
        nonlocal call_no
        step = next(remaining)
        await asyncio.sleep(0.9)  # stand-in for model latency, so replay has a rhythm
        text = step.get("text") or ""
        if text:
            agent._emit_text("\n")
            for i in range(0, len(text), 4):
                agent._emit_text(text[i:i + 4])
                await asyncio.sleep(0.015)
        tool_calls = []
        for tool_name, arguments in step.get("tools", []):
            call_no += 1
            tool_calls.append({
                "id": f"call_{call_no}", "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            })
        prompt_chars = sum(len(str(m.get("content") or "")) for m in agent._openai_messages)
        return {
            "choices": [{"message": {"role": "assistant", "content": text or None, "tool_calls": tool_calls or None},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_chars // 3, "completion_tokens": max(1, len(text) // 3)},
        }

    agent._call_openai_stream = scripted_call
    agent._build_side_query = lambda **_: None  # no memory prefetch: that would be a model call too


def _make_workspace(task: str, base: Path, solved: bool = False) -> Path:
    src = ROOT / "bench" / "tasks" / "sandbox" / task
    ws = base / "workspace"
    ws.mkdir(parents=True)
    for path in src.iterdir():
        if path.name in ("solution", "task.json", "__pycache__", ".pytest_cache"):
            continue
        if path.is_file():
            shutil.copy2(path, ws / path.name)
    if solved:
        for path in (src / "solution").iterdir():
            shutil.copy2(path, ws / path.name)
    return ws


async def _run(name: str, scenario: dict, *, scripted: bool = False) -> Path:
    from keelson import events, ui
    from keelson.agent import Agent
    from keelson.main import _load_env_file, _resolve_api_config
    from keelson.web.recorder import TraceRecorder
    from keelson.web.scrub import leaks, scrub
    from keelson.web.sessions import Session

    os.chdir(ROOT)
    _load_env_file()
    if scripted:
        if "script" not in scenario:
            raise SystemExit(f"{name} has no script; it needs a real model.")
        api_base, api_key, use_openai = "http://scripted.invalid/v1", "scripted", True
        model = "scripted model · real runtime"
    else:
        api_base, api_key, use_openai = _resolve_api_config(None)
        if not api_key:
            raise SystemExit("No API key configured (.env). Use --scripted to record without a model.")
        model = os.environ.get("MODEL") or "deepseek-flash"

    base = Path(tempfile.mkdtemp(prefix="keelson-demo-"))
    workspace = _make_workspace(scenario["task"], base, scenario.get("solved", False))
    os.environ["KEELSON_HOME"] = str(base / "home")
    os.environ.setdefault("KEELSON_AUTO_SKILL_EVOLUTION", "0")
    os.environ.update(scenario.get("env", {}))
    # `python` / `pytest` typed by the model should resolve to this environment.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    os.chdir(workspace)

    agent = Agent(
        permission_mode=scenario["mode"],
        model=model,
        max_turns=16,
        api_base=api_base if use_openai else None,
        anthropic_base_url=api_base if not use_openai else None,
        api_key=api_key,
    )
    description = scenario["description"]
    if scripted:
        _script_model(agent, scenario["script"])
        description += "（模型回复为脚本，未调用 API；权限门、工具执行、文件修改均为真实运行，Token 数为估算。）"
    raw_path = base / "trace.jsonl"
    recorder = TraceRecorder(raw_path, meta={
        "title": scenario["title"],
        "description": description,
        "model": model,
        "scripted": scripted,
        "permission_mode": scenario["mode"],
    })
    session = Session(agent, recorder=recorder)
    ui.set_terminal_enabled(False)
    ui.attach_terminal(session.bus)  # watch the run in this terminal too

    loop = asyncio.get_running_loop()
    allow = scenario["confirm"] == "allow"

    def answer_confirms(event: events.Event) -> None:
        if event.type == events.CONFIRM_REQUEST:
            loop.call_later(CONFIRM_PAUSE_S, session.resolve_confirm, event.data["request_id"], allow)

    session.bus.subscribe(answer_confirms)
    session.bus.emit(events.SESSION, **{**session.describe(), "workspace": "workspace"})

    try:
        # The same binding a web session uses: everything the turn publishes,
        # background skill tasks included, goes to this session's bus.
        with events.use_bus(session.bus):
            for prompt in scenario["turns"]:
                await agent.chat(prompt)
                await agent.drain_background_skill_tasks()
                await asyncio.sleep(0.4)
            if scenario.get("undo_after"):
                await asyncio.sleep(1.2)          # a beat on replay before the undo lands
                session.undo()                    # the console's Undo button: emits the same UNDO event
    finally:
        session.close()
        os.chdir(ROOT)

    text = raw_path.read_text(encoding="utf-8")
    text = scrub(text, [
        (str(workspace), "/workspace"),
        (str(base), "/tmp/keelson-demo"),
        (str(ROOT), "/keelson"),          # e.g. the interpreter path in pytest's banner
    ])
    found = leaks(text)
    if found:
        raise SystemExit(f"Refusing to save {name}: trace still contains {found}. Raw trace kept at {raw_path}")
    for line in text.splitlines():
        json.loads(line)  # the scrub must leave valid JSON behind

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{scenario['order']:02d}-{name}.jsonl"
    out.write_text(text, encoding="utf-8", newline="\n")
    shutil.rmtree(base, ignore_errors=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scripted", action="store_true", help="Scripted assistant turns, real runtime; no API key needed")
    args = parser.parse_args()

    if args.list or not (args.scenario or args.all):
        for name, sc in sorted(SCENARIOS.items(), key=lambda kv: kv[1]["order"]):
            print(f"{name:20s} {sc['title']}")
        return 0

    if args.all:
        # One process per scenario: module-level caches and env stay clean.
        import subprocess

        for name in sorted(SCENARIOS, key=lambda n: SCENARIOS[n]["order"]):
            if args.scripted and "script" not in SCENARIOS[name]:
                continue
            code = subprocess.call([sys.executable, __file__, name] + (["--scripted"] if args.scripted else []))
            if code:
                return code
        return 0

    out = asyncio.run(_run(args.scenario, SCENARIOS[args.scenario], scripted=args.scripted))
    print(f"\nsaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

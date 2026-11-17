"""Run Keelson on real issues from real open-source projects, and grade the result.

Each case in ``cases.json`` is a bug that was reported and later fixed upstream.
The protocol is the one SWE-bench uses:

1. Check the project out at the commit *before* the fix.
2. Give the agent the issue text, and nothing else. It does not see the tests
   the maintainers added with the fix.
3. When the agent stops, put the maintainers' version of the test files in
   place and run them. The case counts as resolved only if every test the fix
   was written for now passes (fail-to-pass) and nothing that passed before
   broke (no regressions).

The agent runs unattended in ``dontAsk`` mode: anything the permission gate
would have asked a person about is refused instead. While it works, the
checkout has no ``.git`` directory, so the fix commit cannot be looked up.

    python bench/casestudy/run.py issues            # download the issue texts (once)
    python bench/casestudy/run.py validate          # no model: prove each case is sound
    python bench/casestudy/run.py run               # run the agent on every case
    python bench/casestudy/run.py run tenacity-658  # ...or on one
    python bench/casestudy/run.py grade             # grade the saved changes again, without the model

A run can be filed as a numbered round (``--round 1``): its results then go to
``round1/results.json`` and its traces get ``-round1`` in their name, and later
runs leave both alone. The latest results carry every filed round's headline
numbers per case, so a change to the runtime can be compared before and after
on the same issues. ``rounds.json`` says, by hand, what changed between rounds.

``validate`` checks the harness itself: at the old commit the fix's tests must
fail, and at the fix commit they must pass. A case that does not behave that
way on this machine is reported and should not be trusted.

Clones and virtual environments are kept under ``--work`` (a temp directory by
default) so a second run is fast. Results go to ``results.json`` next to this
file; scrubbed traces go to ``demo/traces/`` for the web console to replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

CASES_FILE = HERE / "cases.json"
ISSUES_DIR = HERE / "issues"
VALIDATED_FILE = HERE / "validated.json"
RESULTS_FILE = HERE / "results.json"
TRACES_DIR = ROOT / "demo" / "traces"
ROUNDS_FILE = HERE / "rounds.json"
ROUND_KEYS = ("resolved", "status", "model_calls", "tool_calls", "loop_guard", "input_tokens", "wall_time_s", "trace")


def round_dir(number: int) -> Path:
    return HERE / f"round{number}"

TEST_TIMEOUT_S = 300        # one run of a project's test file
SINGLE_TEST_TIMEOUT_S = 40  # one new test at the old commit (some of them hang: that is the bug)
AGENT_TIMEOUT_S = 900
MAX_TURNS = 30
PERMISSION_MODE = "dontAsk"
MAX_DIFF_LINES = 160

PROMPT = """\
You are working in a checkout of the open-source Python project `{project}`.
A user reported the issue below. Find the cause and fix it in the project's source code.

Rules:
- Keep the change small, and keep every behaviour the issue does not complain about.
- Do not edit or delete existing tests. Your fix will be graded with the project's own
  tests, including ones you cannot see yet.
- You can run the existing tests with `python -m pytest <path>`.

## Issue: {title}

{body}
"""


# ─── small helpers ──────────────────────────────────────────


def load_cases(selected: list[str] | None = None) -> list[dict]:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if selected:
        unknown = set(selected) - {case["id"] for case in cases}
        if unknown:
            raise SystemExit(f"unknown case(s): {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in selected]
    return cases


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _run(cmd: list[str], *, cwd: Path, timeout: float | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)


def git(repo: Path, *args: str) -> str:
    proc = _run(["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", *args], cwd=repo, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}:\n{proc.stderr.strip()}")
    return proc.stdout


def case_dir(case: dict, work: Path) -> Path:
    return work / case["id"]


def venv_python(case: dict, work: Path) -> Path:
    venv = case_dir(case, work) / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_test_path(path: str) -> bool:
    parts = Path(path).parts
    name = parts[-1] if parts else ""
    return "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


# ─── issue texts ────────────────────────────────────────────


def fetch_issues(cases: list[dict]) -> None:
    """Save each issue's title and body, so a run does not depend on the network or on later edits."""
    ISSUES_DIR.mkdir(exist_ok=True)
    for case in cases:
        url = f"https://api.github.com/repos/{case['repo']}/issues/{case['issue']}"
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "keelson-casestudy"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        _write_json(ISSUES_DIR / f"{case['id']}.json", {
            "title": data["title"],
            "url": data["html_url"],
            "created_at": data["created_at"],
            "body": (data.get("body") or "").replace("\r\n", "\n"),
        })
        print(f"{case['id']:24s} {data['title']}")


def load_issue(case: dict) -> dict:
    path = ISSUES_DIR / f"{case['id']}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} is missing: run `python bench/casestudy/run.py issues` first")
    return json.loads(path.read_text(encoding="utf-8"))


# ─── repository and environment ─────────────────────────────


def prepare_repo(case: dict, work: Path) -> Path:
    """A clone holding exactly two commits: the one before the fix and the fix."""
    repo = case_dir(case, work) / "repo"
    restore_history(repo)                                  # in case an earlier run died while it was hidden
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, "init", "-q")
        git(repo, "remote", "add", "origin", f"https://github.com/{case['repo']}.git")
        git(repo, "fetch", "-q", "--depth", "1", "origin", case["base_sha"], case["fix_sha"])
        git(repo, "tag", "-f", "ks-base", case["base_sha"])
        git(repo, "tag", "-f", "ks-fix", case["fix_sha"])
    reset_to(repo, "ks-base")
    return repo


def reset_to(repo: Path, ref: str) -> None:
    git(repo, "reset", "-q")
    git(repo, "checkout", "-q", "-f", ref)
    git(repo, "clean", "-q", "-fd")


def hide_history(repo: Path) -> None:
    """Move ``.git`` out of the checkout while the agent works.

    The clone contains the fix commit. With the repository metadata gone the
    agent has a plain directory of files: no ``git log``, no ``git show``.
    """
    (repo / ".git").rename(repo.parent / "dotgit")


def restore_history(repo: Path) -> None:
    hidden = repo.parent / "dotgit"
    if hidden.exists() and not (repo / ".git").exists():
        hidden.rename(repo / ".git")


def overlay_upstream_tests(repo: Path, case: dict) -> None:
    """Replace the test files with the maintainers' version from the fix commit."""
    git(repo, "checkout", "-q", "ks-fix", "--", *case["test_files"])
    git(repo, "reset", "-q")


def ensure_venv(case: dict, work: Path, repo: Path) -> Path:
    python = venv_python(case, work)
    if not python.exists():
        print(f"  creating environment for {case['id']} ...")
        subprocess.run([sys.executable, "-m", "venv", str(python.parents[1])], check=True)
        env = {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"}  # shallow clone: no tags to derive a version from
        proc = _run([str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-e", str(repo), *case["install"]],
                    cwd=repo, timeout=900, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"pip install failed for {case['id']}:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return python


def reinstall_checkout(case: dict, work: Path, repo: Path) -> None:
    """Point the environment back at the checkout.

    The agent has a shell. If it ran ``pip install <project>`` the environment
    now imports a released copy, and the tests would grade that instead of the
    agent's change.
    """
    env = {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"}
    proc = _run([str(venv_python(case, work)), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                 "--no-deps", "-e", str(repo)], cwd=repo, timeout=600, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"could not reinstall {case['id']}:\n{proc.stderr[-1500:]}")


# ─── running tests ──────────────────────────────────────────


def run_tests(case: dict, work: Path, repo: Path, label: str, *, keyword: str | None = None,
              timeout: float = TEST_TIMEOUT_S) -> dict:
    """Run the case's test files; return ``{"outcomes": {test id: passed|failed|skipped}, "timed_out": bool}``."""
    report = case_dir(case, work) / f"junit-{label}.xml"
    report.unlink(missing_ok=True)
    cmd = [str(venv_python(case, work)), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
           f"--junitxml={report}", *case["test_files"]]
    if keyword:
        cmd += ["-k", keyword]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = _run(cmd, cwd=repo, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"outcomes": {}, "timed_out": True, "tail": f"timed out after {timeout:.0f}s"}
    outcomes: dict[str, str] = {}
    if report.exists():
        for node in ET.parse(report).getroot().iter("testcase"):
            test_id = f"{node.get('classname')}::{node.get('name')}"
            if node.find("failure") is not None or node.find("error") is not None:
                outcomes[test_id] = "failed"
            elif node.find("skipped") is not None:
                outcomes[test_id] = "skipped"
            else:
                outcomes[test_id] = "passed"
    return {"outcomes": outcomes, "timed_out": False, "tail": (proc.stdout + proc.stderr)[-600:]}


def _passed(run: dict) -> set[str]:
    return {test for test, outcome in run["outcomes"].items() if outcome == "passed"}


def _failed(run: dict) -> set[str]:
    return {test for test, outcome in run["outcomes"].items() if outcome == "failed"}


# ─── validate: is the case itself sound? ────────────────────


def validate_case(case: dict, work: Path) -> dict:
    repo = prepare_repo(case, work)
    ensure_venv(case, work, repo)

    before = run_tests(case, work, repo, "base")                    # old code, old tests
    reset_to(repo, "ks-fix")
    after = run_tests(case, work, repo, "fix")                      # the maintainers' fix, their tests
    reset_to(repo, "ks-base")

    if after["timed_out"] or not after["outcomes"]:
        return {"sound": False, "reason": f"tests do not run at the fix commit: {after['tail']}"}

    # Tests the fix was written for: new in the fix commit, or failing before it.
    candidates = sorted((_passed(after) - set(before["outcomes"])) | (_passed(after) & _failed(before)))
    fail_to_pass = []
    overlay_upstream_tests(repo, case)
    for test_id in candidates:
        name = test_id.rsplit("::", 1)[-1].split("[")[0]
        # One at a time and with a short limit: on the old code some of these never return.
        single = run_tests(case, work, repo, "single", keyword=name, timeout=SINGLE_TEST_TIMEOUT_S)
        if single["timed_out"] or single["outcomes"].get(test_id) != "passed":
            fail_to_pass.append({"id": test_id, "at_base": "hangs" if single["timed_out"] else "fails"})
    reset_to(repo, "ks-base")

    known_failures = sorted(test for test, outcome in after["outcomes"].items() if outcome == "failed")
    return {
        "sound": bool(fail_to_pass),
        "reason": "" if fail_to_pass else "no test distinguishes the old code from the fix on this machine",
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": len(_passed(after)) - len(fail_to_pass),
        "known_failures": known_failures,   # fail even with the upstream fix (platform quirks); never held against the agent
        "python": ".".join(map(str, sys.version_info[:3])),
        "platform": sys.platform,
    }


def cmd_validate(cases: list[dict], work: Path) -> int:
    validated = _read_json(VALIDATED_FILE, {})
    for case in cases:
        print(f"{case['id']}")
        result = validate_case(case, work)
        validated[case["id"]] = result
        if result["sound"]:
            hangs = sum(1 for test in result["fail_to_pass"] if test["at_base"] == "hangs")
            print(f"  ok: {len(result['fail_to_pass'])} fail-to-pass ({hangs} hang on the old code), "
                  f"{result['pass_to_pass']} pass-to-pass, {len(result['known_failures'])} known failures")
        else:
            print(f"  NOT SOUND: {result['reason']}")
        _write_json(VALIDATED_FILE, validated)
    return 0 if all(validated[case["id"]]["sound"] for case in cases) else 1


# ─── the agent run (in its own process) ─────────────────────


async def _agent_main(case: dict, work: Path) -> None:
    from keelson import events, ui
    from keelson.agent import Agent
    from keelson.main import _load_env_file, _resolve_api_config
    from keelson.web.recorder import TraceRecorder
    from keelson.web.sessions import Session

    directory = case_dir(case, work)
    repo = directory / "repo"
    issue = load_issue(case)

    os.chdir(ROOT)
    _load_env_file()
    api_base, api_key, use_openai = _resolve_api_config(None)
    if not api_key:
        raise SystemExit("No API key configured (.env).")
    model = os.environ.get("MODEL") or "deepseek-flash"

    os.environ["KEELSON_HOME"] = str(directory / "home")       # no skills or memory from this machine
    os.environ["KEELSON_AUTO_SKILL_EVOLUTION"] = "0"
    # `python` typed by the model must be the case's environment, not this one.
    os.environ["PATH"] = str(venv_python(case, work).parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.chdir(repo)

    agent = Agent(
        permission_mode=PERMISSION_MODE,
        model=model,
        max_turns=MAX_TURNS,
        api_base=api_base if use_openai else None,
        anthropic_base_url=api_base if not use_openai else None,
        api_key=api_key,
    )
    recorder = TraceRecorder(directory / "trace.jsonl", meta={
        "title": f"真实 issue · {case['project']} #{case['issue']}：{issue['title']}",
        "description": case["summary"]["zh"],
        "model": model,
        "permission_mode": PERMISSION_MODE,
        "case": case["id"],
    })
    session = Session(agent, recorder=recorder)
    ui.set_terminal_enabled(False)
    ui.attach_terminal(session.bus)

    counts: Counter[str] = Counter()

    def tally(event: events.Event) -> None:
        if event.type == events.TOOL_CALL:
            counts["tool_calls"] += 1
        elif event.type == events.PERMISSION and event.data.get("action") == "deny":
            counts["denied"] += 1
        elif event.type == events.SPAN and event.data.get("name") == "model_stream":
            counts["model_calls"] += 1
        elif event.type == events.LOOP_GUARD:
            counts["loop_guard"] += 1

    session.bus.subscribe(tally)
    session.bus.emit(events.SESSION, **{**session.describe(), "workspace": case["project"]})

    status = "completed"
    started = time.time()
    try:
        with events.use_bus(session.bus):
            await asyncio.wait_for(
                agent.chat(PROMPT.format(project=case["project"], title=issue["title"], body=issue["body"])),
                AGENT_TIMEOUT_S,
            )
    except TimeoutError:
        status = "timeout"
    except Exception as exc:  # a crashed run is a result too
        status = f"error: {type(exc).__name__}: {exc}"
    finally:
        session.close()
        os.chdir(ROOT)

    _write_json(directory / "agent.json", {
        "status": status,
        "model": model,
        "wall_time_s": round(time.time() - started, 1),
        "model_calls": counts["model_calls"],
        "tool_calls": counts["tool_calls"],
        "denied": counts["denied"],
        "loop_guard": counts["loop_guard"],
        "input_tokens": agent.total_input_tokens,
        "output_tokens": agent.total_output_tokens,
    })


# ─── grading ────────────────────────────────────────────────


def _clip(diff: str) -> str:
    lines = diff.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"... ({len(lines) - MAX_DIFF_LINES} more lines)"]
    return "\n".join(lines)


def grade(case: dict, work: Path, validated: dict) -> dict:
    repo = case_dir(case, work) / "repo"
    exclude = [":(exclude).keelson", ":(exclude)*.egg-info", ":(exclude)__pycache__", ":(exclude)*.pyc"]

    git(repo, "add", "-A", "-N", "--", ".", *exclude)                       # so new files show up in the diff
    changed = [line for line in git(repo, "diff", "--name-only", "ks-base", "--", ".", *exclude).splitlines() if line]
    agent_diff = git(repo, "diff", "ks-base", "--", ".", *exclude)
    test_paths = [path for path in changed if is_test_path(path)]
    removed = 0
    if test_paths:
        for line in git(repo, "diff", "--numstat", "ks-base", "--", *test_paths).splitlines():
            deleted = line.split("\t")[1]
            removed += int(deleted) if deleted.isdigit() else 0
    upstream_diff = git(repo, "diff", "ks-base", "ks-fix", "--", *case["source_paths"])

    # The maintainers' tests replace whatever is there, then decide the outcome.
    overlay_upstream_tests(repo, case)
    reinstall_checkout(case, work, repo)
    run = run_tests(case, work, repo, "agent")

    wanted = [test["id"] for test in validated["fail_to_pass"]]
    fixed = [test for test in wanted if run["outcomes"].get(test) == "passed"]
    regressions = sorted(_failed(run) - set(wanted) - set(validated["known_failures"]))
    source_changed = sorted(path for path in changed if not is_test_path(path))
    return {
        "resolved": not run["timed_out"] and len(fixed) == len(wanted) and not regressions,
        "fail_to_pass": {"passed": len(fixed), "total": len(wanted)},
        "regressions": regressions[:20],
        "tests_timed_out": run["timed_out"],
        "files_changed": source_changed,
        "same_files_as_upstream": source_changed == sorted(case["source_paths"]),
        "test_files_touched": test_paths,
        "test_lines_removed": removed,
        "agent_diff": _clip(agent_diff),
        "upstream_diff": _clip(upstream_diff),
        "patch": agent_diff,              # the whole change, kept beside the clone so `grade` can replay it
    }


def publish_trace(case: dict, work: Path, order: int, round_number: int | None = None) -> str:
    """Scrub the recorded trace and copy it to demo/traces. Returns its id, or '' if it had to be withheld."""
    from keelson.web.scrub import leaks, scrub, scrub_home_fragments, scrub_username

    directory = case_dir(case, work)
    raw = directory / "trace.jsonl"
    if not raw.exists():
        return ""
    text = scrub(raw.read_text(encoding="utf-8"), [
        (str(directory / "repo"), "/workspace"),
        (str(directory / "venv"), "/venv"),
        (str(directory), "/tmp/case"),
        (str(work), "/tmp"),
        (str(ROOT), "/keelson"),
        (sys.base_prefix, "/python"),
        (tempfile.gettempdir(), "/tmp"),          # scratch files the model made with `tempfile`
        (str(Path.home()), "/home/user"),         # pip's cache, per-user site-packages
    ])
    text = scrub_home_fragments(text)              # paths a tool abbreviated, e.g. pytest's 'C:/Users/a...9/repo'
    text = scrub_username(text)                    # e.g. pytest's scratch directory, pytest-of-<user>
    found = leaks(text)
    if found:
        print(f"  trace withheld: it still contains {found} (raw copy at {raw})")
        return ""
    lines = text.splitlines()
    for line in lines:
        json.loads(line)
    trace_id = f"{order:02d}-case-{case['id']}"
    if round_number:
        # A filed round sits after the latest runs in the replay list, and says which round it is.
        trace_id = f"{order + 10 * round_number:02d}-case-{case['id']}-round{round_number}"
        header = json.loads(lines[0])
        if header.get("type") == "trace_header":
            header["data"]["title"] = f"第 {round_number} 轮 · {header['data'].get('title', '')}"
            header["data"]["round"] = round_number
            lines[0] = json.dumps(header, ensure_ascii=False)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    (TRACES_DIR / f"{trace_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return trace_id


def cmd_run(cases: list[dict], work: Path, *, rerun_agent: bool = True, round_number: int | None = None) -> int:
    all_cases = load_cases()
    validated = _read_json(VALIDATED_FILE, {})
    results_file = round_dir(round_number) / "results.json" if round_number else RESULTS_FILE
    results_file.parent.mkdir(parents=True, exist_ok=True)
    previous = {row["id"]: row for row in _read_json(results_file, {}).get("cases", [])}
    # The latest results quote every filed round, case by case.
    filed = sorted((int(path.parent.name[5:]), path) for path in HERE.glob("round*/results.json"))
    earlier = [] if round_number else [
        (number, {row["id"]: row for row in _read_json(path, {}).get("cases", [])}) for number, path in filed
    ]
    model = ""

    for case in cases:
        check = validated.get(case["id"])
        if not check or not check.get("sound"):
            raise SystemExit(f"{case['id']} has not passed `validate` on this machine")
        issue = load_issue(case)
        print(f"\n=== {case['id']}: {issue['title']}")
        repo = case_dir(case, work) / "repo"
        patch = case_dir(case, work) / "agent.patch"
        if rerun_agent:
            repo = prepare_repo(case, work)
            ensure_venv(case, work, repo)
            for stale in ("trace.jsonl", "agent.json", "agent.patch"):
                (case_dir(case, work) / stale).unlink(missing_ok=True)

            # One process per case: module-level state and the environment stay clean.
            hide_history(repo)
            try:
                subprocess.run([sys.executable, str(Path(__file__).resolve()), "_agent", case["id"], "--work", str(work)],
                               timeout=AGENT_TIMEOUT_S + 120)
            except subprocess.TimeoutExpired:
                print("  agent process killed after the time limit")
            finally:
                restore_history(repo)
        elif patch.exists():
            # `grade`: judge the change an earlier run left behind, without calling the model again.
            reset_to(repo, "ks-base")
            if patch.read_text(encoding="utf-8").strip():
                git(repo, "apply", "--whitespace=nowarn", str(patch))
        stats = _read_json(case_dir(case, work) / "agent.json", {"status": "no result (process died)"})
        model = stats.get("model") or model

        outcome = grade(case, work, check)
        patch.write_text(outcome.pop("patch"), encoding="utf-8", newline="\n")
        order = 11 + [c["id"] for c in all_cases].index(case["id"])
        trace_id = publish_trace(case, work, order, round_number)
        reset_to(repo, "ks-base")

        row = {
            "id": case["id"],
            "project": case["project"],
            "issue": case["issue"],
            "title": issue["title"],
            "issue_url": issue["url"],
            "fix_url": f"https://github.com/{case['repo']}/pull/{case['pr']}",
            "summary": case["summary"],
            **outcome,
            **{key: stats.get(key) for key in ("status", "wall_time_s", "model_calls", "tool_calls", "denied",
                                                "loop_guard", "input_tokens", "output_tokens")},
            "trace": trace_id,
            "notes": previous.get(case["id"], {}).get("notes", {}),   # written by hand after reading the run
        }
        if earlier:
            row["earlier"] = [{"round": number, **{key: rows[case["id"]].get(key) for key in ROUND_KEYS}}
                              for number, rows in earlier if case["id"] in rows]
        previous[case["id"]] = row
        verdict = "RESOLVED" if row["resolved"] else "not resolved"
        print(f"  {verdict}: fail-to-pass {outcome['fail_to_pass']['passed']}/{outcome['fail_to_pass']['total']}, "
              f"{len(outcome['regressions'])} regressions, {stats.get('model_calls')} model calls, {stats.get('wall_time_s')}s")

        _write_json(results_file, {
            "model": model or _read_json(results_file, {}).get("model", ""),
            "generated": time.strftime("%Y-%m-%d", time.gmtime()),
            "permission_mode": PERMISSION_MODE,
            "max_turns": MAX_TURNS,
            "round": round_number or (filed[-1][0] + 1 if filed else 1),
            "rounds": _read_json(ROUNDS_FILE, []),      # what the runtime was in each round; written by hand
            "cases": [previous[c["id"]] for c in all_cases if c["id"] in previous],
        })
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["issues", "validate", "run", "grade", "_agent"])
    parser.add_argument("cases", nargs="*")
    parser.add_argument("--work", default=str(Path(tempfile.gettempdir()) / "keelson-casestudy"))
    parser.add_argument("--round", type=int, default=None, help="file the results under roundN/ instead of as the latest")
    args = parser.parse_args()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    cases = load_cases(args.cases or None)

    if args.command == "issues":
        fetch_issues(cases)
        return 0
    if args.command == "validate":
        return cmd_validate(cases, work)
    if args.command == "_agent":
        asyncio.run(_agent_main(cases[0], work))
        return 0
    return cmd_run(cases, work, rerun_agent=args.command == "run", round_number=args.round)


if __name__ == "__main__":
    raise SystemExit(main())

"""Self-built coding-task benchmark.

ToolHop measures tool-calling; this measures the harness as a *coding* agent
without SWE-bench's Docker weight. Each task under ``bench/tasks/sandbox/<id>/``
is:

    task.json      {id, instruction, files, kind}
    <source>.py    the file(s) to edit — starts broken / incomplete
    test_*.py      pytest asserts that pass iff the task is done

The grader copies a task to a temp dir, (with a model) runs the agent against the
instruction, then runs pytest. It also records edit-safety signals: how many
files changed, whether files outside ``task["files"]`` were touched, and whether
the test file itself was modified (cheating).

``load_tasks`` and ``grade_workdir`` are pure; ``run`` needs an agent callable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS_DIR = _REPO_ROOT / "bench" / "tasks" / "sandbox"


@dataclass
class SandboxTask:
    id: str
    instruction: str
    files: list[str]
    kind: str
    dir: Path

    @property
    def test_files(self) -> list[str]:
        return [p.name for p in self.dir.glob("test_*.py")]


def load_tasks(tasks_dir: str | Path = DEFAULT_TASKS_DIR) -> list[SandboxTask]:
    root = Path(tasks_dir)
    out: list[SandboxTask] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        spec_path = d / "task.json"
        if not spec_path.is_file():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        out.append(
            SandboxTask(
                id=spec["id"],
                instruction=spec["instruction"],
                files=list(spec.get("files", [])),
                kind=spec.get("kind", ""),
                dir=d,
            )
        )
    return out


def materialize(task: SandboxTask, dest: str | Path | None = None) -> Path:
    """Copy the task's starter files into a fresh workdir. ``solution/`` (the
    reference fix, used by ``validate_task``) is never copied in — the agent
    must not be able to see it."""
    work = Path(dest) if dest else Path(tempfile.mkdtemp(prefix=f"keelson-sbx-{task.id}-"))
    work.mkdir(parents=True, exist_ok=True)
    for src in task.dir.iterdir():
        if src.name == "task.json" or src.name == "solution" or src.is_dir():
            continue
        shutil.copy2(src, work / src.name)
    return work


def apply_solution(task: SandboxTask, work: Path) -> list[str]:
    """Overlay ``<task.dir>/solution/*`` onto ``work`` — used by
    ``validate_task`` to prove a task is solvable and its tests are correct."""
    sol_dir = task.dir / "solution"
    if not sol_dir.is_dir():
        return []
    applied = []
    for src in sol_dir.iterdir():
        if src.is_file():
            shutil.copy2(src, work / src.name)
            applied.append(src.name)
    return applied


# Snapshotted so both new/changed *and* deleted files are visible, and so a
# model can't dodge test failure by adding pytest config that redirects
# discovery (pytest.ini) or injects fixtures (conftest.py).
_SNAPSHOT_GLOBS = ("*.py", "*.ini", "*.cfg", "*.toml")


def _snapshot(work: Path) -> dict[str, str]:
    names = {p.name for pattern in _SNAPSHOT_GLOBS for p in work.glob(pattern) if p.is_file()}
    return {n: (work / n).read_text(encoding="utf-8", errors="replace") for n in sorted(names)}


def _restore_pristine_test_surface(task: SandboxTask, work: Path, before: dict[str, str]) -> list[str]:
    """Put the task's own test file(s) back exactly as shipped, and remove any
    pytest-config file the agent added, before grading — otherwise editing,
    deleting, or routing around the test is indistinguishable from solving the
    task. Returns the names that had to be restored/removed (tamper evidence)."""
    restored = []
    for name in task.test_files:
        original = before.get(name)
        if original is None:
            continue
        path = work / name
        current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
        if current != original:
            path.write_text(original, encoding="utf-8", newline="\n")
            restored.append(name)
    for cfg_name in ("pytest.ini", "conftest.py", "pyproject.toml", "setup.cfg", "tox.ini"):
        if cfg_name not in before and (work / cfg_name).exists():
            (work / cfg_name).unlink()
            restored.append(cfg_name)
    return restored


def run_pytest(work: Path, timeout: int = 60) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", str(work)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(work),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    passed = proc.returncode == 0
    return {"passed": passed, "returncode": proc.returncode, "output_tail": out[-800:]}


def grade_workdir(task: SandboxTask, work: Path, before: dict[str, str]) -> dict[str, Any]:
    after = _snapshot(work)
    changed = sorted(n for n in after if before.get(n) != after.get(n))
    new_files = sorted(set(after) - set(before))
    deleted_files = sorted(set(before) - set(after))
    test_touched = (
        any(n.startswith("test_") for n in changed)
        or any(n.startswith("test_") for n in new_files)
        or any(n.startswith("test_") for n in deleted_files)
    )
    out_of_scope = sorted(
        n for n in changed
        if n not in task.files and not n.startswith("test_")
    )
    # Grade against the pristine test surface — restoring here (not just
    # detecting) means tampering can never buy a passing score.
    tampered = _restore_pristine_test_surface(task, work, before)
    pt = run_pytest(work)
    return {
        "task_id": task.id,
        "kind": task.kind,
        "tests_pass": pt["passed"] and not test_touched,
        "raw_tests_pass": pt["passed"],
        "test_file_modified": test_touched,
        "files_changed": changed,
        "files_created": new_files,
        "files_deleted": deleted_files,
        "tampered_files_restored": tampered,
        "out_of_scope_edits": out_of_scope,
        "pytest_tail": pt["output_tail"],
    }


AgentFn = Callable[[SandboxTask, Path], Any]  # (task, workdir) -> None (mutates files)


def run(task: SandboxTask, agent_fn: AgentFn) -> dict[str, Any]:
    work = materialize(task)
    try:
        before = _snapshot(work)
        agent_fn(task, work)
        return grade_workdir(task, work, before)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def validate_task(task: SandboxTask) -> dict[str, Any]:
    """No-model sanity check for a task definition: the starter code must fail
    its own tests, and `<task.dir>/solution/` must make them pass. Catches a
    task that is unsolvable, already solved, or whose tests don't actually
    exercise the instruction."""
    work = materialize(task)
    try:
        before = _snapshot(work)
        starter = grade_workdir(task, work, before)
        starter_fails = not starter["raw_tests_pass"]

        applied = apply_solution(task, work)
        if not applied:
            return {"task_id": task.id, "ok": False, "error": "no solution/ directory"}

        solved = grade_workdir(task, work, before)
        solution_passes = solved["raw_tests_pass"] and not solved["test_file_modified"]
        return {
            "task_id": task.id,
            "ok": starter_fails and solution_passes,
            "starter_fails": starter_fails,
            "solution_passes": solution_passes,
            "starter_pytest_tail": starter["pytest_tail"] if not starter_fails else "",
            "solution_pytest_tail": solved["pytest_tail"] if not solution_passes else "",
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    if not n:
        return {"n": 0}
    solved = sum(1 for r in results if r.get("tests_pass"))
    cheated = sum(1 for r in results if r.get("test_file_modified"))
    oos = sum(1 for r in results if r.get("out_of_scope_edits"))
    return {
        "n": n,
        "solve_rate": round(solved / n, 3),
        "test_file_modified_rate": round(cheated / n, 3),
        "out_of_scope_edit_rate": round(oos / n, 3),
        "by_kind": {
            k: round(
                sum(1 for r in results if r.get("kind") == k and r.get("tests_pass"))
                / max(1, sum(1 for r in results if r.get("kind") == k)),
                3,
            )
            for k in sorted({r.get("kind", "") for r in results})
        },
    }

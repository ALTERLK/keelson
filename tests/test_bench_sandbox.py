"""Self-built coding-task grader."""

from __future__ import annotations

from keelson.bench.sandbox import (
    _snapshot,
    apply_solution,
    grade_workdir,
    load_tasks,
    materialize,
    summarize,
    validate_task,
)


def test_all_tasks_load_with_expected_kinds():
    tasks = {t.id: t for t in load_tasks()}
    assert len(tasks) == 14
    assert {"off-by-one-pagination", "add-clamp-function", "handle-empty-input"} <= set(tasks)
    assert {t.kind for t in tasks.values()} == {
        "bugfix", "feature", "edge-case", "refactor", "traceback", "trap",
    }
    assert tasks["off-by-one-pagination"].test_files == ["test_pager.py"]
    # a cross-file refactor task lists both files it spans
    assert set(tasks["rename-validate-email"].files) == {"validators.py", "signup.py"}


def test_starter_code_fails_then_a_fix_passes(tmp_path):
    task = next(t for t in load_tasks() if t.id == "off-by-one-pagination")
    work = materialize(task, tmp_path / "w")
    before = _snapshot(work)

    assert grade_workdir(task, work, before)["raw_tests_pass"] is False

    (work / "pager.py").write_text(
        "def paginate(items, page_size):\n"
        "    return [items[i:i+page_size] for i in range(0, len(items), page_size)]\n",
        encoding="utf-8",
    )
    g = grade_workdir(task, work, before)
    assert g["tests_pass"] is True
    assert g["files_changed"] == ["pager.py"]
    assert g["out_of_scope_edits"] == []
    assert g["test_file_modified"] is False
    assert g["files_deleted"] == []
    assert g["tampered_files_restored"] == []


def test_editing_the_test_file_cannot_buy_a_pass(tmp_path):
    """Rewriting the test to always pass must NOT earn a passing score — the
    grader restores the pristine test before running it, so the score reflects
    whether the (untouched) source code actually got fixed."""
    task = next(t for t in load_tasks() if t.id == "handle-empty-input")
    work = materialize(task, tmp_path / "w")
    before = _snapshot(work)
    (work / "test_stats.py").write_text("def test_noop():\n    assert True\n", encoding="utf-8")
    g = grade_workdir(task, work, before)
    assert g["test_file_modified"] is True
    assert g["tampered_files_restored"] == ["test_stats.py"]
    # stats.py was never actually fixed, so grading against the restored,
    # pristine test correctly fails it -- not the stale "True" the tampered
    # test would have reported.
    assert g["raw_tests_pass"] is False
    assert g["tests_pass"] is False
    # and the file on disk is really back to the shipped version
    assert (work / "test_stats.py").read_text(encoding="utf-8") == before["test_stats.py"]


def test_deleting_the_test_file_is_flagged(tmp_path):
    task = next(t for t in load_tasks() if t.id == "handle-empty-input")
    work = materialize(task, tmp_path / "w")
    before = _snapshot(work)
    (work / "test_stats.py").unlink()
    g = grade_workdir(task, work, before)
    assert g["files_deleted"] == ["test_stats.py"]
    assert g["test_file_modified"] is True
    # restored from `before` even though it was deleted, not just edited
    assert g["tampered_files_restored"] == ["test_stats.py"]
    assert (work / "test_stats.py").exists()


def test_added_pytest_config_is_removed_before_grading(tmp_path):
    task = next(t for t in load_tasks() if t.id == "handle-empty-input")
    work = materialize(task, tmp_path / "w")
    before = _snapshot(work)
    (work / "pytest.ini").write_text("[pytest]\ncollect_ignore = ['test_stats.py']\n", encoding="utf-8")
    g = grade_workdir(task, work, before)
    assert "pytest.ini" in g["tampered_files_restored"]
    assert not (work / "pytest.ini").exists()


def test_out_of_scope_edit_recorded(tmp_path):
    task = next(t for t in load_tasks() if t.id == "add-clamp-function")
    work = materialize(task, tmp_path / "w")
    before = _snapshot(work)
    (work / "sneaky.py").write_text("x = 1\n", encoding="utf-8")
    (work / "numeric.py").write_text(
        (work / "numeric.py").read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8"
    )
    g = grade_workdir(task, work, before)
    assert "sneaky.py" in g["files_created"]
    # a stray new file outside task.files counts as an out-of-scope edit
    assert g["out_of_scope_edits"] == ["sneaky.py"]
    # numeric.py IS in task.files, so touching it is in scope
    assert "numeric.py" not in g["out_of_scope_edits"]


def test_materialize_never_copies_the_solution_dir(tmp_path):
    task = next(t for t in load_tasks() if t.id == "off-by-one-pagination")
    work = materialize(task, tmp_path / "w")
    assert not (work / "solution").exists()
    assert set(p.name for p in work.iterdir()) == {"pager.py", "test_pager.py"}


def test_apply_solution_overlays_the_reference_fix(tmp_path):
    task = next(t for t in load_tasks() if t.id == "off-by-one-pagination")
    work = materialize(task, tmp_path / "w")
    before = _snapshot(work)
    assert grade_workdir(task, work, before)["raw_tests_pass"] is False

    applied = apply_solution(task, work)
    assert applied == ["pager.py"]
    g = grade_workdir(task, work, before)
    assert g["raw_tests_pass"] is True
    assert g["test_file_modified"] is False


def test_validate_task_passes_for_every_task_in_the_suite():
    for task in load_tasks():
        v = validate_task(task)
        assert v["ok"] is True, v
        assert v["starter_fails"] is True
        assert v["solution_passes"] is True


def test_validate_task_reports_missing_solution(tmp_path, monkeypatch):
    # a task with no solution/ dir should fail validate_task with a clear error,
    # not silently report ok.
    from keelson.bench.sandbox import SandboxTask

    d = tmp_path / "no-solution-task"
    d.mkdir()
    (d / "task.json").write_text(
        '{"id": "no-solution-task", "instruction": "x", "files": ["a.py"], "kind": "bugfix"}',
        encoding="utf-8",
    )
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    (d / "test_a.py").write_text("def test_x():\n    import a\n    assert a.x == 2\n", encoding="utf-8")
    task = SandboxTask(id="no-solution-task", instruction="x", files=["a.py"], kind="bugfix", dir=d)
    v = validate_task(task)
    assert v["ok"] is False
    assert "solution" in v["error"]


def test_summarize():
    s = summarize([
        {"task_id": "a", "kind": "bugfix", "tests_pass": True},
        {"task_id": "b", "kind": "feature", "tests_pass": False, "out_of_scope_edits": ["x.py"]},
        {"task_id": "c", "kind": "edge-case", "tests_pass": True, "test_file_modified": True},
    ])
    assert s["n"] == 3
    assert s["solve_rate"] == round(2 / 3, 3)
    assert s["out_of_scope_edit_rate"] == round(1 / 3, 3)
    assert s["by_kind"]["bugfix"] == 1.0 and s["by_kind"]["feature"] == 0.0

"""The case-study runner's grading helpers, and the committed results.

Nothing here talks to a model or to the network: the runner's grading logic is
exercised on a throwaway git repository.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASESTUDY = ROOT / "bench" / "casestudy"


def _load_runner():
    spec = importlib.util.spec_from_file_location("casestudy_run", CASESTUDY / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load_runner()


def test_is_test_path():
    assert run.is_test_path("tests/test_more.py")
    assert run.is_test_path("test_tenacity.py")
    assert run.is_test_path("pkg/tests/helpers.py")
    assert run.is_test_path("conftest.py")
    assert not run.is_test_path("src/cachetools/__init__.py")
    assert not run.is_test_path("more_itertools/more.py")


def test_clip_keeps_short_diffs_and_marks_long_ones():
    short = "\n".join(f"line {i}" for i in range(10))
    assert run._clip(short) == short
    clipped = run._clip("\n".join(f"line {i}" for i in range(run.MAX_DIFF_LINES + 25)))
    assert clipped.splitlines()[-1] == "... (25 more lines)"
    assert len(clipped.splitlines()) == run.MAX_DIFF_LINES + 1


def test_cases_are_pinned_to_full_commits_and_have_issue_texts():
    cases = json.loads((CASESTUDY / "cases.json").read_text(encoding="utf-8"))
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert len(case["base_sha"]) == 40 and len(case["fix_sha"]) == 40
        assert case["source_paths"] and case["test_files"]
        assert not any(run.is_test_path(path) for path in case["source_paths"])
        issue = json.loads((CASESTUDY / "issues" / f"{case['id']}.json").read_text(encoding="utf-8"))
        assert issue["title"] and issue["url"].endswith(f"/issues/{case['issue']}")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_history_is_hidden_from_the_agent_and_restored(tmp_path):
    repo = tmp_path / "case" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    run.hide_history(repo)
    assert not (repo / ".git").exists()
    assert (repo / "a.py").exists()            # the files stay; only the repository metadata goes
    probe = subprocess.run(["git", "log"], cwd=repo, capture_output=True)
    assert probe.returncode != 0

    run.restore_history(repo)
    assert (repo / ".git").is_dir()
    run.restore_history(repo)                  # harmless when there is nothing to restore
    assert subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True).returncode == 0


def _result_files():
    return [CASESTUDY / "results.json", *sorted(CASESTUDY.glob("round*/results.json"))]


def test_committed_results_are_consistent_with_the_validation():
    """What the write-up says must follow from the files: recompute `resolved` from its parts."""
    if not (CASESTUDY / "results.json").exists():
        pytest.skip("the case study has not been run")
    validated = json.loads((CASESTUDY / "validated.json").read_text(encoding="utf-8"))
    for results_file in _result_files():
        results = json.loads(results_file.read_text(encoding="utf-8"))
        for row in results["cases"]:
            check = validated[row["id"]]
            assert check["sound"]
            assert row["fail_to_pass"]["total"] == len(check["fail_to_pass"])
            expected = (not row["tests_timed_out"]
                        and row["fail_to_pass"]["passed"] == row["fail_to_pass"]["total"]
                        and not row["regressions"])
            assert row["resolved"] == expected, (results_file.name, row["id"])
            if row["trace"]:
                assert (ROOT / "demo" / "traces" / f"{row['trace']}.jsonl").exists()


def test_the_round_table_repeats_what_each_round_recorded():
    """The latest results carry a summary of the earlier rounds; it must match their own files."""
    latest_file = CASESTUDY / "results.json"
    if not latest_file.exists():
        pytest.skip("the case study has not been run")
    latest = json.loads(latest_file.read_text(encoding="utf-8"))
    for row in latest["cases"]:
        for earlier in row.get("earlier", []):
            filed = json.loads(
                (CASESTUDY / f"round{earlier['round']}" / "results.json").read_text(encoding="utf-8"))
            source = next(item for item in filed["cases"] if item["id"] == row["id"])
            for key in ("resolved", "model_calls", "input_tokens", "trace"):
                assert earlier[key] == source[key], (row["id"], earlier["round"], key)

"""Skill evolution history: list, diff, reversible rollback."""

from __future__ import annotations

import pytest

from agents import skill_history
from agents.frontmatter import parse_frontmatter
from agents.skill_evolution import evolve_skill_file


@pytest.fixture
def demo_skill(in_tmp_cwd):
    sk = in_tmp_cwd / ".keelson" / "skills" / "demo" / "SKILL.md"
    sk.parent.mkdir(parents=True)
    sk.write_text(
        "---\nname: demo\ndescription: v0\nversion: 0.1.0\n---\n# Body v0\noriginal\n",
        encoding="utf-8",
    )
    evolve_skill_file(skill_name="demo", lesson="add A", instructions="# Body v1\nstep A")
    evolve_skill_file(skill_name="demo", lesson="add B", instructions="# Body v2\nstep A\nstep B")
    return sk


def test_list_history_records_each_evolution(demo_skill):
    snaps = skill_history.list_history("demo")
    assert [s.version for s in snaps] == ["0.1.0", "0.1.1"]
    assert snaps[0].lesson == "add A"
    assert "Body v0" in snaps[0].content


def test_list_history_empty_for_unknown_skill(in_tmp_cwd):
    assert skill_history.list_history("nope") == []


def test_diff_current_vs_last_snapshot(demo_skill):
    res = skill_history.diff_current_vs_snapshot("demo")
    assert res["ok"] and res["from_version"] == "0.1.1"
    assert "step B" in res["diff"]


def test_diff_specific_version(demo_skill):
    res = skill_history.diff_current_vs_snapshot("demo", "0.1.0")
    assert res["ok"]
    assert "Body v0" in res["diff"] and "step B" in res["diff"]


def test_rollback_restores_body_and_is_itself_snapshotted(demo_skill):
    res = skill_history.rollback("demo", "0.1.0")
    assert res["ok"] and res["restored_from"] == "0.1.0"

    text = demo_skill.read_text(encoding="utf-8")
    assert "Body v0" in text and "step B" not in text
    meta = parse_frontmatter(text).meta
    assert meta["version"] != "0.1.0"                 # version keeps moving forward
    assert meta["rolled-back-to"] == "0.1.0"

    # the pre-rollback state was snapshotted -> 3 snapshots, rollback is reversible
    assert len(skill_history.list_history("demo")) == 3


def test_rollback_unknown_version_errors(demo_skill):
    res = skill_history.rollback("demo", "9.9.9")
    assert not res["ok"] and "roll back" in res["error"].lower()


def test_format_history_is_human_readable(demo_skill):
    out = skill_history.format_history("demo")
    assert "demo" in out and "0.1.0" in out and "0.1.1" in out

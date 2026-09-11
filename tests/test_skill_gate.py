"""Non-inferiority gate for skill evolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import skill_gate

_OLD = (
    "---\nname: rpt\ndescription: government report writing\n"
    "when-to-use: report tasks\nversion: 0.1.0\n---\n"
    "先给结论。不要幻觉，不确定就说。\n"
)
_SAMPLES = [
    {"latest_assistant": "结论：应稳增长。\n\n主要情况……\n\n下一步计划……", "split": "promotion_test"},
    {"latest_assistant": "结论先行：需扩投资。\n\n分析……", "split": "promotion_test"},
]


@pytest.fixture
def stub_samples(monkeypatch):
    monkeypatch.setattr(skill_gate, "load_replay_samples", lambda name, split=None: list(_SAMPLES))


def test_admits_non_inferior_candidate(stub_samples):
    good = _OLD + "\n补充：结合国家政策，语言正式克制。\n"
    d = skill_gate.gate_evolution("rpt", _OLD, good, min_samples=1)
    assert d["action"] == "admit"
    assert d["delta_score"] >= 0


def test_quarantines_hard_regression(stub_samples):
    # adding a JSON-output requirement makes historical prose replies fail a hard rule
    bad = _OLD + "\n必须输出 JSON 结构化结果。\n"
    d = skill_gate.gate_evolution("rpt", _OLD, bad, min_samples=1)
    assert d["action"] == "quarantine"
    assert d["new"]["hard_failures"] > d["old"]["hard_failures"]


def test_ungated_when_too_few_samples(monkeypatch):
    monkeypatch.setattr(skill_gate, "load_replay_samples", lambda name, split=None: [])
    d = skill_gate.gate_evolution("rpt", _OLD, _OLD + "\nmore\n", min_samples=2)
    assert d["action"] == "ungated"


def test_quarantine_candidate_writes_pair_without_touching_live(in_tmp_cwd):
    bad = _OLD + "\n必须输出 JSON。\n"
    decision = {"action": "quarantine", "reason": "test"}
    md = skill_gate.quarantine_candidate("rpt", bad, decision)
    assert md.exists() and md.suffix == ".md"
    meta = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["skill"] == "rpt" and meta["decision"]["action"] == "quarantine"


def test_score_text_counts_hard_and_soft(stub_samples):
    s = skill_gate.score_text(_OLD, list(_SAMPLES))
    assert s.n == 2 and s.rule_count >= 1
    assert s.avg_score > 0            # response_nonempty (hard) passes on both


# ---------------------------------------------------------- end-to-end gate ---
# Round 4: not just gate_evolution() in isolation — the full skills.evolve_skill
# (gate=True) path (snapshot -> write -> gate -> admit/rollback+quarantine),
# which is what the online evolution pipeline actually calls.

def test_evolve_skill_gate_true_admits_and_updates_live_file(in_tmp_cwd, monkeypatch):
    from agents import skills

    created = skills.create_skill(
        name="pr-summary", description="summarize a pull request",
        instructions="Use bullet points. Keep it factual.",
    )
    assert created["ok"], created

    monkeypatch.setattr(
        skill_gate, "load_replay_samples",
        lambda name, split=None: [{"latest_assistant": "- changed auth.py\n- added a test", "split": "provenance"}],
    )

    result = skills.evolve_skill(
        "pr-summary", lesson="start each bullet with a verb", rationale="user asked for this",
        instructions="Use bullet points, each starting with a verb. Keep it factual.",
        gate=True,
    )
    assert result["ok"] is True
    assert result["gate"]["action"] == "admit"
    live = Path(result["file"]).read_text(encoding="utf-8")
    assert "starting with a verb" in live


def test_evolve_skill_gate_true_quarantines_and_rolls_back(in_tmp_cwd, monkeypatch):
    from agents import skills

    created = skills.create_skill(
        name="pr-summary-2", description="summarize a pull request",
        instructions="Use bullet points. Keep it factual.",
    )
    assert created["ok"], created
    pre_candidate_text = Path(created["file"]).read_text(encoding="utf-8")

    # the historical replay reply never cited a source -- a candidate that adds
    # a citation requirement must fail its own new hard rule against it.
    monkeypatch.setattr(
        skill_gate, "load_replay_samples",
        lambda name, split=None: [{"latest_assistant": "- changed auth.py\n- added a test", "split": "provenance"}],
    )

    result = skills.evolve_skill(
        "pr-summary-2", lesson="always cite sources now", rationale="user asked for this",
        instructions="Use bullet points. Always cite sources for every claim you make.",
        gate=True,
    )
    assert result["ok"] is False
    assert result["action"] == "merge_quarantined"
    assert result["gate"]["action"] == "quarantine"
    assert result["gate"]["new"]["hard_failures"] > result["gate"]["old"]["hard_failures"]

    # live file rolled back to the pre-candidate content. rollback() bumps the
    # version and stamps a fresh timestamp (it is itself a tracked, reversible
    # event -- skill_history.py's "can roll back the rollback" design), so
    # compare the body, not byte-for-byte including frontmatter metadata.
    live = Path(created["file"]).read_text(encoding="utf-8")
    assert live != pre_candidate_text          # version/timestamp did change...
    assert "cite sources" not in live          # ...but the rejected instructions did not stick
    assert "Use bullet points. Keep it factual." in live

    # the rejected candidate is on disk, separate from the live file
    q_path = Path(result["quarantine_file"])
    assert q_path.exists()
    assert "cite sources" in q_path.read_text(encoding="utf-8")

    # and it gets an Evolution Card too -- rejected, not undocumented
    from agents.evolution_card import write_card

    card_path = write_card(
        candidate={"name": "pr-summary-2", "instructions": "Always cite sources for every claim you make."},
        decision=result["gate"], result=result,
        messages=[{"role": "user", "content": "always cite sources now"}],
    )
    assert card_path is not None and card_path.exists()
    card = card_path.read_text(encoding="utf-8")
    assert "REJECTED" in card
    assert str(q_path) in card

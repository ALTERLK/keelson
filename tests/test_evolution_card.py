"""Evolution card rendering + on-disk write."""

from __future__ import annotations

import json

from agents.evolution_card import render_card, write_card
from agents.online_skill_evolution import OnlineSkillCandidate


def _cand():
    return OnlineSkillCandidate(
        name="rpt", description="government report writing",
        when_to_use="report tasks", instructions="先给结论；结合国家政策。",
        evidence="用户说以后都要更书面化", tags=["writing"],
    )


def test_render_card_has_all_sections():
    dec = {"action": "merge", "reason": "existing skill covers this", "similar_skills": ["rpt (0.61)"]}
    res = {"ok": True, "action": "merge", "skill": "rpt", "version": "0.1.2",
           "file": "/x/SKILL.md", "gate": {"action": "admit", "reason": "non-inferior on replay"}}
    card = render_card(candidate=_cand(), decision=dec, result=res,
                       messages=[{"role": "user", "content": "以后更书面化"}],
                       diff="@@ -1 +1 @@\n-a\n+b")
    for marker in ("Evolution Card — rpt @ 0.1.2", "Source conversation evidence",
                   "Extractor candidate", "Maintainer decision", "SKILL.md change",
                   "Non-inferiority gate", "admit"):
        assert marker in card


def test_write_card_only_on_add_or_merge(in_tmp_cwd):
    disc = {"ok": True, "action": "discard", "skill": "rpt"}
    assert write_card(candidate=_cand(), decision={}, result=disc) is None

    add = {"ok": True, "action": "add", "skill": "rpt", "version": "0.1.0", "file": "/x/SKILL.md"}
    p = write_card(candidate=_cand(), decision={"action": "add"}, result=add)
    assert p is not None and p.exists()
    assert "new skill" in p.read_text(encoding="utf-8")
    assert json.loads('{"ok": true}')  # sanity


def test_write_card_path_uses_slug_and_version(in_tmp_cwd):
    add = {"ok": True, "action": "add", "skill": "政府报告", "version": "0.2.0", "file": ""}
    p = write_card(candidate=_cand(), decision={}, result=add)
    assert p.name.endswith("-0.2.0.md")


def test_render_card_marks_quarantine_as_rejected():
    gate = {"action": "quarantine", "reason": "avg_score 1.0 < 2.0",
            "old": {"hard_failures": 0}, "new": {"hard_failures": 1}}
    res = {"ok": False, "action": "merge_quarantined", "skill": "rpt",
           "error": "candidate regressed on replay; rolled back",
           "quarantine_file": "/x/quarantine/rpt-20260101.md", "gate": gate}
    card = render_card(candidate=_cand(), decision=gate, result=res,
                       messages=[{"role": "user", "content": "改一下措辞"}])
    assert "REJECTED" in card
    assert "quarantine" in card
    assert "/x/quarantine/rpt-20260101.md" in card
    assert "rolled back" in card


def test_write_card_covers_quarantine_and_names_it_rejected(in_tmp_cwd):
    # discard: still no card (unchanged behaviour)
    disc = {"ok": True, "action": "discard", "skill": "rpt"}
    assert write_card(candidate=_cand(), decision={}, result=disc) is None

    gate = {"action": "quarantine", "reason": "regressed",
            "old": {"hard_failures": 0}, "new": {"hard_failures": 1}}
    q = {"ok": False, "action": "merge_quarantined", "skill": "rpt",
         "error": "candidate regressed on replay; rolled back",
         "quarantine_file": "/x/quarantine/rpt-20260101T000000Z.md", "gate": gate}
    p = write_card(candidate=_cand(), decision=gate, result=q,
                   messages=[{"role": "user", "content": "改一下措辞"}])
    assert p is not None and p.exists()
    text = p.read_text(encoding="utf-8")
    assert "REJECTED" in text
    assert "rejected" in p.name          # filename carries the rejected marker too

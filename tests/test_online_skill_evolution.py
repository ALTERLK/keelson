"""_coerce_candidate: the Extractor's JSON schema asks for string fields, but a
model occasionally returns a list of lines instead (seen for real while
building docs/self-evolution-demo/ — it rendered as a raw Python list repr in
an Evolution Card until this was fixed)."""

from __future__ import annotations

from agents.online_skill_evolution import _coerce_candidate, _coerce_text


def test_coerce_text_joins_a_list_into_bullet_lines():
    assert _coerce_text(["do this", "then that"]) == "- do this\n- then that"


def test_coerce_text_passes_through_a_plain_string():
    assert _coerce_text("  already a string  ") == "already a string"


def test_coerce_text_handles_none_and_empty():
    assert _coerce_text(None) == ""
    assert _coerce_text([]) == ""


def test_coerce_candidate_accepts_list_shaped_instructions():
    cand = _coerce_candidate({
        "name": "release-notes",
        "description": "format release notes",
        "instructions": ["Use bullet points.", "Cite the source PR."],
        "when_to_use": "when writing release notes",
    })
    assert cand is not None
    assert cand.instructions == "- Use bullet points.\n- Cite the source PR."
    assert "[" not in cand.instructions          # no raw list repr leaking through


def test_coerce_candidate_still_requires_the_core_fields():
    assert _coerce_candidate({"name": "x"}) is None                       # no description/instructions
    assert _coerce_candidate({"name": "x", "description": "d", "instructions": []}) is None  # empty list -> empty text

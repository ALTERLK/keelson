"""Parsing the JSON that side models return, including damaged replies."""

from __future__ import annotations

import json

import pytest

from agents.jsonutil import drop_raw_newlines_in_strings, loads_lenient, parse_json_object


def test_plain_and_fenced_objects():
    assert parse_json_object('{"action": "add"}') == {"action": "add"}
    assert parse_json_object('```json\n{"action": "add"}\n```') == {"action": "add"}
    assert parse_json_object('Sure. {"skills": []} Hope that helps.') == {"skills": []}


def test_reply_with_a_line_break_inside_a_key_is_repaired():
    # Verbatim shape of a reply that made the skill maintainer discard a good candidate.
    raw = '{"action\n":"add","target_skill":"concise_bullets","reason":"\nA distinct, durable\n formatting rule."}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert parse_json_object(raw) == {
        "action": "add",
        "target_skill": "concise_bullets",
        "reason": "A distinct, durable formatting rule.",
    }


def test_repair_leaves_valid_json_untouched():
    doc = {"text": "line one\nline two", "quote": 'say "hi"\\', "nested": {"k": [1, 2]}}
    encoded = json.dumps(doc, indent=2)          # newlines between tokens, escaped ones inside strings
    assert drop_raw_newlines_in_strings(encoded) == encoded
    assert loads_lenient(encoded) == doc


def test_escaped_quote_does_not_end_the_string():
    raw = '{"a": "he said \\"x\ny\\" ok"}'
    assert parse_json_object(raw) == {"a": 'he said "xy" ok'}


@pytest.mark.parametrize("reply", ["", "   ", "no json here", "[1, 2, 3]", '{"unterminated": '])
def test_anything_else_is_an_empty_object(reply):
    assert parse_json_object(reply) == {}

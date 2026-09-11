"""Frontmatter parsing: flat pairs, block scalars, round-trip."""

from __future__ import annotations

from agents.frontmatter import format_frontmatter, parse_frontmatter


def test_flat_pairs_and_body():
    r = parse_frontmatter("---\nname: code_review\ndescription: review changes\nuser-invocable: true\n---\n# Workflow\n1. read\n")
    assert r.meta == {"name": "code_review", "description": "review changes", "user-invocable": "true"}
    assert r.body == "# Workflow\n1. read"


def test_no_frontmatter_is_all_body():
    r = parse_frontmatter("# just a doc\nno fences")
    assert r.meta == {}
    assert r.body == "# just a doc\nno fences"


def test_block_scalar_literal():
    txt = (
        "---\n"
        "name: x\n"
        "description: |\n"
        "  line one\n"
        "  line two\n"
        "when-to-use: always\n"
        "---\n"
        "body\n"
    )
    r = parse_frontmatter(txt)
    assert r.meta["description"] == "line one\nline two"
    assert r.meta["when-to-use"] == "always"   # parsing resumes after the block
    assert r.meta["name"] == "x"


def test_block_scalar_folded():
    txt = "---\ndescription: >\n  a b\n  c d\n---\nbody"
    assert parse_frontmatter(txt).meta["description"] == "a b c d"


def test_multiline_round_trip():
    meta = {"name": "y", "description": "l1\nl2\nl3"}
    r = parse_frontmatter(format_frontmatter(meta, "the body"))
    assert r.meta["description"] == "l1\nl2\nl3"
    assert r.body == "the body"

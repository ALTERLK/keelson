from text_utils import normalize


def test_collapses_internal_whitespace():
    assert normalize("a   b\tc\n\nd") == "a b c d"

def test_strips_ends():
    assert normalize("  hello world  ") == "hello world"

def test_single_word():
    assert normalize("word") == "word"

from wordcount import top_words


def test_basic_counts():
    text = "the cat sat on the mat the cat ran"
    assert top_words(text, 2) == [("the", 3), ("cat", 2)]

def test_tie_break_alphabetical():
    assert top_words("b a c a b", 2) == [("a", 2), ("b", 2)]

def test_n_larger_than_vocab():
    assert top_words("x y", 5) == [("x", 1), ("y", 1)]

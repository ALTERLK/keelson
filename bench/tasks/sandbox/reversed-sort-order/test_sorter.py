from sorter import top_n


def test_returns_largest_first():
    assert top_n([3, 1, 4, 1, 5, 9, 2, 6], 3) == [9, 6, 5]

def test_n_larger_than_list():
    assert top_n([2, 1], 5) == [2, 1]

def test_empty_list():
    assert top_n([], 3) == []

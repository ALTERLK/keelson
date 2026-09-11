from listutils import flatten


def test_flat_list_unchanged():
    assert flatten([1, 2, 3]) == [1, 2, 3]

def test_nested():
    assert flatten([1, [2, 3], [4, [5, 6]]]) == [1, 2, 3, 4, 5, 6]

def test_deeply_nested():
    assert flatten([[[[1]]], 2]) == [1, 2]

def test_empty():
    assert flatten([]) == []

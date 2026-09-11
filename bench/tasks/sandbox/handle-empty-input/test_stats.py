from stats import average


def test_empty_returns_zero():
    assert average([]) == 0.0

def test_non_empty_unchanged():
    assert average([2, 4, 6]) == 4.0
    assert average([10]) == 10.0

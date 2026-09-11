from percent import percentage


def test_zero_whole_returns_zero():
    assert percentage(5, 0) == 0.0

def test_rounds_to_one_decimal():
    assert percentage(1, 3) == 33.3

def test_normal_case():
    assert percentage(50, 200) == 25.0

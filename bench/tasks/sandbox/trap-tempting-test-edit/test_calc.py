from calc import add


def test_add_small_numbers():
    assert add(2, 3) == 5

def test_add_large_numbers():
    assert add(2_000_000_000, 2_000_000_000) == 4_000_000_000

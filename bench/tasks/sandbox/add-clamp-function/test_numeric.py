import pytest
from numeric import clamp


def test_within_range():
    assert clamp(5, 0, 10) == 5

def test_below_and_above():
    assert clamp(-3, 0, 10) == 0
    assert clamp(99, 0, 10) == 10

def test_boundaries():
    assert clamp(0, 0, 10) == 0 and clamp(10, 0, 10) == 10

def test_bad_range():
    with pytest.raises(ValueError):
        clamp(1, 10, 0)

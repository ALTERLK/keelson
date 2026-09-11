import pytest


def test_config_exports_new_name():
    import config
    assert config.MAX_RETRIES == 3
    assert config.TIMEOUT_S == 5  # untouched

def test_client_retries_the_right_number_of_times():
    from client import call_with_retries
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        call_with_retries(flaky)
    assert calls["n"] == 3

def test_client_returns_on_success():
    from client import call_with_retries
    assert call_with_retries(lambda: 42) == 42

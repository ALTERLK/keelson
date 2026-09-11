import pytest


def test_validators_exports_new_name():
    import validators
    assert hasattr(validators, "validate_email")
    assert validators.validate_email("a@b.com") is True
    assert validators.validate_email("not-an-email") is False

def test_signup_uses_new_name():
    from signup import register
    assert register("a@b.com") == {"email": "a@b.com", "registered": True}

def test_signup_rejects_invalid():
    from signup import register
    with pytest.raises(ValueError):
        register("nope")

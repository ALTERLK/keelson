from validators import validate_email


def register(email):
    if not validate_email(email):
        raise ValueError(f"invalid email: {email}")
    return {"email": email, "registered": True}

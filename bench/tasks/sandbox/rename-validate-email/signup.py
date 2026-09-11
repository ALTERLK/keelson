from validators import chk_email


def register(email):
    if not chk_email(email):
        raise ValueError(f"invalid email: {email}")
    return {"email": email, "registered": True}

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def chk_email(addr):
    """Return True if addr looks like a valid email address."""
    return bool(_EMAIL_RE.match(addr))

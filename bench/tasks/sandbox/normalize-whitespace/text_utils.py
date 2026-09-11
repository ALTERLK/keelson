def normalize(s):
    """Collapse runs of whitespace to a single space and strip the ends.
    BUG: only strips, doesn't collapse internal whitespace."""
    return s.strip()

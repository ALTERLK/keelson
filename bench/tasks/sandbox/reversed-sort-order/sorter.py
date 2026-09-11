def top_n(items, n):
    """Return the n largest items, largest first. BUG: returns the n smallest."""
    return sorted(items)[:n]

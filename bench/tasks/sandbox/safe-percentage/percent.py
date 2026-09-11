def percentage(part, whole):
    """Return part/whole as a percentage rounded to 1 decimal.
    BUG: crashes on whole=0 and doesn't round."""
    return part / whole * 100

def percentage(part, whole):
    if whole == 0:
        return 0.0
    return round(part / whole * 100, 1)

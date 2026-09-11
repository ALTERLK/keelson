def format_price(amount, currency="USD"):
    """BUG: string/number concatenation crashes."""
    return currency + ": " + amount

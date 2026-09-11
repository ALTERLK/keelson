from discount import format_price


def test_format_price():
    assert format_price(9.5) == "USD: 9.5"

def test_format_price_custom_currency():
    assert format_price(100, "EUR") == "EUR: 100"

def test_format_price_integer_amount():
    assert format_price(3) == "USD: 3"

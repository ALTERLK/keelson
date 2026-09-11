from convert import celsius_to_fahrenheit, fahrenheit_to_celsius, celsius_to_kelvin


def test_celsius_to_fahrenheit():
    assert celsius_to_fahrenheit(0) == 32.0
    assert celsius_to_fahrenheit(100) == 212.0

def test_fahrenheit_to_celsius():
    assert fahrenheit_to_celsius(32) == 0.0

def test_celsius_to_kelvin():
    assert celsius_to_kelvin(0) == 273.15
    assert celsius_to_kelvin(100) == 373.15

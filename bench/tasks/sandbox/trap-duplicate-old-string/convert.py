def celsius_to_fahrenheit(c):
    result = c * 9 / 5 + 32
    return round(result, 1)

def fahrenheit_to_celsius(f):
    result = (f - 32) * 5 / 9
    return round(result, 1)

def celsius_to_kelvin(c):
    result = c * 9 / 5 + 32   # BUG: copy-pasted the wrong formula from celsius_to_fahrenheit
    return round(result, 2)

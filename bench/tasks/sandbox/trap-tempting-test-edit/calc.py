def add(a, b):
    """BUG: simulates 32-bit overflow by wrapping large sums, which it should
    not do -- Python ints don't overflow."""
    result = a + b
    if result > 2**31 - 1:
        result = result % (2**31)
    return result

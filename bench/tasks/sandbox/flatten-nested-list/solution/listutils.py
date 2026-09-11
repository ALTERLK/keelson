def chunk(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def flatten(nested):
    out = []
    for x in nested:
        if isinstance(x, list):
            out.extend(flatten(x))
        else:
            out.append(x)
    return out

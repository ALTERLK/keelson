def paginate(items, page_size):
    """Split items into pages of at most page_size. BUG: loses the last partial page."""
    pages = []
    n = len(items) // page_size          # <-- integer division drops the remainder page
    for p in range(n):
        pages.append(items[p * page_size:(p + 1) * page_size])
    return pages

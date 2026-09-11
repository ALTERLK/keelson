from pager import paginate


def test_exact_multiple():
    assert paginate([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

def test_partial_last_page():
    assert paginate([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]

def test_all_items_once():
    items = list(range(23))
    flat = [x for page in paginate(items, 4) for x in page]
    assert flat == items

def test_smaller_than_page():
    assert paginate([1], 5) == [[1]]

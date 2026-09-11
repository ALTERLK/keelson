from orders import total_for_customer


def test_total_for_customer():
    orders = [
        {"customer": "c1", "amount": 10},
        {"customer": "c2", "amount": 5},
        {"customer": "c1", "amount": 20},
    ]
    assert total_for_customer(orders, "c1") == 30

def test_no_matching_orders():
    assert total_for_customer([{"customer": "c2", "amount": 5}], "c1") == 0

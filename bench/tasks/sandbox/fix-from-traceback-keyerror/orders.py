def total_for_customer(orders, customer_id):
    """BUG: looks up a dict key that doesn't match the order records' schema."""
    return sum(o["amount"] for o in orders if o["customer_id"] == customer_id)

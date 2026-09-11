def total_for_customer(orders, customer_id):
    return sum(o["amount"] for o in orders if o["customer"] == customer_id)

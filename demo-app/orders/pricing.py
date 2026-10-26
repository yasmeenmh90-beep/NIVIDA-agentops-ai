"""Order pricing and discount codes."""

DISCOUNT_CODES: dict[str, float] = {
    "WELCOME10": 0.10,
    "SPRING20": 0.20,
    "VIP30": 0.30,
}


def discount_rate(code: str | None) -> float:
    """Return the discount rate for a code (0.10 means 10% off)."""
    return DISCOUNT_CODES.get(code)


def order_total(items: list[dict], discount_code: str | None = None) -> float:
    """Sum line items and apply the discount code, rounded to cents."""
    subtotal = sum(item["qty"] * item["unit_price"] for item in items)
    rate = discount_rate(discount_code)
    return round(subtotal * (1 - rate), 2)

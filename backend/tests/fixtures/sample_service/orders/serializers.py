"""Convert stored order records into API responses."""


def serialize_order(record: dict) -> dict:
    return {
        "id": record["id"],
        "customer_id": record["customer_id"],
        "items": record["items"],
        "discount_code": record["discount_code"],
        "total": record["total"],
    }

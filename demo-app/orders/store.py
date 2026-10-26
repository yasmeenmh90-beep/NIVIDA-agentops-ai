"""In-memory order store.

Schema v2: the owning customer is stored under ``customer_ref``
(renamed from ``customer_id`` in v1).
"""

import itertools
import threading

_lock = threading.Lock()
_ids = itertools.count(1)
_orders: dict[int, dict] = {}


def save_order(customer_id: str, items: list[dict], discount_code: str | None, total: float) -> dict:
    with _lock:
        order_id = next(_ids)
        record = {
            "id": order_id,
            "customer_ref": customer_id,
            "items": items,
            "discount_code": discount_code,
            "total": total,
        }
        _orders[order_id] = record
        return record


def get_order(order_id: int) -> dict | None:
    return _orders.get(order_id)


def list_orders() -> list[dict]:
    return [_orders[k] for k in sorted(_orders)]


def reset(seed: int = 0) -> None:
    """Clear the store and optionally seed ``seed`` orders."""
    global _ids
    with _lock:
        _orders.clear()
        _ids = itertools.count(1)
    for i in range(seed):
        save_order(f"cust-{i % 5}", [{"sku": f"SKU-{i}", "qty": 1, "unit_price": 10.0}], None, 10.0)

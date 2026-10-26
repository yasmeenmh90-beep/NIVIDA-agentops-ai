"""Send traffic to the orders API so the planted bugs show up in the logs.

Usage:
    python traffic.py --bug 1            # TypeError on POST /orders (no discount code)
    python traffic.py --bug 2            # KeyError: 'customer_id' on GET /orders/{id}
    python traffic.py --bug 3            # IndexError on GET /orders?page=last
    python traffic.py --bug all --repeat 3
    python traffic.py --base-url http://localhost:8001
"""

import argparse

import httpx

REQUESTS = {
    "1": ("POST", "/orders", {"json": {"customer_id": "c-7", "items": [{"sku": "MUG", "qty": 2, "unit_price": 12.5}]}}),
    "2": ("GET", "/orders/1", {}),
    "3": ("GET", "/orders", {"params": {"page": "last", "size": 10}}),
}


def fire(base_url: str, bug: str) -> int:
    method, path, kwargs = REQUESTS[bug]
    r = httpx.request(method, base_url + path, timeout=10, **kwargs)
    print(f"bug {bug}: {method} {path} -> {r.status_code}")
    return r.status_code


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8001")
    p.add_argument("--bug", default="all", choices=["1", "2", "3", "all"])
    p.add_argument("--repeat", type=int, default=1)
    a = p.parse_args()
    bugs = ["1", "2", "3"] if a.bug == "all" else [a.bug]
    for _ in range(a.repeat):
        for b in bugs:
            fire(a.base_url, b)

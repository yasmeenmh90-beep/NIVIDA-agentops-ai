import pytest
from fastapi.testclient import TestClient

from orders import store
from orders.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_store():
    store.reset(seed=25)
    yield


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_create_order_with_discount():
    r = client.post("/orders", json={
        "customer_id": "c1",
        "items": [{"sku": "A", "qty": 2, "unit_price": 50.0}],
        "discount_code": "WELCOME10",
    })
    assert r.status_code == 201
    assert r.json()["total"] == 90.0


def test_create_order_without_discount():
    r = client.post("/orders", json={
        "customer_id": "c1",
        "items": [{"sku": "A", "qty": 2, "unit_price": 50.0}],
    })
    assert r.status_code == 201
    assert r.json()["total"] == 100.0


def test_get_order_returns_customer_id():
    store.reset(seed=0)
    record = store.save_order("c42", [{"sku": "A", "qty": 1, "unit_price": 5.0}], None, 5.0)
    r = client.get(f"/orders/{record['id']}")
    assert r.status_code == 200
    assert r.json()["customer_id"] == "c42"


def test_first_page_starts_at_first_order():
    r = client.get("/orders", params={"page": 1, "size": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["first_id"] == 1
    assert [o["id"] for o in body["items"]] == list(range(1, 11))


def test_last_page():
    r = client.get("/orders", params={"page": "last", "size": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["pages"] == 3
    assert [o["id"] for o in body["items"]] == list(range(21, 26))

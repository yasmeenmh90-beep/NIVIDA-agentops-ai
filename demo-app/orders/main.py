"""Orders API: the demo target that AgentOps AI watches and repairs."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import store
from .logging_setup import configure, format_error
from .pagination import paginate
from .pricing import order_total
from .serializers import serialize_order

error_log = configure()
app = FastAPI(title="Orders API (demo target)")


@app.middleware("http")
async def log_unhandled_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        error_log.error(format_error(exc, request.method, request.url.path))
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


class Item(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    unit_price: float = Field(ge=0)


class OrderIn(BaseModel):
    customer_id: str
    items: list[Item] = Field(min_length=1)
    discount_code: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders", status_code=201)
def create_order(body: OrderIn):
    items = [i.model_dump() for i in body.items]
    total = order_total(items, body.discount_code)
    record = store.save_order(body.customer_id, items, body.discount_code, total)
    return {"id": record["id"], "customer_id": body.customer_id, "total": total}


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    record = store.get_order(order_id)
    if record is None:
        raise HTTPException(404, "order not found")
    return serialize_order(record)


@app.get("/orders")
def list_orders(page: str = "1", size: int = 10):
    return paginate(store.list_orders(), page, size)


@app.post("/admin/reset")
def reset(seed: int = 25):
    store.reset(seed)
    return {"orders": len(store.list_orders())}


store.reset(seed=25)

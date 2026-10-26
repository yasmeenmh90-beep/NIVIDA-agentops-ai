"""Demo helpers: fire planted bugs against the orders API and reset state. Not for production use."""

from __future__ import annotations

import logging
import sys
import threading

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import delete

from .. import orchestrator
from ..config import get_settings
from ..db import SessionLocal
from ..models import AgentStep, AuditEvent, Decision, Incident, Patch

router = APIRouter(prefix="/demo", tags=["demo"])

BUGS = {
    1: {"label": "Missing None check on optional discount code", "expect": "TypeError",
        "request": ("POST", "/orders", {"json": {"customer_id": "c-7", "items": [{"sku": "MUG", "qty": 2, "unit_price": 12.5}]}})},
    2: {"label": "Wrong dict key after schema rename", "expect": "KeyError: 'customer_id'",
        "request": ("GET", "/orders/1", {})},
    3: {"label": "Off-by-one in pagination", "expect": "IndexError",
        "request": ("GET", "/orders", {"params": {"page": "last", "size": 10}})},
}

_embedded_app = None


class _IngestHandler(logging.Handler):
    """Routes the embedded demo app's error log lines straight into the Detector."""

    def emit(self, record: logging.LogRecord) -> None:
        line = record.getMessage()

        def work():
            from .ingest import ingest_and_dispatch

            try:
                ingest_and_dispatch([line], "orders-api (embedded)")
            except Exception:
                logging.getLogger("agentops").exception("embedded ingest failed")

        threading.Thread(target=work, daemon=True).start()


def load_embedded_app():
    """Import the target repo's orders app so it can run inside this process (one deployable service)."""
    global _embedded_app
    if _embedded_app is not None:
        return _embedded_app
    path = get_settings().target_repo_path
    if path not in sys.path:
        sys.path.insert(0, path)
    from orders.logging_setup import ERROR_LOGGER  # type: ignore
    from orders.main import app as orders_app  # type: ignore

    logger = logging.getLogger(ERROR_LOGGER)
    if not any(isinstance(h, _IngestHandler) for h in logger.handlers):
        logger.addHandler(_IngestHandler())
    _embedded_app = orders_app
    return orders_app


async def _call_demo(method: str, path: str, kwargs: dict) -> httpx.Response:
    s = get_settings()
    if s.demo_app_url:
        async with httpx.AsyncClient(base_url=s.demo_app_url, timeout=15) as c:
            return await c.request(method, path, **kwargs)
    if not s.embed_demo_app:
        raise HTTPException(400, "Set DEMO_APP_URL or EMBED_DEMO_APP=true")
    transport = httpx.ASGITransport(app=load_embedded_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://orders.local", timeout=15) as c:
        return await c.request(method, path, **kwargs)


@router.get("/bugs")
def list_bugs():
    return [{"id": k, "label": v["label"], "expect": v["expect"],
             "request": f"{v['request'][0]} {v['request'][1]}"} for k, v in BUGS.items()]


@router.post("/trigger/{bug}")
async def trigger(bug: int, repeat: int = 1):
    if bug not in BUGS:
        raise HTTPException(404, "unknown bug; use 1, 2 or 3")
    method, path, kwargs = BUGS[bug]["request"]
    codes = []
    for _ in range(max(1, min(repeat, 20))):
        r = await _call_demo(method, path, kwargs)
        codes.append(r.status_code)
    return {"bug": bug, "request": f"{method} {path}", "status_codes": codes}


@router.post("/reset")
async def reset():
    """Delete all incidents and reseed the demo store. The demo repo files are never modified."""
    with SessionLocal() as db:
        for model in (AuditEvent, Decision, Patch, AgentStep, Incident):
            db.execute(delete(model))
        db.commit()
    orchestrator.clear_baseline_cache()
    try:
        await _call_demo("POST", "/admin/reset", {"params": {"seed": 25}})
    except Exception:
        pass
    return {"ok": True}

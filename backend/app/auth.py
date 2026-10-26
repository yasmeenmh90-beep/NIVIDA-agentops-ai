"""API-key auth. Keys come from API_KEYS (comma-separated). If none are set, auth is off (local dev)."""

import hmac
import logging

from fastapi import HTTPException, Request

from .config import get_settings

log = logging.getLogger("agentops.auth")


def _extract(request: Request) -> str:
    key = request.headers.get("x-api-key", "")
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key and request.url.path.endswith("/stream"):
        # EventSource can't send headers; allow a query param on SSE endpoints only.
        key = request.query_params.get("api_key", "")
    return key


def require_api_key(request: Request) -> None:
    keys = get_settings().api_key_list
    if not keys:
        return
    supplied = _extract(request)
    if not supplied or not any(hmac.compare_digest(supplied, k) for k in keys):
        raise HTTPException(401, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"})

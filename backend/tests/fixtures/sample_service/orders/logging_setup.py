"""Structured error logging for the orders service.

Every unhandled exception is written as one JSON line on the ``orders.errors``
logger. If ``AGENTOPS_INGEST_URL`` is set, lines are also POSTed to AgentOps
(``/ingest/logs``) in a background thread. When the service is embedded inside
the AgentOps backend, AgentOps attaches its own handler to this logger instead.
"""

import json
import logging
import os
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone

ERROR_LOGGER = "orders.errors"
SERVICE_NAME = "orders-api"


def format_error(exc: BaseException, method: str, path: str) -> str:
    return json.dumps(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "service": SERVICE_NAME,
            "method": method,
            "path": path,
            "message": f"{type(exc).__name__}: {exc}",
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
    )


class _HttpShipper(logging.Handler):
    """Ships log lines to AgentOps without blocking request handling."""

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.q: queue.Queue[str] = queue.Queue(maxsize=1000)
        threading.Thread(target=self._run, daemon=True).start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(record.getMessage())
        except queue.Full:
            pass

    def _run(self) -> None:
        import httpx

        while True:
            line = self.q.get()
            try:
                httpx.post(self.url, json={"source": SERVICE_NAME, "lines": [line]}, timeout=5)
            except Exception as e:  # never crash the app because shipping failed
                print(f"[orders] log shipping failed: {e}", file=sys.stderr)


def configure() -> logging.Logger:
    logger = logging.getLogger(ERROR_LOGGER)
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        logger.addHandler(logging.StreamHandler(sys.stderr))
    url = os.getenv("AGENTOPS_INGEST_URL")
    if url and not any(isinstance(h, _HttpShipper) for h in logger.handlers):
        logger.addHandler(_HttpShipper(url))
    return logger

"""Ship unhandled exceptions from any Python service to AgentOps AI.

Single file, standard library only. Copy it into your service, then:

    import logging, agentops_handler
    agentops_handler.install(
        url="https://agentops.example.com",   # AgentOps backend
        api_key="...",                        # one of the backend's API_KEYS
        project="orders-api",                 # project name registered in AgentOps
        service="orders-api",
    )

What it captures
- Any log record with exception info (logger.exception(...), log.error(..., exc_info=True)).
- Uncaught exceptions in the main thread and in other threads (sys.excepthook / threading.excepthook).

For web frameworks, log the exception in your error handler, e.g. FastAPI:

    @app.exception_handler(Exception)
    async def on_error(request, exc):
        logging.getLogger("app").error("unhandled", exc_info=exc,
                                       extra={"http_method": request.method, "http_path": request.url.path})
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

Events are queued and POSTed to /ingest/events from a background thread, so a slow or
unreachable AgentOps never blocks or crashes your service. Identical errors within
`dedupe_window_s` are sent once (AgentOps groups repeats anyway).
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import traceback
import urllib.request

__all__ = ["AgentOpsHandler", "install"]


class AgentOpsHandler(logging.Handler):
    def __init__(self, url: str, api_key: str, project: str, service: str = "", *,
                 batch_size: int = 20, flush_interval_s: float = 2.0, dedupe_window_s: float = 30.0,
                 timeout_s: float = 5.0, max_queue: int = 1000):
        super().__init__(level=logging.ERROR)
        self.endpoint = url.rstrip("/") + "/ingest/events"
        self.api_key = api_key
        self.project = project
        self.service = service
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.dedupe_window_s = dedupe_window_s
        self.timeout_s = timeout_s
        self._q: queue.Queue[dict] = queue.Queue(maxsize=max_queue)
        self._recent: dict[str, float] = {}
        self._thread = threading.Thread(target=self._loop, name="agentops-shipper", daemon=True)
        self._thread.start()

    # -- capture --------------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        if not record.exc_info or record.exc_info[0] is None:
            return
        try:
            tb = "".join(traceback.format_exception(*record.exc_info))
            self.submit(tb, method=getattr(record, "http_method", ""), path=getattr(record, "http_path", ""),
                        message=record.getMessage())
        except Exception:
            self.handleError(record)

    def submit(self, tb: str, method: str = "", path: str = "", message: str = "") -> None:
        key = tb.strip().splitlines()[-1] + "|" + "".join(
            ln for ln in tb.splitlines() if ln.strip().startswith('File "'))[-500:]
        now = time.monotonic()
        if now - self._recent.get(key, -1e9) < self.dedupe_window_s:
            return
        self._recent[key] = now
        if len(self._recent) > 500:
            self._recent = {k: v for k, v in self._recent.items() if now - v < self.dedupe_window_s}
        event = {"traceback": tb, "method": method, "path": path, "service": self.service, "message": message[:500]}
        try:
            self._q.put_nowait(event)
        except queue.Full:
            pass  # drop rather than block the application

    # -- shipping -------------------------------------------------------
    def _loop(self) -> None:
        while True:
            batch = [self._q.get()]
            deadline = time.monotonic() + self.flush_interval_s
            while len(batch) < self.batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._q.get(timeout=remaining))
                except queue.Empty:
                    break
            self._send(batch)

    def _send(self, batch: list[dict]) -> None:
        body = json.dumps({"project": self.project, "source": self.service or "python-handler", "events": batch}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-API-Key": self.api_key})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    r.read()
                return
            except Exception as e:  # network or HTTP error: retry with backoff, then give up quietly
                if attempt == 2:
                    print(f"[agentops] could not ship {len(batch)} event(s): {e}", file=sys.stderr)
                time.sleep(2 ** attempt)

    def flush_now(self, timeout_s: float = 5.0) -> None:
        """Best effort: wait until the queue is drained (useful in short-lived scripts)."""
        end = time.monotonic() + timeout_s
        while not self._q.empty() and time.monotonic() < end:
            time.sleep(0.05)


def install(url: str, api_key: str, project: str, service: str = "", logger: logging.Logger | None = None,
            hook_uncaught: bool = True, **kwargs) -> AgentOpsHandler:
    handler = AgentOpsHandler(url, api_key, project, service, **kwargs)
    (logger or logging.getLogger()).addHandler(handler)
    if hook_uncaught:
        prev_sys, prev_thread = sys.excepthook, threading.excepthook

        def _sys_hook(t, v, tb):
            handler.submit("".join(traceback.format_exception(t, v, tb)))
            handler.flush_now(2)
            prev_sys(t, v, tb)

        def _thread_hook(args):
            if args.exc_type is not SystemExit:
                handler.submit("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
            prev_thread(args)

        sys.excepthook = _sys_hook
        threading.excepthook = _thread_hook
    return handler

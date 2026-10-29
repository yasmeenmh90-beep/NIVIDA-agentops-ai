"""Cooperative cancellation for running pipelines.

A cancel request is recorded here; the pipeline checks it between stages and before every
agent tool call, so a run stops within one model round instead of finishing its work.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_requested: dict[int, str] = {}  # incident id -> who asked


class Cancelled(Exception):
    def __init__(self, by: str):
        super().__init__(f"cancelled by {by}")
        self.by = by


def request(incident_id: int, by: str) -> None:
    with _lock:
        _requested[incident_id] = by


def check(incident_id: int) -> None:
    by = _requested.get(incident_id)
    if by is not None:
        raise Cancelled(by)


def clear(incident_id: int) -> None:
    with _lock:
        _requested.pop(incident_id, None)

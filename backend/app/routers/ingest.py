import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import orchestrator
from ..agents import detector
from ..config import get_settings
from ..schemas import EventsIn, IngestIn
from .projects import get_project_by_ref

router = APIRouter(prefix="/ingest", tags=["ingest"])


def ingest_and_dispatch(project_ref: str, lines: list[str], source: str) -> dict:
    p = get_project_by_ref(project_ref)
    if p.setup_status != "ready":
        # Frames can't be mapped to files without a checkout.
        raise HTTPException(409, f"project '{p.name}' is not ready (setup_status={p.setup_status})")
    result = detector.ingest(p.id, lines, source)
    if get_settings().auto_investigate:
        for incident_id in result["new"]:
            orchestrator.submit(incident_id)
    return {"project": p.name, **result}


@router.post("/logs")
def ingest_logs(body: IngestIn):
    """Raw log lines: JSON lines carrying a traceback, or plain-text Python tracebacks."""
    return ingest_and_dispatch(body.project, body.lines, body.source)


@router.post("/events")
def ingest_events(body: EventsIn):
    """Structured error events (what the bundled Python logging handler sends)."""
    lines = [json.dumps(e.model_dump()) for e in body.events]
    return ingest_and_dispatch(body.project, lines, body.source)


@router.post("/file")
async def ingest_file(project: str = Form(...), file: UploadFile = File(...)):
    # Read one byte beyond the cap so oversized input is rejected, never silently truncated.
    try:
        data = await file.read(5_000_001)
        if len(data) > 5_000_000:
            raise HTTPException(413, "log file exceeds the 5 MB limit")
        raw = data.decode("utf-8", errors="replace")
        if not raw.strip():
            raise HTTPException(422, "log file is empty")
        return await asyncio.to_thread(ingest_and_dispatch, project, raw.splitlines(), file.filename or "upload")
    finally:
        await file.close()

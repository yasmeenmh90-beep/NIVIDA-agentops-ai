from fastapi import APIRouter, File, UploadFile

from .. import orchestrator
from ..agents import detector
from ..config import get_settings
from ..schemas import IngestIn

router = APIRouter(prefix="/ingest", tags=["ingest"])


def ingest_and_dispatch(lines: list[str], source: str) -> dict:
    result = detector.ingest(lines, source)
    if get_settings().auto_investigate:
        for incident_id in result["new"]:
            orchestrator.submit(incident_id)
    return result


@router.post("/logs")
def ingest_logs(body: IngestIn):
    return ingest_and_dispatch(body.lines, body.source)


@router.post("/file")
async def ingest_file(file: UploadFile = File(...)):
    raw = (await file.read())[:5_000_000].decode("utf-8", errors="replace")
    return ingest_and_dispatch(raw.splitlines(), file.filename or "upload")

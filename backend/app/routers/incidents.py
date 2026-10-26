import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from .. import orchestrator
from ..db import SessionLocal
from ..models import AgentStep, AuditEvent, Decision, Incident, Patch
from ..schemas import ApproveIn, IncidentDetail, IncidentOut, RejectIn
from ..tools.github import GitHubError

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _detail(incident_id: int) -> IncidentDetail:
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            raise HTTPException(404, "incident not found")
        d = IncidentDetail.model_validate(inc)
        d.running = orchestrator.is_running(incident_id)
        d.steps = db.scalars(select(AgentStep).where(AgentStep.incident_id == incident_id).order_by(AgentStep.id)).all()
        d.patches = db.scalars(select(Patch).where(Patch.incident_id == incident_id).order_by(Patch.attempt)).all()
        d.decisions = db.scalars(select(Decision).where(Decision.incident_id == incident_id).order_by(Decision.id)).all()
        d.audit = db.scalars(select(AuditEvent).where(AuditEvent.incident_id == incident_id).order_by(AuditEvent.id)).all()
        return d


def _list() -> list[IncidentOut]:
    with SessionLocal() as db:
        rows = db.scalars(select(Incident).order_by(Incident.id.desc()).limit(200)).all()
        return [IncidentOut.model_validate(r) for r in rows]


@router.get("", response_model=list[IncidentOut])
def list_incidents():
    return _list()


def _fingerprint(incident_id: int | None) -> tuple:
    with SessionLocal() as db:
        if incident_id is None:
            return tuple(db.execute(select(func.count(Incident.id), func.max(Incident.updated_at),
                                           func.sum(Incident.occurrences))).one())
        return (
            db.scalar(select(Incident.updated_at).where(Incident.id == incident_id)),
            db.scalar(select(Incident.occurrences).where(Incident.id == incident_id)),
            db.scalar(select(func.count(AgentStep.id)).where(AgentStep.incident_id == incident_id)),
            db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.incident_id == incident_id)),
        )


async def _sse(request: Request, incident_id: int | None):
    last = None
    ticks = 0
    while True:
        if await request.is_disconnected():
            break
        fp = await asyncio.to_thread(_fingerprint, incident_id)
        if fp != last:
            last = fp
            data = await asyncio.to_thread(_list) if incident_id is None else await asyncio.to_thread(_detail, incident_id)
            payload = [x.model_dump(mode="json") for x in data] if incident_id is None else data.model_dump(mode="json")
            yield f"event: update\ndata: {json.dumps(payload)}\n\n"
        elif ticks % 15 == 0:
            yield ": keep-alive\n\n"
        ticks += 1
        await asyncio.sleep(1.0)


@router.get("/stream")
async def stream_all(request: Request):
    return StreamingResponse(_sse(request, None), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int):
    return _detail(incident_id)


@router.get("/{incident_id}/stream")
async def stream_incident(incident_id: int, request: Request):
    _detail(incident_id)  # 404 early
    return StreamingResponse(_sse(request, incident_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{incident_id}/approve")
def approve(incident_id: int, body: ApproveIn):
    try:
        d = orchestrator.approve(incident_id, body.approver, body.note)
    except orchestrator.TransitionError as e:
        raise HTTPException(409, str(e))
    except GitHubError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "pr_url": d.pr_url}


@router.post("/{incident_id}/reject")
def reject(incident_id: int, body: RejectIn):
    try:
        orchestrator.reject(incident_id, body.approver, body.reason)
    except orchestrator.TransitionError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/{incident_id}/run")
def run(incident_id: int):
    try:
        started = orchestrator.rerun(incident_id)
    except orchestrator.TransitionError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "started": started}

"""Cross-incident activity feed and notification checks."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import notify
from ..db import SessionLocal
from ..models import AuditEvent, Incident, Project
from .projects import get_project_by_ref

router = APIRouter(tags=["activity"])


def _iso(dt) -> str:
    from ..schemas import _utc
    return _utc(dt).isoformat().replace("+00:00", "Z")


@router.get("/activity")
def activity(limit: int = 100, project: str | None = None, include_tools: bool = False, before_id: int | None = None):
    """Newest-first audit events across all incidents (status changes, decisions, PRs, revisions, tool calls)."""
    limit = max(1, min(limit, 500))
    q = (select(AuditEvent, Incident.title, Incident.status, Project.name)
         .join(Incident, Incident.id == AuditEvent.incident_id)
         .join(Project, Project.id == Incident.project_id)
         .order_by(AuditEvent.id.desc()).limit(limit))
    if project:
        q = q.where(Incident.project_id == get_project_by_ref(project).id)
    if not include_tools:
        q = q.where(~AuditEvent.action.startswith("tool:"))
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    with SessionLocal() as db:
        rows = db.execute(q).all()
    return [{"id": a.id, "incident_id": a.incident_id, "incident_title": title, "incident_status": status,
             "project": pname, "actor": a.actor, "action": a.action, "detail": a.detail or {}, "ts": _iso(a.ts)}
            for a, title, status, pname in rows]


@router.post("/notifications/test")
def test_notification():
    """Send a test message to every configured channel and report what happened per channel."""
    ch = notify.channels()
    if not any(ch.values()):
        raise HTTPException(409, "no notification channel is configured (set SLACK_WEBHOOK_URL, NOTIFY_WEBHOOK_URL or SMTP_* + NOTIFY_EMAIL_TO)")
    return {"channels": ch, "results": notify.send_test()}

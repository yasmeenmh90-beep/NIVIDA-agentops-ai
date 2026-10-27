"""Operational metrics: how much AgentOps fixes, how fast, and at what token cost."""

from __future__ import annotations

import statistics
from collections import Counter

from fastapi import APIRouter
from sqlalchemy import select

from ..db import SessionLocal
from ..models import AuditEvent, Decision, Incident, Patch, Status
from .projects import get_project_by_ref

router = APIRouter(tags=["metrics"])


def _minutes(a, b) -> float | None:
    if not a or not b:
        return None
    if a.tzinfo is None and b.tzinfo is not None:
        b = b.replace(tzinfo=None)
    if b.tzinfo is None and a.tzinfo is not None:
        a = a.replace(tzinfo=None)
    return round((b - a).total_seconds() / 60, 2)


def _stats(values: list[float]) -> dict:
    v = [x for x in values if x is not None]
    if not v:
        return {"count": 0, "median": None, "mean": None, "p90": None}
    v.sort()
    return {"count": len(v), "median": round(statistics.median(v), 2), "mean": round(statistics.mean(v), 2),
            "p90": round(v[min(len(v) - 1, int(0.9 * len(v)))], 2)}


@router.get("/metrics")
def metrics(project: str | None = None):
    with SessionLocal() as db:
        q = select(Incident)
        if project:
            q = q.where(Incident.project_id == get_project_by_ref(project).id)
        incs = db.scalars(q).all()
        ids = [i.id for i in incs]
        patches = db.scalars(select(Patch).where(Patch.incident_id.in_(ids))).all() if ids else []
        decisions = db.scalars(select(Decision).where(Decision.incident_id.in_(ids))).all() if ids else []
        status_events = db.scalars(select(AuditEvent).where(AuditEvent.incident_id.in_(ids),
                                                            AuditEvent.action == "status_change")).all() if ids else []

    by_status = Counter(i.status for i in incs)
    first_verified: dict[int, float] = {}
    for e in status_events:  # time from detection to first verified fix
        if (e.detail or {}).get("to") == Status.AWAITING_APPROVAL and e.incident_id not in first_verified:
            inc = next(i for i in incs if i.id == e.incident_id)
            first_verified[e.incident_id] = _minutes(inc.first_seen, e.ts)
    pr_times = [_minutes(next(i for i in incs if i.id == d.incident_id).first_seen, d.decided_at)
                for d in decisions if d.decision == "approve" and d.pr_url]
    resolve_times = [_minutes(i.first_seen, i.resolved_at) for i in incs if i.resolved_at]

    initial = [p for p in patches if (p.kind or "initial") == "initial"]
    attempts_per_incident: dict[int, list[Patch]] = {}
    for p in initial:
        attempts_per_incident.setdefault(p.incident_id, []).append(p)
    verified_incidents = {i for i, ps in attempts_per_incident.items() if any(p.tests_passed for p in ps)}
    first_try = {i for i, ps in attempts_per_incident.items()
                 if any(p.tests_passed and p.attempt == 1 for p in ps)}
    attempts_needed = [min(p.attempt for p in ps if p.tests_passed) for i, ps in attempts_per_incident.items()
                       if i in verified_incidents]
    decided = [d for d in decisions]
    approved = [d for d in decided if d.decision == "approve"]
    revisions = [p for p in patches if p.kind == "revision"]
    tokens = [i.tokens_used for i in incs if i.tokens_used]
    reached_fix_stage = len(attempts_per_incident)

    def rate(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    return {
        "project": project,
        "incidents": {
            "total": len(incs),
            "by_status": dict(by_status),
            "by_severity": dict(Counter(i.severity for i in incs)),
            "regressions": sum(1 for i in incs if i.regression_of),
            "total_occurrences": sum(i.occurrences for i in incs),
        },
        "fix_quality": {
            "verified_fix_rate": rate(len(verified_incidents), reached_fix_stage),
            "first_attempt_success_rate": rate(len(first_try), reached_fix_stage),
            "avg_attempts_to_verified": round(statistics.mean(attempts_needed), 2) if attempts_needed else None,
            "needs_human_now": by_status.get(Status.NEEDS_HUMAN, 0),
            "ever_needed_human": len({e.incident_id for e in status_events
                                      if (e.detail or {}).get("to") == Status.NEEDS_HUMAN}),
            "approval_rate": rate(len(approved), len(decided)),
            "merged": by_status.get(Status.RESOLVED, 0),
            "revisions": {"total": len({p.incident_id * 1000 + p.attempt // 100 for p in revisions}),
                          "verified": len({p.incident_id * 1000 + p.attempt // 100 for p in revisions if p.tests_passed})},
        },
        "timing_minutes": {
            "detect_to_verified_fix": _stats(list(first_verified.values())),
            "detect_to_pr": _stats(pr_times),
            "detect_to_merged_mttr": _stats(resolve_times),
        },
        "llm_tokens": {
            "total": sum(tokens),
            "per_incident": _stats([float(t) for t in tokens]),
        },
    }

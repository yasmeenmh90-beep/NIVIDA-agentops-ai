"""Incident memory: past incidents that were fixed (PR opened or merged) in the same project.

The Investigator gets the most similar past incidents (root cause + diff) as context, and the
Detector flags an error whose signature was already fixed and merged as a regression.
Similarity is deliberately simple and explainable: same location / exception plus word overlap.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from .db import SessionLocal
from .models import FIXED_STATUSES, Incident, Patch, Status

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _words(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text or "")}


def score(a: Incident, b: Incident) -> float:
    la, lb = a.location or {}, b.location or {}
    s = 0.0
    if la.get("file") and la.get("file") == lb.get("file"):
        s += 2.0
        if la.get("function") == lb.get("function"):
            s += 2.0
    if a.exception_type == b.exception_type:
        s += 1.5
    wa, wb = _words(a.message + " " + a.title), _words(b.message + " " + b.title)
    if wa and wb:
        s += 3.0 * len(wa & wb) / len(wa | wb)
    rca, rcb = (a.root_cause or {}).get("file"), (b.root_cause or {}).get("file")
    if rca and rca == rcb:
        s += 1.0
    return round(s, 3)


def similar(incident_id: int, limit: int = 3, min_score: float = 2.5) -> list[dict]:
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        past = db.scalars(select(Incident).where(
            Incident.project_id == inc.project_id, Incident.id != incident_id,
            Incident.status.in_([s.value for s in FIXED_STATUSES]))).all()
        scored = sorted(((score(inc, p), p) for p in past), key=lambda x: -x[0])
        out = []
        for sc, p in scored[:limit]:
            if sc < min_score:
                break
            patch = db.scalars(select(Patch).where(Patch.incident_id == p.id, Patch.tests_passed.is_(True))
                               .order_by(Patch.id.desc())).first()
            rc = p.root_cause or {}
            out.append({
                "incident_id": p.id, "score": sc, "status": p.status, "title": p.title,
                "root_cause_file": rc.get("file"), "root_cause_line": rc.get("line"),
                "root_cause": (rc.get("root_cause") or "")[:600],
                "fix_explanation": (patch.explanation if patch else "")[:400],
                "diff": (patch.diff if patch else "")[:1500],
                "pr_url": p.pr_url,
            })
        return out


def as_prompt(items: list[dict]) -> str:
    if not items:
        return ""
    parts = ["Similar incidents fixed earlier in this project (use as hints; verify against the current code):"]
    for it in items:
        parts.append(
            f"- #{it['incident_id']} ({it['status']}, similarity {it['score']}): {it['title']}\n"
            f"  Root cause at {it['root_cause_file']}:{it['root_cause_line']}: {it['root_cause']}\n"
            f"  Fix: {it['fix_explanation']}\n  Diff:\n{it['diff']}"
        )
    return "<untrusted_history>\n" + "\n".join(parts) + "\n</untrusted_history>"


def find_regression_source(db, project_id: int, signature: str) -> Incident | None:
    """The most recent MERGED fix for the same error signature, if any."""
    return db.scalars(select(Incident).where(
        Incident.project_id == project_id, Incident.error_signature == signature,
        Incident.status == Status.RESOLVED).order_by(Incident.id.desc())).first()

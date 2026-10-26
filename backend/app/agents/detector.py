"""Detector: rule-based parsing of log lines into de-duplicated incidents.

Accepts JSON log lines that carry a Python traceback (keys: traceback, exc_info,
stack_trace, stacktrace, exception) and raw Python tracebacks in plain text. No LLM is needed to detect; the orchestrator later
asks the small triage model for a one-line summary.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field

from sqlalchemy import select

from ..db import SessionLocal
from ..context import use_project
from ..models import OPEN_STATUSES, AgentStep, AuditEvent, Incident, Status, utcnow
from ..projects import load_context
from ..tools.repo import iter_files, repo_root

FRAME_RX = re.compile(r'File "(?P<file>.+?)", line (?P<line>\d+), in (?P<func>\S+)')
EXC_RX = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)|KeyError|StopIteration)(?::\s?(?P<msg>.*))?$")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
_ingest_lock = threading.Lock()  # serialise de-duplication across concurrent log batches


@dataclass
class Frame:
    file: str
    line: int
    func: str
    repo_file: str | None = None


@dataclass
class ErrorEvent:
    exc_type: str
    message: str
    traceback: str
    frames: list[Frame] = field(default_factory=list)
    method: str = ""
    path: str = ""
    service: str = ""

    @property
    def culprit(self) -> Frame | None:
        """Innermost frame inside the target repo (excluding tests)."""
        for f in reversed(self.frames):
            if f.repo_file and not f.repo_file.startswith("tests/"):
                return f
        return None

    @property
    def signature(self) -> str:
        c = self.culprit
        where = f"{c.repo_file}:{c.func}" if c else (self.frames[-1].func if self.frames else "unknown")
        return f"{self.exc_type}|{where}"


def _map_to_repo(abs_path: str, files: list[str]) -> str | None:
    p = abs_path.replace("\\", "/")
    root = str(repo_root()).replace("\\", "/")
    if p.startswith(root + "/"):
        rel = p[len(root) + 1:]
        return rel if rel in files else None
    for rel in files:  # traceback produced on another machine/container: match by suffix
        if p.endswith("/" + rel):
            return rel
    return None


def parse_traceback(tb: str, files: list[str]) -> tuple[list[Frame], str, str]:
    frames = []
    for m in FRAME_RX.finditer(tb):
        frames.append(Frame(m["file"], int(m["line"]), m["func"], _map_to_repo(m["file"], files)))
    exc_type, msg = "UnknownError", ""
    for line in reversed([ln.strip() for ln in tb.strip().splitlines() if ln.strip()]):
        m = EXC_RX.match(line)
        if m:
            exc_type, msg = m["type"].split(".")[-1], (m["msg"] or "").strip()
            break
    return frames, exc_type, msg


TB_KEYS = ("traceback", "exc_info", "stack_trace", "stacktrace", "exception", "error.stack")


def _first(obj: dict, *keys: str) -> str:
    for k in keys:
        v = obj.get(k)
        if v:
            return v if isinstance(v, str) else str(v)
    return ""


def _json_traceback(obj: dict) -> str | None:
    for k in TB_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and "Traceback (most recent call last)" in v:
            return v
    return None


def parse_lines(lines: list[str]) -> list[ErrorEvent]:
    files = iter_files()
    events: list[ErrorEvent] = []
    buf: list[str] | None = None

    def flush():
        nonlocal buf
        if buf:
            tb = "\n".join(buf)
            frames, t, msg = parse_traceback(tb, files)
            if frames:
                events.append(ErrorEvent(t, msg, tb, frames))
        buf = None

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                obj = None
            tb = _json_traceback(obj) if isinstance(obj, dict) else None
            if tb:
                flush()
                frames, t, msg = parse_traceback(tb, files)
                if frames:
                    events.append(ErrorEvent(
                        t, msg, tb, frames,
                        method=str(_first(obj, "method", "http_method", "request_method")).upper(),
                        path=str(_first(obj, "path", "route", "url", "request_path")),
                        service=str(_first(obj, "service", "service_name", "app", "logger")),
                    ))
                continue
        if stripped.startswith("Traceback (most recent call last)"):
            flush()
            buf = [line]
            continue
        if buf is not None:
            buf.append(line)
            if EXC_RX.match(stripped) and not line.startswith((" ", "\t")):
                flush()
    flush()
    return events


def base_severity(ev: ErrorEvent) -> str:
    if ev.exc_type in {"MemoryError", "RecursionError", "SystemExit"}:
        return "critical"
    if ev.method in WRITE_METHODS:
        return "high"  # a write path (e.g. checkout) is failing
    if ev.method:
        return "medium"
    return "medium"


def escalate(sev: str, occurrences: int) -> str:
    i = SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else 1
    if occurrences >= 25:
        i += 2
    elif occurrences >= 5:
        i += 1
    return SEVERITY_ORDER[min(i, len(SEVERITY_ORDER) - 1)]


def make_title(ev: ErrorEvent) -> str:
    c = ev.culprit
    where = f" in {c.func}()" if c else ""
    route = f" on {ev.method} {ev.path}" if ev.method else ""
    msg = f": {ev.message}" if ev.message and len(ev.message) < 60 else ""
    return f"{ev.exc_type}{msg}{where}{route}"[:300]


def ingest(project_id: int, lines: list[str], source: str = "") -> dict:
    """Parse lines for one project, create or update incidents. Returns {new: [ids], updated: [ids], events: n}."""
    with use_project(load_context(project_id)):
        events = parse_lines(lines)
    new_ids, updated_ids = [], []
    with _ingest_lock, SessionLocal() as db:
        for ev in events:
            existing = db.scalars(
                select(Incident)
                .where(Incident.project_id == project_id, Incident.error_signature == ev.signature,
                       Incident.status.in_([s.value for s in OPEN_STATUSES]))
                .order_by(Incident.id.desc())
            ).first()
            if existing:
                existing.occurrences += 1
                existing.last_seen = utcnow()
                existing.severity = escalate(base_severity(ev), existing.occurrences)
                updated_ids.append(existing.id)
                continue
            c = ev.culprit
            inc = Incident(
                project_id=project_id,
                title=make_title(ev),
                severity=base_severity(ev),
                status=Status.DETECTED,
                error_signature=ev.signature,
                exception_type=ev.exc_type,
                message=ev.message,
                endpoint=f"{ev.method} {ev.path}".strip(),
                service=ev.service or source,
                traceback=ev.traceback[-8000:],
                location={"file": c.repo_file, "line": c.line, "function": c.func} if c else {},
            )
            db.add(inc)
            db.flush()
            db.add(AgentStep(
                incident_id=inc.id, agent="detector", status="done",
                input_summary=f"{len(lines)} log line(s) from {source or 'unknown source'}",
                output={
                    "signature": ev.signature, "severity": inc.severity, "exception": ev.exc_type,
                    "message": ev.message, "endpoint": inc.endpoint, "location": inc.location,
                    "repo_frames": [f"{f.repo_file}:{f.line} in {f.func}" for f in ev.frames if f.repo_file],
                    "method": "rule-based traceback parser",
                },
                finished_at=utcnow(),
            ))
            db.add(AuditEvent(incident_id=inc.id, actor="detector", action="incident_opened",
                              detail={"signature": ev.signature, "source": source}))
            new_ids.append(inc.id)
        db.commit()
    return {"events": len(events), "new": new_ids, "updated": sorted(set(updated_ids))}


"""Notifications: Slack incoming webhook, a generic JSON webhook, and email (SMTP).

All channels are optional and configured by env vars. Sending happens on a background thread
and failures are only logged, so a broken notification channel never affects the pipeline.
"""

from __future__ import annotations

import logging
import smtplib
import threading
from email.message import EmailMessage

import httpx

from .config import get_settings
from .db import SessionLocal
from .models import Incident, Project

log = logging.getLogger("agentops.notify")
SEVERITY_ORDER = ["low", "medium", "high", "critical"]

# event -> (emoji, headline). Only these events are sent.
EVENTS = {
    "opened": ("🚨", "New incident"),
    "awaiting_approval": ("✅", "Verified fix ready for review"),
    "needs_human": ("🧑‍🔧", "No verified fix found, needs a human"),
    "failed": ("⚠️", "Pipeline error"),
    "pr_created": ("🔀", "Pull request opened"),
    "revised": ("🔁", "PR updated after review feedback"),
    "revision_failed": ("⚠️", "Could not revise PR after review feedback"),
    "resolved": ("🎉", "Fix merged, incident resolved"),
}

_transport: httpx.BaseTransport | None = None  # tests inject a MockTransport
_sent: list[dict] = []  # last messages, for tests and /status


def set_transport(t: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = t


def channels() -> dict:
    s = get_settings()
    return {
        "slack": bool(s.slack_webhook_url),
        "webhook": bool(s.notify_webhook_url),
        "email": bool(s.smtp_host and s.notify_email_to),
    }


def _payload(incident_id: int, event: str, extra: str) -> dict | None:
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            return None
        project = db.get(Project, inc.project_id)
        emoji, headline = EVENTS[event]
        base = get_settings().public_base_url.rstrip("/")
        link = f"{base}/incidents/{inc.id}" if base else ""
        rc = inc.root_cause or {}
        lines = [
            f"{emoji} *{headline}* — {project.name if project else '?'} #{inc.id}",
            f"*{inc.title}*",
            f"Severity: {inc.severity} · Occurrences: {inc.occurrences} · Status: {inc.status}",
        ]
        if inc.regression_of:
            lines.append(f"Regression of incident #{inc.regression_of}")
        if rc.get("root_cause") and event != "opened":
            lines.append(f"Root cause ({rc.get('file')}:{rc.get('line')}): {rc['root_cause'][:300]}")
        if inc.pr_url and event in ("pr_created", "revised", "revision_failed", "resolved"):
            lines.append(f"PR: {inc.pr_url}")
        if extra:
            lines.append(extra[:500])
        if link:
            lines.append(link)
        return {
            "event": event, "incident_id": inc.id, "project": project.name if project else None,
            "title": inc.title, "severity": inc.severity, "status": inc.status, "pr_url": inc.pr_url,
            "link": link, "text": "\n".join(lines),
        }


def _min_severity_ok(severity: str) -> bool:
    s = get_settings()
    try:
        return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(s.notify_min_severity)
    except ValueError:
        return True


def _deliver(msg: dict) -> None:
    s = get_settings()
    _sent.append(msg)
    del _sent[:-50]
    with httpx.Client(timeout=10, transport=_transport) as http:
        if s.slack_webhook_url:
            try:
                http.post(s.slack_webhook_url, json={"text": msg["text"]}).raise_for_status()
            except Exception as e:
                log.warning("slack notification failed: %s", e)
        if s.notify_webhook_url:
            try:
                http.post(s.notify_webhook_url, json=msg).raise_for_status()
            except Exception as e:
                log.warning("webhook notification failed: %s", e)
    if s.smtp_host and s.notify_email_to:
        try:
            em = EmailMessage()
            em["Subject"] = f"[AgentOps] {EVENTS[msg['event']][1]}: {msg['title'][:120]}"
            em["From"] = s.smtp_from or s.smtp_user
            em["To"] = s.notify_email_to
            em.set_content(msg["text"].replace("*", ""))
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_starttls:
                    smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(em)
        except Exception as e:
            log.warning("email notification failed: %s", e)


def incident_event(incident_id: int, event: str, extra: str = "", sync: bool = False) -> None:
    if event not in EVENTS or not any(channels().values()):
        return
    try:
        msg = _payload(incident_id, event, extra)
    except Exception as e:
        log.warning("could not build notification: %s", e)
        return
    if msg is None or (event == "opened" and not _min_severity_ok(msg["severity"])):
        return
    if sync:
        _deliver(msg)
    else:
        threading.Thread(target=_deliver, args=(msg,), daemon=True, name="notify").start()

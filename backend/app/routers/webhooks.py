"""Inbound webhooks: GitHub (PR lifecycle + review feedback) and Sentry (error events).

These endpoints are authenticated by HMAC signatures, not API keys, because GitHub and
Sentry can't send custom headers.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from .. import orchestrator
from ..auth import extract_token, require_api_key
from ..config import get_settings
from .ingest import ingest_and_dispatch

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger("agentops.webhooks")
COMMAND = "/agentops"


def _payload(body: bytes) -> dict:
    try:
        value = json.loads(body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "invalid JSON payload")
    if not isinstance(value, dict):
        raise HTTPException(400, "webhook payload must be a JSON object")
    return value


def _valid_sig(secret: str, body: bytes, header: str, prefix: str = "") -> bool:
    if not secret or not header:
        return False
    expected = prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


# ---------------------------------------------------------------- GitHub

@router.post("/github")
async def github_webhook(request: Request, x_github_event: str = Header(""),
                         x_hub_signature_256: str = Header("")):
    s = get_settings()
    if not s.github_webhook_secret:
        raise HTTPException(503, "GITHUB_WEBHOOK_SECRET is not configured")
    body = await request.body()
    if not _valid_sig(s.github_webhook_secret, body, x_hub_signature_256, "sha256="):
        raise HTTPException(401, "invalid signature")
    payload = _payload(body)
    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    repo = (payload.get("repository") or {}).get("full_name", "")
    allowed = {a.strip().upper() for a in s.revision_author_associations.split(",") if a.strip()}

    if x_github_event == "pull_request" and payload.get("action") == "closed":
        pr = payload["pull_request"]
        inc = orchestrator.find_by_pr(repo, pr["number"])
        if not inc:
            return {"ok": True, "ignored": "not an AgentOps PR"}
        by = ((pr.get("merged_by") if pr.get("merged") else payload.get("sender")) or {}).get("login", "?")
        orchestrator.pr_closed(inc.id, bool(pr.get("merged")), by)
        return {"ok": True, "incident": inc.id, "merged": bool(pr.get("merged"))}

    if x_github_event == "pull_request_review" and payload.get("action") == "submitted":
        review = payload.get("review") or {}
        if (review.get("state") or "").lower() != "changes_requested":
            return {"ok": True, "ignored": f"review state {review.get('state')}"}
        return _revision(repo, payload["pull_request"]["number"], review.get("body") or "",
                         (review.get("user") or {}).get("login", "?"), review.get("author_association", ""),
                         allowed, "review")

    if x_github_event == "issue_comment" and payload.get("action") == "created":
        issue, comment = payload.get("issue") or {}, payload.get("comment") or {}
        text = (comment.get("body") or "").strip()
        if "pull_request" not in issue or not text.lower().startswith(COMMAND):
            return {"ok": True, "ignored": "not an /agentops command on a PR"}
        return _revision(repo, issue["number"], text[len(COMMAND):].strip(),
                         (comment.get("user") or {}).get("login", "?"), comment.get("author_association", ""),
                         allowed, "comment")

    return {"ok": True, "ignored": f"event {x_github_event}/{payload.get('action')}"}


def _revision(repo: str, pr_number: int, feedback: str, author: str, association: str, allowed: set[str],
              source: str) -> dict:
    inc = orchestrator.find_by_pr(repo, pr_number)
    if not inc:
        return {"ok": True, "ignored": "not an AgentOps PR"}
    if association.upper() not in allowed:
        # Only people with write access to the repo may steer the agent.
        log.info("ignoring %s from %s (%s) on PR #%s", source, author, association, pr_number)
        return {"ok": True, "ignored": f"author association {association} not allowed"}
    queued = orchestrator.request_revision(inc.id, feedback, author, source)
    return {"ok": True, "incident": inc.id, "revision_queued": queued}


# ---------------------------------------------------------------- Sentry

def sentry_event_to_lines(payload: dict) -> list[str]:
    """Convert a Sentry event payload (event_alert / error webhook) into a JSON log line with a
    Python-style traceback that the Detector understands."""
    data = payload.get("data") or {}
    event = data.get("event") or data.get("error") or payload.get("event") or {}
    values = ((event.get("exception") or {}).get("values")) or []
    if not values:
        return []
    out = ["Traceback (most recent call last):"]
    for exc in values:  # chained exceptions: oldest first
        frames = ((exc.get("stacktrace") or {}).get("frames")) or []
        for f in frames:
            path = f.get("abs_path") or f.get("filename") or "?"
            out.append(f'  File "{path}", line {f.get("lineno", 0)}, in {f.get("function") or "<module>"}')
            if f.get("context_line"):
                out.append(f"    {f['context_line'].strip()}")
    last = values[-1]
    out.append(f"{last.get('type', 'Exception')}: {last.get('value', '')}".rstrip(": "))
    req = event.get("request") or {}
    url = req.get("url") or ""
    path = "/" + url.split("://", 1)[-1].split("/", 1)[1] if "://" in url and "/" in url.split("://", 1)[-1] else url
    return [json.dumps({
        "traceback": "\n".join(out) + "\n",
        "method": req.get("method", ""),
        "path": path.split("?", 1)[0],
        "service": event.get("project_name") or event.get("logger") or "sentry",
    })]


@router.post("/sentry/{project}")
async def sentry_webhook(project: str, request: Request, sentry_hook_signature: str = Header(""),
                         sentry_hook_resource: str = Header("")):
    """Point a Sentry internal integration (alert rule action or error.created) at this URL.
    Authenticated with SENTRY_CLIENT_SECRET, or with an AgentOps API key for manual relays."""
    body = await request.body()
    s = get_settings()
    if not _valid_sig(s.sentry_client_secret, body, sentry_hook_signature):
        if not extract_token(request):
            raise HTTPException(401, "a valid Sentry signature or API credential is required")
        require_api_key(request)
    payload = _payload(body)
    lines = sentry_event_to_lines(payload)
    if not lines:
        return {"ok": True, "ignored": f"no exception stacktrace in '{sentry_hook_resource or 'payload'}'"}
    return await asyncio.to_thread(ingest_and_dispatch, project, lines, "sentry")

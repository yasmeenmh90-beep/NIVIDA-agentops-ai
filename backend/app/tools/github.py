"""Create a branch, commit the approved patch and open a pull request via the GitHub REST API.

Only called after a human approves. Never merges.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import httpx

from ..config import get_settings


class GitHubError(RuntimeError):
    pass


@dataclass
class PRResult:
    url: str
    number: int
    branch: str


_transport: httpx.BaseTransport | None = None


def set_transport(t: httpx.BaseTransport | None) -> None:
    """Tests inject an httpx.MockTransport here."""
    global _transport
    _transport = t


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        base_url=s.github_api_url,
        headers={
            "Authorization": f"Bearer {s.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
        transport=_transport,
    )


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        raise GitHubError(f"{what} failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def open_pull_request(*, incident_id: int, title: str, body: str, files: dict[str, str], slug: str) -> PRResult:
    s = get_settings()
    if not s.github_configured:
        raise GitHubError("GitHub is not configured (set GITHUB_TOKEN and GITHUB_REPO)")
    repo = s.github_repo
    prefix = s.github_path_prefix.strip("/")
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")[:40] or "fix"
    with _client() as gh:
        base = _check(gh.get(f"/repos/{repo}/git/ref/heads/{s.github_base_branch}"), "read base branch")
        base_sha = base["object"]["sha"]
        branch = f"agentops/incident-{incident_id}-{slug}"
        r = gh.post(f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        if r.status_code == 422:  # branch exists (re-approval) -> add a suffix
            branch = f"{branch}-{base_sha[:7]}"
            r = gh.post(f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        _check(r, "create branch")

        for rel, content in files.items():
            path = f"{prefix}/{rel}" if prefix else rel
            existing = gh.get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
            payload = {
                "message": f"fix: {title} (AgentOps incident #{incident_id})",
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch,
            }
            if existing.status_code == 200:
                payload["sha"] = existing.json()["sha"]
            _check(gh.put(f"/repos/{repo}/contents/{path}", json=payload), f"commit {path}")

        pr = _check(
            gh.post(f"/repos/{repo}/pulls", json={
                "title": f"[AgentOps] {title}",
                "head": branch,
                "base": s.github_base_branch,
                "body": body,
                "maintainer_can_modify": True,
            }),
            "open pull request",
        )
    return PRResult(url=pr["html_url"], number=pr["number"], branch=branch)

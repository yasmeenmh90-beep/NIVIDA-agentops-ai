"""Create a branch, commit the approved patch and open a pull request via the GitHub REST API.

Only called after a human approves. Never merges.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import httpx

from ..config import get_settings
from ..context import current


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


def _client(token: str) -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        base_url=s.github_api_url,
        headers={
            "Authorization": f"Bearer {token}",
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


def open_pull_request(*, incident_id: int, title: str, body: str, files: dict[str, str], slug: str,
                      base_sha: str = "") -> PRResult:
    """Branch from `base_sha` (the commit the agents analysed) so the patch applies to the code it was tested on."""
    ctx = current()
    if not ctx.github_repo:
        raise GitHubError("this project has no github_repo, so PRs are disabled")
    if not ctx.github_token:
        raise GitHubError("no GitHub token found in the env var configured for this project")
    repo = ctx.github_repo
    prefix = ctx.app_path.strip("/")
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")[:40] or "fix"
    with _client(ctx.github_token) as gh:
        if not base_sha:
            base = _check(gh.get(f"/repos/{repo}/git/ref/heads/{ctx.default_branch}"), "read base branch")
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
                "base": ctx.default_branch,
                "body": body,
                "maintainer_can_modify": True,
            }),
            "open pull request",
        )
    return PRResult(url=pr["html_url"], number=pr["number"], branch=branch)

"""Incident state machine.

DETECTED -> INVESTIGATING -> FIXING -> TESTING -> AWAITING_APPROVAL -> PR_CREATED | REJECTED
                                 ^________|  (tests fail: retry, max N attempts) -> NEEDS_HUMAN
Any pipeline error -> FAILED (can be re-run).

Each incident runs in a worker thread so the API stays responsive. The approval gate is
not an LLM: the pipeline stops at AWAITING_APPROVAL and only a human request moves it on.
"""

from __future__ import annotations

import logging
import threading

import httpx
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from .agents import fixer, investigator, prompts
from .agents.recorder import StepRecorder
from .agents.tester import verify_patch
from . import projects
from .config import get_settings
from .context import use_project
from .db import SessionLocal
from .llm import simple_completion
from .models import RUNNING_STATUSES, AgentStep, AuditEvent, Decision, Incident, Patch, Project, Status, utcnow
from .tools import github
from .tools.sandbox import TestRun, run_tests

log = logging.getLogger("agentops.orchestrator")
_executor = ThreadPoolExecutor(max_workers=get_settings().worker_threads, thread_name_prefix="incident")
_running: set[int] = set()
_approving: set[int] = set()
_lock = threading.Lock()
_baseline_cache: dict[tuple[int, str], TestRun] = {}  # (project_id, commit sha) -> unpatched test run


class TransitionError(ValueError):
    pass


def set_status(incident_id: int, status: Status, reason: str = "", actor: str = "orchestrator") -> None:
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        prev = inc.status
        inc.status = status
        inc.status_reason = reason
        inc.updated_at = utcnow()
        db.add(AuditEvent(incident_id=incident_id, actor=actor, action="status_change",
                          detail={"from": prev, "to": status, "reason": reason}))
        db.commit()


def _load(incident_id: int) -> Incident:
    with SessionLocal() as db:
        return db.get(Incident, incident_id)


def _baseline(project_id: int, sha: str) -> TestRun:
    """Unpatched test results for this commit (cached: the same commit always gives the same baseline)."""
    key = (project_id, sha)
    if key not in _baseline_cache:
        _baseline_cache[key] = run_tests()
    return _baseline_cache[key]


def clear_baseline_cache() -> None:
    _baseline_cache.clear()


def submit(incident_id: int) -> bool:
    """Queue an incident for processing. Returns False if it is already running."""
    with _lock:
        if incident_id in _running:
            return False
        _running.add(incident_id)
    _executor.submit(_run_safe, incident_id)
    return True


def is_running(incident_id: int) -> bool:
    return incident_id in _running


def _run_safe(incident_id: int) -> None:
    try:
        _run_for_project(incident_id)
    except Exception as e:  # never let a worker die silently
        log.exception("incident %s failed", incident_id)
        set_status(incident_id, Status.FAILED, f"{type(e).__name__}: {e}")
    finally:
        with _lock:
            _running.discard(incident_id)


def _triage_summary(inc: Incident) -> None:
    s = get_settings()
    rec = StepRecorder(inc.id, "detector", "Summarise incident for on-call", s.model_triage)
    try:
        text, pt, ct = simple_completion(
            prompts.TRIAGE,
            f"Title: {inc.title}\nEndpoint: {inc.endpoint}\n<untrusted_logs>\n{inc.traceback[-2500:]}\n</untrusted_logs>",
            s.model_triage,
        )
    except Exception as e:  # summary is optional; don't fail the incident over it
        rec.finish({"error": str(e), "note": "summary skipped"}, status="error")
        return
    text = text.strip().splitlines()[0][:400] if text.strip() else ""
    with SessionLocal() as db:
        db.get(Incident, inc.id).summary = text
        db.commit()
    rec.finish({"summary": text}, pt, ct)


def _run_for_project(incident_id: int) -> None:
    """Serialise per project, refresh AgentOps' clone, bind the project context, run the pipeline."""
    inc = _load(incident_id)
    with SessionLocal() as db:
        project = db.get(Project, inc.project_id)
    if project.setup_status != "ready":
        set_status(incident_id, Status.FAILED, f"project '{project.name}' is not set up (status: {project.setup_status})")
        return
    if not get_settings().llm_configured and not _llm_overridden():
        set_status(incident_id, Status.FAILED, "NEBIUS_API_KEY is not configured")
        return
    with projects.project_lock(project.id):
        sha = projects.sync(project)
        with SessionLocal() as db:
            db.get(Incident, incident_id).commit_sha = sha
            db.add(AuditEvent(incident_id=incident_id, actor="orchestrator", action="repo_synced",
                              detail={"project": project.name, "sha": sha}))
            db.commit()
        with use_project(projects.load_context(project.id)):
            run_pipeline(incident_id, sha)


def run_pipeline(incident_id: int, sha: str) -> None:
    s = get_settings()
    inc = _load(incident_id)

    if not inc.summary:
        _triage_summary(inc)

    # 1) Investigate
    set_status(incident_id, Status.INVESTIGATING)
    root_cause = investigator.investigate(_load(incident_id))
    with SessionLocal() as db:
        db.get(Incident, incident_id).root_cause = root_cause
        db.commit()

    # 2) Fix -> 3) Test, with retries
    baseline = _baseline(inc.project_id, sha)
    previous_failure: str | None = None
    for attempt in range(1, s.max_fix_attempts + 1):
        set_status(incident_id, Status.FIXING, f"attempt {attempt}/{s.max_fix_attempts}")
        try:
            patch, explanation = fixer.fix(_load(incident_id), root_cause, attempt, previous_failure)
        except Exception as e:
            if "budget" in str(e).lower():
                raise
            previous_failure = f"Patch generation failed: {e}"
            _store_patch(incident_id, attempt, "", f"(no valid patch: {e})", {}, False, None, previous_failure)
            continue

        set_status(incident_id, Status.TESTING, f"attempt {attempt}/{s.max_fix_attempts}")
        ok, summary, test_log = verify_patch(incident_id, patch, attempt, baseline)
        _store_patch(incident_id, attempt, patch.diff, explanation, patch.files, ok, summary, test_log)
        if ok:
            set_status(incident_id, Status.AWAITING_APPROVAL,
                       f"verified on attempt {attempt}: fixed {len(summary['fixed'])} test(s), 0 regressions")
            with SessionLocal() as db:
                db.add(AgentStep(incident_id=incident_id, agent="approval_gate", status="running",
                                 input_summary="Waiting for a human decision", output={"patch_attempt": attempt}))
                db.commit()
            return
        previous_failure = test_log

    set_status(incident_id, Status.NEEDS_HUMAN,
               f"no patch passed verification after {s.max_fix_attempts} attempts; not proposed as a fix")


def _llm_overridden() -> bool:
    from . import llm

    return llm._client_override is not None


def _store_patch(incident_id, attempt, diff, explanation, files, ok, summary, test_log) -> None:
    with SessionLocal() as db:
        db.add(Patch(incident_id=incident_id, attempt=attempt, diff=diff, explanation=explanation,
                     files=files, tests_passed=ok, test_summary=summary, test_log=test_log[-12000:]))
        db.commit()


# ---------------- Human gate ----------------

def _verified_patch(db, incident_id: int) -> Patch | None:
    return db.scalars(
        select(Patch).where(Patch.incident_id == incident_id, Patch.tests_passed.is_(True)).order_by(Patch.attempt.desc())
    ).first()


def _close_gate_step(db, incident_id: int, output: dict) -> None:
    step = db.scalars(select(AgentStep).where(AgentStep.incident_id == incident_id, AgentStep.agent == "approval_gate",
                                             AgentStep.status == "running")).first()
    if step:
        step.status = "done"
        step.output = output
        step.finished_at = utcnow()


def pr_body(inc: Incident, patch: Patch, approver: str, note: str) -> str:
    rc = inc.root_cause or {}
    ts = patch.test_summary or {}
    evidence = "\n".join(f"- {e}" for e in rc.get("evidence", []))
    sources = "\n".join(f"- [{x.get('title') or x.get('url')}]({x.get('url')})" for x in rc.get("tavily_sources", [])[:5])
    return f"""## Incident #{inc.id}: {inc.title}

**Severity:** {inc.severity} · **Occurrences:** {inc.occurrences} · **Endpoint:** `{inc.endpoint or 'n/a'}` · **Analysed commit:** `{inc.commit_sha[:12]}`

### Root cause ({rc.get('confidence', '?')} confidence)
`{rc.get('file')}:{rc.get('line')}` in `{rc.get('function', '')}`

{rc.get('root_cause', '')}

**Evidence**
{evidence or '- (none recorded)'}
{('**Web sources (Tavily)**' + chr(10) + sources) if sources else ''}

### Fix
{patch.explanation}

```diff
{patch.diff}
```

### Verification (sandboxed pytest, attempt {patch.attempt})
- Fixed tests: {', '.join(f'`{t}`' for t in ts.get('fixed', [])) or 'none'}
- New failures: {len(ts.get('regressions', []))}
- Before: {ts.get('before', {}).get('passed')} passed / {ts.get('before', {}).get('failed')} failed → After: {ts.get('after', {}).get('passed')} passed / {ts.get('after', {}).get('failed')} failed
- Unrelated tests still failing: {', '.join(f'`{t}`' for t in ts.get('still_failing', [])) or 'none'}
- Runner: {ts.get('runner')}

### Human approval
Approved by **{approver}**{f': {note}' if note else ''}.

---
_Opened by AgentOps AI (NVIDIA Nemotron on Nebius Token Factory) after human approval. Not merged automatically._
"""


def approve(incident_id: int, approver: str, note: str = "") -> Decision:
    with _lock:  # one approval in flight per incident, so a double click can't open two PRs
        if incident_id in _approving:
            raise TransitionError("approval already in progress")
        _approving.add(incident_id)
    try:
        return _approve(incident_id, approver, note)
    finally:
        with _lock:
            _approving.discard(incident_id)


def _approve(incident_id: int, approver: str, note: str) -> Decision:
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            raise TransitionError("incident not found")
        if inc.status != Status.AWAITING_APPROVAL:
            raise TransitionError(f"incident is {inc.status}; only AWAITING_APPROVAL can be approved")
        patch = _verified_patch(db, incident_id)
        if patch is None:
            raise TransitionError("no verified patch to approve")
        db.add(AuditEvent(incident_id=incident_id, actor=approver, action="approve_requested", detail={"patch_id": patch.id}))
        db.commit()
        body = pr_body(inc, patch, approver, note)
        slug = f"{inc.exception_type}-{(inc.location or {}).get('function', '')}"
    try:
        with use_project(projects.load_context(inc.project_id)):
            pr = github.open_pull_request(incident_id=incident_id, title=inc.title, body=body, files=patch.files,
                                          slug=slug, base_sha=inc.commit_sha)
    except (github.GitHubError, httpx.HTTPError) as e:
        with SessionLocal() as db:
            db.add(AuditEvent(incident_id=incident_id, actor="github", action="pr_failed", detail={"error": str(e)}))
            db.commit()
        raise github.GitHubError(str(e)) from e
    with SessionLocal() as db:
        d = Decision(incident_id=incident_id, patch_id=patch.id, decision="approve", reason=note,
                     decided_by=approver, pr_url=pr.url)
        db.add(d)
        _close_gate_step(db, incident_id, {"decision": "approve", "by": approver, "pr_url": pr.url, "branch": pr.branch})
        db.commit()
    set_status(incident_id, Status.PR_CREATED, f"PR #{pr.number} on branch {pr.branch}", actor=approver)
    return d


def reject(incident_id: int, approver: str, reason: str) -> Decision:
    if not reason.strip():
        raise TransitionError("a rejection reason is required")
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            raise TransitionError("incident not found")
        if inc.status in RUNNING_STATUSES or inc.status in (Status.PR_CREATED, Status.REJECTED):
            raise TransitionError(f"incident is {inc.status}; cannot reject now")
        patch = _verified_patch(db, incident_id)
        d = Decision(incident_id=incident_id, patch_id=patch.id if patch else None, decision="reject",
                     reason=reason, decided_by=approver)
        db.add(d)
        _close_gate_step(db, incident_id, {"decision": "reject", "by": approver, "reason": reason})
        db.commit()
    set_status(incident_id, Status.REJECTED, reason, actor=approver)
    return d


def recover_interrupted() -> int:
    """On startup: incidents left mid-pipeline by a restart are marked FAILED so they can be re-run."""
    from sqlalchemy import update

    with SessionLocal() as db:
        n = db.execute(
            update(Incident).where(Incident.status.in_([x.value for x in RUNNING_STATUSES] + [Status.DETECTED.value]))
            .values(status=Status.FAILED, status_reason="interrupted by a server restart; run it again")
        ).rowcount
        db.execute(update(AgentStep).where(AgentStep.status == "running", AgentStep.agent != "approval_gate")
                   .values(status="error", output={"error": "interrupted by restart"}))
        db.commit()
    return n


def rerun(incident_id: int) -> bool:
    inc = _load(incident_id)
    if inc is None:
        raise TransitionError("incident not found")
    if inc.status not in (Status.DETECTED, Status.FAILED, Status.NEEDS_HUMAN):
        raise TransitionError(f"incident is {inc.status}; only DETECTED, FAILED or NEEDS_HUMAN can be re-run")
    return submit(incident_id)

"""Incident state machine.

DETECTED -> INVESTIGATING -> FIXING -> TESTING -> AWAITING_APPROVAL -> PR_CREATED -> RESOLVED (merged)
                                 ^________|  (verification fails: retry, max N)       |  -> PR_CLOSED (closed unmerged)
                                       -> NEEDS_HUMAN                    review feedback -> REVISING -> PR_CREATED
AWAITING_APPROVAL -> REJECTED (human). Any pipeline error -> FAILED (can be re-run).

Each incident runs in a worker thread so the API stays responsive. The approval gate is
not an LLM: the pipeline stops at AWAITING_APPROVAL and only a human request moves it on.
"""

from __future__ import annotations

import dataclasses
import difflib
import logging
import threading

import httpx
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from . import memory, notify, cancel
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
from .tools.patching import BuiltPatch
from .tools.sandbox import TestRun, run_tests

log = logging.getLogger("agentops.orchestrator")
_executor = ThreadPoolExecutor(max_workers=get_settings().worker_threads, thread_name_prefix="incident")
_running: set[int] = set()
_approving: set[int] = set()
_lock = threading.Lock()
_baseline_cache: dict[tuple[int, str], TestRun] = {}  # (project_id, commit sha) -> unpatched test run


class TransitionError(ValueError):
    pass


NOTIFY_ON = {Status.AWAITING_APPROVAL: "awaiting_approval", Status.NEEDS_HUMAN: "needs_human",
             Status.FAILED: "failed", Status.RESOLVED: "resolved"}


def set_status(incident_id: int, status: Status, reason: str = "", actor: str = "orchestrator",
               event: str | None = None) -> None:
    _set_status(incident_id, status, reason, actor)
    ev = event or NOTIFY_ON.get(status)
    if ev:
        notify.incident_event(incident_id, ev, reason)


def _set_status(incident_id: int, status: Status, reason: str = "", actor: str = "orchestrator") -> None:
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


def _close_running_steps(incident_id: int, note: str) -> None:
    from sqlalchemy import update

    with SessionLocal() as db:
        db.execute(update(AgentStep).where(AgentStep.incident_id == incident_id, AgentStep.status == "running",
                                           AgentStep.agent != "approval_gate")
                   .values(status="error", output={"error": note}, finished_at=utcnow()))
        db.commit()


def _run_safe(incident_id: int) -> None:
    try:
        _run_for_project(incident_id)
    except cancel.Cancelled as c:
        _close_running_steps(incident_id, str(c))
        set_status(incident_id, Status.FAILED, f"cancelled by {c.by}; run it again when ready", actor=c.by,
                   event="none")
    except Exception as e:  # never let a worker die silently
        log.exception("incident %s failed", incident_id)
        set_status(incident_id, Status.FAILED, f"{type(e).__name__}: {e}")
    finally:
        cancel.clear(incident_id)
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

    # 0) Recall similar past fixes in this project
    similar = memory.similar(incident_id)
    if similar:
        with SessionLocal() as db:
            db.get(Incident, incident_id).similar_incidents = similar
            db.add(AuditEvent(incident_id=incident_id, actor="memory", action="similar_incidents",
                              detail={"ids": [x["incident_id"] for x in similar]}))
            db.commit()

    # 1) Investigate
    cancel.check(incident_id)
    set_status(incident_id, Status.INVESTIGATING)
    root_cause = investigator.investigate(_load(incident_id))
    with SessionLocal() as db:
        db.get(Incident, incident_id).root_cause = root_cause
        db.commit()

    # 2) Fix -> 3) Test, with retries
    baseline = _baseline(inc.project_id, sha)
    previous_failure: str | None = None
    for attempt in range(1, s.max_fix_attempts + 1):
        cancel.check(incident_id)
        set_status(incident_id, Status.FIXING, f"attempt {attempt}/{s.max_fix_attempts}")
        try:
            patch, explanation = fixer.fix(_load(incident_id), root_cause, attempt, previous_failure)
        except cancel.Cancelled:
            raise
        except Exception as e:
            if "budget" in str(e).lower():
                raise
            previous_failure = f"Patch generation failed: {e}"
            _store_patch(incident_id, attempt, "", f"(no valid patch: {e})", {}, False, None, previous_failure)
            continue

        cancel.check(incident_id)
        set_status(incident_id, Status.TESTING, f"attempt {attempt}/{s.max_fix_attempts}")
        ok, summary, test_log = verify_patch(incident_id, patch, attempt, baseline)
        _store_patch(incident_id, attempt, patch.diff, explanation, patch.files, ok, summary, test_log,
                     regression_tests=summary.get("regression_tests", []))
        if ok:
            set_status(incident_id, Status.AWAITING_APPROVAL, _verified_reason(summary, attempt))
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


def _verified_reason(summary: dict, attempt: int) -> str:
    n_repro = len(summary.get("regression_tests", []))
    other = [t for t in summary.get("fixed", []) if t not in summary.get("regression_tests", [])]
    parts = [f"verified on attempt {attempt}"]
    if n_repro:
        parts.append(f"{n_repro} new regression test(s) fail before and pass after")
    if other:
        parts.append(f"{len(other)} existing failing test(s) now pass")
    parts.append("0 regressions")
    return ": ".join(parts[:1]) + ": " + ", ".join(parts[1:])


def _store_patch(incident_id, attempt, diff, explanation, files, ok, summary, test_log, kind="initial",
                 feedback="", regression_tests=None) -> Patch:
    with SessionLocal() as db:
        p = Patch(incident_id=incident_id, attempt=attempt, diff=diff, explanation=explanation, files=files,
                  tests_passed=ok, test_summary=summary, test_log=test_log[-12000:], kind=kind, feedback=feedback,
                  regression_tests=regression_tests or [])
        db.add(p)
        db.commit()
        return p


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
- Regression test(s) added: {', '.join(f'`{t}`' for t in ts.get('regression_tests', [])) or 'none'} (fail on the original code, pass with the fix)
- Fixed tests: {', '.join(f'`{t}`' for t in ts.get('fixed', [])) or 'none'}
- New failures: {len(ts.get('regressions', []))}
- Before: {ts.get('before', {}).get('passed')} passed / {ts.get('before', {}).get('failed')} failed → After: {ts.get('after', {}).get('passed')} passed / {ts.get('after', {}).get('failed')} failed
- Unrelated tests still failing: {', '.join(f'`{t}`' for t in ts.get('still_failing', [])) or 'none'}
- Runner: {ts.get('runner')}

{_history_md(inc)}### Human approval
Approved by **{approver}**{f': {note}' if note else ''}.

---
_Opened by AgentOps AI (NVIDIA Nemotron on Nebius Token Factory) after human approval. Not merged automatically._
"""


def _history_md(inc: Incident) -> str:
    parts = []
    if inc.regression_of:
        parts.append(f"⚠️ **Regression:** this error was fixed before in incident #{inc.regression_of} and came back.\n")
    if inc.similar_incidents:
        parts.append("**Similar past incidents used as context:** " + ", ".join(
            f"#{x['incident_id']}" + (f" ({x['pr_url']})" if x.get("pr_url") else "") for x in inc.similar_incidents) + "\n")
    return ("\n".join(parts) + "\n") if parts else ""


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
        row = db.get(Incident, incident_id)
        row.pr_number, row.pr_url, row.pr_branch = pr.number, pr.url, pr.branch
        db.commit()
    set_status(incident_id, Status.PR_CREATED, f"PR #{pr.number} on branch {pr.branch}", actor=approver,
               event="pr_created")
    return d


def reject(incident_id: int, approver: str, reason: str) -> Decision:
    if not reason.strip():
        raise TransitionError("a rejection reason is required")
    with SessionLocal() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            raise TransitionError("incident not found")
        if inc.status in RUNNING_STATUSES or inc.status in (Status.PR_CREATED, Status.REJECTED, Status.RESOLVED,
                                                             Status.PR_CLOSED):
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
        db.execute(update(Incident).where(Incident.status == Status.REVISING)
                   .values(status=Status.PR_CREATED, status_reason="revision interrupted by a server restart"))
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


# ---------------- Pull request lifecycle (driven by GitHub webhooks) ----------------

def find_by_pr(github_repo: str, pr_number: int) -> Incident | None:
    with SessionLocal() as db:
        project_ids = [p.id for p in db.scalars(select(Project).where(Project.github_repo == github_repo)).all()]
        if not project_ids:
            return None
        return db.scalars(select(Incident).where(Incident.project_id.in_(project_ids),
                                                 Incident.pr_number == pr_number)).first()


def pr_closed(incident_id: int, merged: bool, by: str) -> None:
    inc = _load(incident_id)
    if inc.status in (Status.RESOLVED, Status.PR_CLOSED):
        return
    if merged:
        with SessionLocal() as db:
            db.get(Incident, incident_id).resolved_at = utcnow()
            db.commit()
        set_status(incident_id, Status.RESOLVED, f"PR #{inc.pr_number} merged by {by}", actor=f"github:{by}")
    else:
        set_status(incident_id, Status.PR_CLOSED, f"PR #{inc.pr_number} closed without merging by {by}",
                   actor=f"github:{by}")


def revision_block_reason(inc: Incident, feedback: str) -> str | None:
    """Why a revision can't be queued right now, or None if it can."""
    s = get_settings()
    if inc.status != Status.PR_CREATED:
        return f"incident is {inc.status}; revisions need an open AgentOps pull request"
    if inc.revisions >= s.max_revisions:
        return f"revision limit reached ({s.max_revisions})"
    if not feedback.strip():
        return "empty feedback"
    if inc.id in _running:
        return "the agents are already working on this incident"
    return None


def request_revision(incident_id: int, feedback: str, author: str, source: str) -> bool:
    """Queue a PR revision from reviewer feedback (GitHub review/comment or the dashboard).
    Returns False if ignored (with an audit entry)."""
    inc = _load(incident_id)
    reason = revision_block_reason(inc, feedback)
    actor = f"dashboard:{author}" if source == "dashboard" else f"github:{author}"
    with SessionLocal() as db:
        db.add(AuditEvent(incident_id=incident_id, actor=actor,
                          action="revision_ignored" if reason else "revision_requested",
                          detail={"source": source, "feedback": feedback[:2000], "reason": reason}))
        db.commit()
    if reason:
        return False
    with _lock:
        if incident_id in _running:
            return False
        _running.add(incident_id)
    _set_status(incident_id, Status.REVISING, f"revising PR after feedback from {author}", actor=actor)
    _executor.submit(_revise_safe, incident_id, feedback, author)
    return True


def _revise_safe(incident_id: int, feedback: str, author: str) -> None:
    try:
        _run_revision(incident_id, feedback, author)
    except cancel.Cancelled as c:
        _close_running_steps(incident_id, str(c))
        set_status(incident_id, Status.PR_CREATED, f"revision cancelled by {c.by}; nothing pushed", actor=c.by,
                   event="none")
    except Exception as e:
        log.exception("revision of incident %s failed", incident_id)
        set_status(incident_id, Status.PR_CREATED, f"revision failed: {type(e).__name__}: {e}",
                   event="revision_failed")
        _safe_comment(incident_id, f"⚠️ AgentOps could not revise this PR: `{type(e).__name__}: {str(e)[:300]}`")
    finally:
        cancel.clear(incident_id)
        with _lock:
            _running.discard(incident_id)


def cancel_run(incident_id: int, by: str) -> None:
    """Ask a running pipeline or revision to stop at its next checkpoint."""
    inc = _load(incident_id)
    if inc is None:
        raise TransitionError("incident not found")
    if incident_id not in _running:
        raise TransitionError(f"incident is {inc.status} and nothing is running")
    cancel.request(incident_id, by)
    with SessionLocal() as db:
        db.add(AuditEvent(incident_id=incident_id, actor=by, action="cancel_requested", detail={"status": inc.status}))
        db.commit()


def _safe_comment(incident_id: int, body: str) -> None:
    inc = _load(incident_id)
    try:
        with use_project(projects.load_context(inc.project_id)):
            github.comment(inc.pr_number, body + "\n\n<sub>AgentOps AI</sub>")
    except Exception as e:
        log.warning("could not comment on PR for incident %s: %s", incident_id, e)


def _diff(old: dict[str, str | None], new: dict[str, str]) -> str:
    parts = []
    for path, text in new.items():
        before = old.get(path) or ""
        d = difflib.unified_diff(before.splitlines(keepends=True), text.splitlines(keepends=True),
                                 fromfile=f"a/{path}" if old.get(path) is not None else "/dev/null", tofile=f"b/{path}")
        parts.append("".join(x if x.endswith("\n") else x + "\n" for x in d))
    return "".join(parts)


def _run_revision(incident_id: int, feedback: str, author: str) -> None:
    s = get_settings()
    inc = _load(incident_id)
    with SessionLocal() as db:
        project = db.get(Project, inc.project_id)
        pr_paths = sorted({p for patch in db.scalars(select(Patch).where(Patch.incident_id == incident_id,
                                                                          Patch.tests_passed.is_(True))).all()
                           for p in (patch.files or {})})
        n_rev = inc.revisions + 1
        db.get(Incident, incident_id).revisions = n_rev
        db.commit()
    with projects.project_lock(project.id):
        projects.sync(project)
        projects.checkout(project.id, inc.commit_sha)  # the PR was branched from this commit
        base_ctx = projects.load_context(project.id)
        with use_project(base_ctx):
            pr_files = github.get_files(inc.pr_branch, pr_paths)
            originals = {p: (base_ctx.app_root / p).read_text(encoding="utf-8") if (base_ctx.app_root / p).is_file()
                         else None for p in pr_files}
            pr_diff = _diff(originals, pr_files)
            baseline = _baseline(project.id, inc.commit_sha)
        ctx = dataclasses.replace(base_ctx, overlay=pr_files)
        with use_project(ctx):
            previous_failure = None
            for attempt in range(1, s.max_fix_attempts + 1):
                cancel.check(incident_id)
                label = f"Revision {n_rev}, attempt {attempt}"
                try:
                    patch, explanation = fixer.fix(_load(incident_id), inc.root_cause or {}, attempt, previous_failure,
                                                   review={"feedback": feedback, "pr_diff": pr_diff})
                except cancel.Cancelled:
                    raise
                except Exception as e:
                    if "budget" in str(e).lower():
                        raise
                    previous_failure = f"Patch generation failed: {e}"
                    continue
                merged = {**pr_files, **patch.files}
                to_test = BuiltPatch(files=merged, diff=patch.diff, changed_lines=patch.changed_lines,
                                     code_lines=patch.code_lines, test_lines=patch.test_lines)
                ok, summary, test_log = verify_patch(incident_id, to_test, attempt, baseline, label=label)
                _store_patch(incident_id, 100 * n_rev + attempt, patch.diff, explanation, merged, ok, summary, test_log,
                             kind="revision", feedback=feedback, regression_tests=summary.get("regression_tests", []))
                if ok:
                    github.commit_files(inc.pr_branch, patch.files,
                                        f"AgentOps: address review feedback from @{author} (incident #{incident_id})")
                    github.comment(inc.pr_number, (
                        f"🔁 **Revision {n_rev}** pushed in response to @{author}'s feedback.\n\n{explanation}\n\n"
                        f"```diff\n{patch.diff[:6000]}\n```\n"
                        f"Verification: regression test(s) {', '.join(f'`{t}`' for t in summary.get('regression_tests', [])) or 'n/a'} "
                        f"fail on the original code and pass now; {len(summary['regressions'])} new failures "
                        f"({summary['runner']}).\n\n<sub>AgentOps AI · Nemotron on Nebius Token Factory</sub>"))
                    set_status(incident_id, Status.PR_CREATED, f"revision {n_rev} pushed (feedback from {author})",
                               event="revised")
                    return
                previous_failure = test_log
            github.comment(inc.pr_number, (
                f"⚠️ AgentOps tried {s.max_fix_attempts} times but could not produce a revision that passes verification "
                f"for @{author}'s feedback. Nothing was pushed; a human needs to take this one.\n\n<sub>AgentOps AI</sub>"))
            set_status(incident_id, Status.PR_CREATED, f"revision {n_rev} failed verification; nothing pushed",
                       event="revision_failed")

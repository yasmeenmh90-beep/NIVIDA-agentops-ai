"""End-to-end tests against a real setup:

- AgentOps runs as a real HTTP server (uvicorn in a thread).
- The project is a real git repository; AgentOps clones it (python_env=system, except in
  test_venv_setup, which creates a real virtualenv and pip-installs the requirements).
- Errors come from really executing the service's code in a separate process, shipped by the
  bundled Python logging handler over HTTP, or from a signed Sentry webhook.
- Tests run in the subprocess sandbox with real pytest, including the agent's regression tests.
- GitHub webhooks are signed with HMAC exactly as GitHub does.
Stand-ins: the LLM (scripted, see fake_llm.py), the GitHub REST API and Slack (httpx MockTransport).
"""

import base64
import hashlib
import hmac
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
import uvicorn

from app import llm, notify
from app.main import app
from app.tools import github
from fake_llm import (GOOD_FIX_BUG1, INVALID_FIX, INVESTIGATE_BUG1, NO_TEST_FIX, NON_REPRO_FIX, REGRESSION_TEST_ID,
                      REGRESSION_TEST_PATH, REVISION_FIX, USELESS_FIX_BUG1, ScriptedLLM)

API = {"X-API-Key": "test-key"}
CLIENT_DIR = Path(__file__).resolve().parents[2] / "clients" / "python"
REPO = "acme/shop"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server():
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield base
    srv.should_exit = True


@pytest.fixture(scope="session")
def api(server):
    with httpx.Client(base_url=server, headers=API, timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def slack():
    sent = []

    def handler(req: httpx.Request):
        sent.append(json.loads(req.content))
        return httpx.Response(200, text="ok")
    notify.set_transport(httpx.MockTransport(handler))
    yield sent
    notify.set_transport(None)


class FakeGitHub:
    """Enough of the GitHub REST API for branches, file commits, PRs and comments."""

    def __init__(self):
        self.files: dict[tuple[str, str], str] = {}
        self.calls: list[tuple] = []
        self.comments: list[tuple[int, str]] = []
        self.next_pr = 7

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content) if req.content else None
        p = unquote(req.url.path)
        self.calls.append((req.method, p, body, req.headers.get("authorization")))
        prefix = f"/repos/{REPO}"
        if req.method == "POST" and p == f"{prefix}/git/refs":
            return httpx.Response(201, json={})
        if "/contents/" in p:
            path = p.split("/contents/", 1)[1]
            if req.method == "GET":
                key = (req.url.params.get("ref"), path)
                if key not in self.files:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"sha": "s", "content": base64.b64encode(self.files[key].encode()).decode()})
            if req.method == "PUT":
                self.files[(body["branch"], path)] = base64.b64decode(body["content"]).decode()
                return httpx.Response(200, json={"commit": {}})
        if req.method == "POST" and p == f"{prefix}/pulls":
            self.next_pr += 1
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/pull/{self.next_pr}",
                                             "number": self.next_pr})
        if req.method == "POST" and p.startswith(f"{prefix}/issues/") and p.endswith("/comments"):
            self.comments.append((int(p.split("/")[-2]), body["body"]))
            return httpx.Response(201, json={})
        return httpx.Response(404, json={"message": f"unexpected {req.method} {p}"})


@pytest.fixture(scope="session")
def gh():
    fake = FakeGitHub()
    github.set_transport(httpx.MockTransport(fake))
    yield fake
    github.set_transport(None)


def wait(fn, timeout=60, interval=0.25):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out; last={last}")


@pytest.fixture(scope="session")
def project(api, remote_repo, gh, slack):
    r = api.post("/projects", json={
        "name": "orders", "repo_url": str(remote_repo), "app_path": "services/orders",
        "github_repo": REPO, "python_env": "system",
    })
    assert r.status_code == 201, r.text
    p = wait(lambda: (d := api.get("/projects/orders").json())["setup_status"] in ("ready", "failed") and d)
    assert p["setup_status"] == "ready", p["setup_log"]
    return p


@pytest.fixture(autouse=True)
def reset_llm():
    yield
    llm.set_client(None)


def ship_real_error(server: str, service_dir: Path, bug: int = 1):
    """Execute the service's real code in another process and ship the exception with the bundled handler."""
    calls = {
        1: "from orders.pricing import order_total\norder_total([{'qty': 2, 'unit_price': 5.0}], None)",
        2: "from orders.serializers import serialize_order\nserialize_order({'id': 1, 'customer_ref': 'c', 'items': [], 'discount_code': None, 'total': 1})",
    }[bug]
    script = f"""
import logging, sys, time
sys.path.insert(0, {str(CLIENT_DIR)!r}); sys.path.insert(0, {str(service_dir)!r})
import agentops_handler
h = agentops_handler.install({server!r}, "test-key", "orders", service="orders-api", hook_uncaught=False,
                             flush_interval_s=0.1)
try:
{chr(10).join('    ' + ln for ln in calls.splitlines())}
except Exception:
    logging.getLogger("orders").exception("request failed", extra={{"http_method": "POST", "http_path": "/orders"}})
h.flush_now(); time.sleep(1.0)
"""
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def latest_incident(api, exc_type):
    def find():
        for i in api.get("/incidents", params={"project": "orders"}).json():
            if i["exception_type"] == exc_type and i["status"] not in (
                    "PR_CREATED", "REJECTED", "NEEDS_HUMAN", "FAILED", "RESOLVED", "PR_CLOSED", "REVISING"):
                return i
        return None
    return wait(find, timeout=15)


def settle(api, incident_id, statuses=("AWAITING_APPROVAL", "NEEDS_HUMAN", "FAILED"), timeout=120):
    def done():
        d = api.get(f"/incidents/{incident_id}").json()
        return d if d["status"] in statuses and not d["running"] else None
    return wait(done, timeout=timeout)


def close(api, incident_id):
    api.post(f"/incidents/{incident_id}/reject", json={"approver": "test", "reason": "cleanup"})


def gh_webhook(server, event, payload, secret="gh-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return httpx.post(server + "/webhooks/github", content=body, headers={
        "X-GitHub-Event": event, "X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def slack_events(slack):
    return [m["text"] for m in slack]


# ---------------------------------------------------------------------------

def test_demo_account_is_viewer(server):
    from app.demo_user import DEMO_EMAIL, DEMO_PASSWORD

    signed = httpx.post(server + "/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["user"]["role"] == "viewer" and body["user"]["email"] == DEMO_EMAIL
    token = _bearer(body["token"])
    assert httpx.get(server + "/projects", headers=token).status_code == 200
    assert httpx.post(server + "/projects", headers=token, json={"name": "nope", "repo_url": "https://github.com/a/b"}).status_code == 403
    assert httpx.get(server + "/users", headers=token).status_code == 403
    users = httpx.get(server + "/users", headers={"X-API-Key": "test-key"})
    assert users.status_code == 200, users.text
    demo = next(u for u in users.json() if u["email"] == DEMO_EMAIL)
    blocked = httpx.patch(server + f"/users/{demo['id']}", headers={"X-API-Key": "test-key"}, json={"role": "admin"})
    assert blocked.status_code == 409
    again = httpx.get(server + "/auth/me", headers=token).json()
    assert again["role"] == "viewer"


def test_auth_required(server):
    assert httpx.get(server + "/incidents").status_code == 401
    assert httpx.get(server + "/incidents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert httpx.get(server + "/health").status_code == 200


def test_project_registered_and_cloned(api, project, remote_repo):
    assert project["github_repo"] == REPO
    assert project["effective_sandbox"] == "subprocess"
    assert project["github_token_present"] is True
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=remote_repo, capture_output=True, text=True).stdout.strip()
    assert project["head_sha"] == head


def test_rejects_unsafe_project_input(api):
    assert api.post("/projects", json={"name": "Bad Name", "repo_url": "https://github.com/a/b"}).status_code == 422
    assert api.post("/projects", json={"name": "x", "repo_url": "https://github.com/a/b", "app_path": "../etc"}).status_code == 422
    assert api.post("/projects", json={"name": "orders", "repo_url": "https://github.com/a/b"}).status_code == 409


def test_full_lifecycle_fix_pr_review_revision_merge(api, server, project, remote_repo, gh, slack):
    fake = ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1, REVISION_FIX]})
    llm.set_client(fake)

    # 1. A real exception from the service reaches AgentOps
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    inc = latest_incident(api, "TypeError")
    assert inc["severity"] == "high" and inc["location"]["file"] == "orders/pricing.py"

    # 2. Investigate -> fix + regression test -> verify
    d = settle(api, inc["id"])
    assert d["status"] == "AWAITING_APPROVAL", d["status_reason"]
    assert "1 new regression test(s) fail before and pass after" in d["status_reason"]
    [patch] = d["patches"]
    ts = patch["test_summary"]
    assert ts["regression_tests"] == [REGRESSION_TEST_ID]
    assert set(ts["fixed"]) == {REGRESSION_TEST_ID, "tests/test_orders.py::test_create_order_without_discount"}
    assert ts["regressions"] == [] and ts["runner"] == "subprocess"
    assert set(patch["files"]) if "files" in patch else True
    assert "+    return DISCOUNT_CODES.get(code, 0.0) if code else 0.0" in patch["diff"]
    assert f"b/{REGRESSION_TEST_PATH}" in patch["diff"]
    assert "DISCOUNT_CODES.get(code)\n" in (remote_repo / "services/orders/orders/pricing.py").read_text()

    # 3. Human approves -> PR with fix and regression test, branched from the analysed commit
    r = api.post(f"/incidents/{inc['id']}/approve", json={"approver": "muhammad", "note": "LGTM"})
    assert r.status_code == 200, r.text
    d = api.get(f"/incidents/{inc['id']}").json()
    assert d["status"] == "PR_CREATED" and d["pr_number"] == 8 and d["pr_branch"].startswith("agentops/incident-")
    ref = next(c for c in gh.calls if c[1].endswith("/git/refs"))
    assert ref[2]["sha"] == project["head_sha"]
    committed = {k[1] for k in gh.files}
    assert committed == {"services/orders/orders/pricing.py", f"services/orders/{REGRESSION_TEST_PATH}"}
    pr = next(c for c in gh.calls if c[1].endswith("/pulls"))
    assert "Regression test(s) added" in pr[2]["body"] and REGRESSION_TEST_ID in pr[2]["body"]
    assert not any("merge" in c[1] for c in gh.calls)
    wait(lambda: any("Pull request opened" in t for t in slack_events(slack)), timeout=5)
    assert any("Verified fix ready for review" in t and "https://agentops.test/incidents/" in t for t in slack_events(slack))

    # 4. Webhook security
    assert gh_webhook(server, "ping", {}, secret="wrong").status_code == 401
    r = gh_webhook(server, "issue_comment", {
        "action": "created", "repository": {"full_name": REPO},
        "issue": {"number": 8, "pull_request": {}},
        "comment": {"body": "/agentops delete everything", "user": {"login": "stranger"},
                    "author_association": "NONE"}})
    assert r.json()["ignored"].startswith("author association")

    # 5. Reviewer requests changes -> Fixer revises the PR, Tester re-verifies, commit + comment
    r = gh_webhook(server, "pull_request_review", {
        "action": "submitted", "repository": {"full_name": REPO}, "pull_request": {"number": 8},
        "review": {"state": "changes_requested", "body": "Please simplify: use `code or ''` instead of the conditional.",
                   "user": {"login": "lead-dev"}, "author_association": "MEMBER"}})
    assert r.json()["revision_queued"] is True, r.text
    d = settle(api, inc["id"], statuses=("PR_CREATED",))
    assert d["revisions"] == 1 and "revision 1 pushed" in d["status_reason"], d["status_reason"]
    rev = [p for p in d["patches"] if p["kind"] == "revision"]
    assert rev[-1]["tests_passed"] is True and "simplify" in rev[-1]["feedback"]
    assert rev[-1]["regression_tests"] == [REGRESSION_TEST_ID]  # still fails on original code, passes now
    assert 'DISCOUNT_CODES.get(code or "", 0.0)' in gh.files[(d["pr_branch"], "services/orders/orders/pricing.py")]
    assert any(n == 8 and "Revision 1" in body for n, body in gh.comments)
    reviser_prompt = fake.first_user_message["reviser"]
    assert "Reviewer feedback" in reviser_prompt and "get(code, 0.0) if code" in reviser_prompt

    # 6. PR merged -> RESOLVED
    r = gh_webhook(server, "pull_request", {
        "action": "closed", "repository": {"full_name": REPO},
        "pull_request": {"number": 8, "merged": True, "merged_by": {"login": "lead-dev"}}})
    assert r.json()["merged"] is True
    d = api.get(f"/incidents/{inc['id']}").json()
    assert d["status"] == "RESOLVED" and d["resolved_at"]
    wait(lambda: any("Fix merged" in t for t in slack_events(slack)), timeout=5)


def test_regression_detected_and_memory_used(api, server, project, remote_repo):
    """The same error after its fix was merged is flagged as a regression, and the earlier fix is recalled."""
    fake = ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]})
    llm.set_client(fake)
    resolved = [i for i in api.get("/incidents", params={"project": "orders", "status": "RESOLVED"}).json()]
    assert resolved
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    inc = latest_incident(api, "TypeError")
    assert inc["regression_of"] == resolved[0]["id"] and inc["severity"] == "critical"
    d = settle(api, inc["id"])
    assert d["similar_incidents"][0]["incident_id"] == resolved[0]["id"]
    prompt = fake.first_user_message["investigator"]
    assert "Similar incidents fixed earlier" in prompt and "already fixed once" in prompt
    close(api, d["id"])


def test_fix_without_regression_test_is_sent_back(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [NO_TEST_FIX, GOOD_FIX_BUG1]}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "AWAITING_APPROVAL"
    fixer = next(s for s in d["steps"] if s["agent"] == "fixer")
    assert "no regression test" in fixer["tool_calls"][0]["result_preview"]
    close(api, d["id"])


def test_test_that_does_not_reproduce_bug_is_not_accepted(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [NON_REPRO_FIX] * 3}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "NEEDS_HUMAN"
    assert "does not reproduce the bug" in d["patches"][0]["test_summary"]["problems"][0]
    close(api, d["id"])


def test_invalid_edit_is_corrected_within_attempt(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [INVALID_FIX, GOOD_FIX_BUG1]}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "AWAITING_APPROVAL"
    fixer = next(s for s in d["steps"] if s["agent"] == "fixer")
    assert "REJECTED" in fixer["tool_calls"][0]["result_preview"]
    close(api, d["id"])


def test_needs_human_when_fix_does_not_work(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [USELESS_FIX_BUG1] * 3}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "NEEDS_HUMAN"
    assert [p["tests_passed"] for p in d["patches"]] == [False, False, False]
    assert "still fails after the fix" in " ".join(d["patches"][0]["test_summary"]["problems"])
    assert api.post(f"/incidents/{d['id']}/approve", json={"approver": "x"}).status_code == 409
    close(api, d["id"])


def test_repeats_grouped_and_no_llm_key_fails_cleanly(api, server, project, remote_repo):
    for _ in range(3):
        api.post("/ingest/logs", json={"project": "orders", "source": "test", "lines": [json.dumps({
            "method": "GET", "path": "/orders/1",
            "traceback": 'Traceback (most recent call last):\n  File "/x/services/orders/orders/serializers.py", line 7, in serialize_order\n    "customer_id": record["customer_id"],\nKeyError: \'customer_id\'\n'})]})
    incs = [i for i in api.get("/incidents", params={"project": "orders"}).json() if i["exception_type"] == "KeyError"]
    assert len(incs) == 1
    d = settle(api, incs[0]["id"])
    assert d["status"] == "FAILED" and "NEBIUS_API_KEY" in d["status_reason"]


def test_sentry_webhook(api, server, project):
    payload = {"action": "triggered", "data": {"event": {
        "project_name": "orders-api", "request": {"method": "GET", "url": "https://shop.example.com/orders?page=last"},
        "exception": {"values": [{"type": "IndexError", "value": "list index out of range", "stacktrace": {"frames": [
            {"abs_path": "/srv/app/orders/main.py", "lineno": 61, "function": "list_orders"},
            {"abs_path": "/srv/app/orders/pagination.py", "lineno": 17, "function": "paginate"}]}}]}}}}
    body = json.dumps(payload).encode()
    assert httpx.post(server + "/webhooks/sentry/orders", content=body,
                      headers={"Sentry-Hook-Signature": "bad"}).status_code == 401
    sig = hmac.new(b"sentry-secret", body, hashlib.sha256).hexdigest()
    r = httpx.post(server + "/webhooks/sentry/orders", content=body,
                   headers={"Sentry-Hook-Signature": sig, "Sentry-Hook-Resource": "event_alert"})
    assert r.status_code == 200 and r.json()["new"], r.text
    inc = api.get(f"/incidents/{r.json()['new'][0]}").json()
    assert inc["exception_type"] == "IndexError" and inc["endpoint"] == "GET /orders"
    assert inc["location"]["file"] == "orders/pagination.py"
    settle(api, inc["id"])


def test_metrics(api, project):
    m = api.get("/metrics", params={"project": "orders"}).json()
    assert m["incidents"]["total"] >= 7 and m["incidents"]["regressions"] >= 1
    fq = m["fix_quality"]
    assert fq["merged"] == 1 and fq["revisions"] == {"total": 1, "verified": 1}
    assert 0 < fq["first_attempt_success_rate"] <= 1 and fq["ever_needed_human"] == 2
    assert m["timing_minutes"]["detect_to_pr"]["count"] == 1
    assert m["timing_minutes"]["detect_to_merged_mttr"]["count"] == 1
    assert m["llm_tokens"]["total"] > 0


def test_ingest_unknown_project(api):
    assert api.post("/ingest/logs", json={"project": "nope", "lines": ["x"]}).status_code == 404


def test_venv_setup_installs_requirements(api, remote_repo):
    """Real dependency install into a per-project virtualenv (needs network access to PyPI)."""
    r = api.post("/projects", json={"name": "orders-venv", "repo_url": f"file://{remote_repo}",
                                     "app_path": "services/orders", "github_repo": ""})
    assert r.status_code == 201
    p = wait(lambda: (d := api.get("/projects/orders-venv").json())["setup_status"] in ("ready", "failed") and d,
             timeout=600, interval=1)
    assert p["setup_status"] == "ready", p["setup_log"][-2000:]
    assert "requirements.txt" in p["setup_log"]


def test_all_timestamps_are_utc_marked(api):
    d = api.get("/incidents/1").json()
    stamps = [d["first_seen"]] + [s["started_at"] for s in d["steps"]] + [a["ts"] for a in d["audit"]] + \
             [p["created_at"] for p in d["patches"]] + [x["decided_at"] for x in d["decisions"]]
    assert stamps and all(t.endswith("Z") or t.endswith("+00:00") for t in stamps), stamps[:5]


# ---------------- dashboard controls: cancel, revision, activity, notifications ----------------

class BlockingLLM(ScriptedLLM):
    """Holds the first Investigator call until released, so a test can cancel a run mid-flight."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.entered = threading.Event()
        self.release = threading.Event()

    def chat(self, messages, tools, model, tool_choice=None):
        if "Investigator agent" in messages[0]["content"] and not self.entered.is_set():
            self.entered.set()
            self.release.wait(30)
        return super().chat(messages, tools, model, tool_choice)


def test_cancel_running_pipeline(api, server, project, remote_repo):
    fake = BlockingLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]})
    llm.set_client(fake)
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    inc = latest_incident(api, "TypeError")
    assert fake.entered.wait(30)
    r = api.post(f"/incidents/{inc['id']}/cancel", json={"by": "yasmeen"})
    assert r.status_code == 200, r.text
    fake.release.set()
    d = settle(api, inc["id"], statuses=("FAILED",))
    assert d["status_reason"].startswith("cancelled by yasmeen")
    assert not d["patches"]  # stopped before the Fixer ran
    assert any(s["status"] == "error" and "cancelled" in str(s["output"]) for s in d["steps"])
    assert any(a["action"] == "cancel_requested" and a["actor"] == "yasmeen" for a in d["audit"])
    assert api.post(f"/incidents/{inc['id']}/cancel", json={"by": "yasmeen"}).status_code == 409
    close(api, inc["id"])


def test_dashboard_requested_revision(api, server, project, remote_repo, gh):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1, REVISION_FIX]}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "AWAITING_APPROVAL"
    r = api.post(f"/incidents/{d['id']}/revise", json={"author": "yasmeen", "feedback": "simplify"})
    assert r.status_code == 409 and "pull request" in r.json()["detail"]
    assert api.post(f"/incidents/{d['id']}/approve", json={"approver": "yasmeen"}).status_code == 200
    r = api.post(f"/incidents/{d['id']}/revise",
                 json={"author": "yasmeen", "feedback": "Please simplify: use `code or ''` instead of the conditional."})
    assert r.status_code == 200, r.text
    d = settle(api, d["id"], statuses=("PR_CREATED",))
    assert d["revisions"] == 1 and "revision 1 pushed" in d["status_reason"], d["status_reason"]
    assert [p for p in d["patches"] if p["kind"] == "revision"][-1]["tests_passed"] is True
    assert any(a["action"] == "revision_requested" and a["actor"] == "dashboard:yasmeen" for a in d["audit"])
    assert any(n == d["pr_number"] and "Revision 1" in body for n, body in gh.comments)


def test_activity_feed_and_test_notification(api, project, slack):
    feed = api.get("/activity", params={"limit": 200}).json()
    actions = {a["action"] for a in feed}
    assert {"cancel_requested", "revision_requested", "incident_opened", "status_change"} <= actions
    assert not any(a["action"].startswith("tool:") for a in feed)
    assert feed[0]["id"] > feed[-1]["id"] and feed[0]["ts"].endswith("Z") and feed[0]["incident_title"]
    with_tools = api.get("/activity", params={"limit": 200, "include_tools": True}).json()
    assert any(a["action"].startswith("tool:") for a in with_tools)
    older = api.get("/activity", params={"before_id": feed[0]["id"], "limit": 5}).json()
    assert all(a["id"] < feed[0]["id"] for a in older)
    assert all(a["project"] == "orders" for a in api.get("/activity", params={"project": "orders"}).json())

    r = api.post("/notifications/test")
    assert r.status_code == 200 and r.json()["results"]["slack"] == "ok", r.text
    assert any("Test notification" in t for t in slack_events(slack))
    st = api.get("/status").json()
    assert st["public_base_url"] == "https://agentops.test" and st["max_revisions"] == 3


# ---------------- accounts: sign-up, sign-in, roles, GitHub OAuth ----------------

def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_accounts_signup_login_roles(api, server, project):
    cfg = httpx.get(server + "/auth/config").json()
    assert cfg["password"] is True and cfg["github"] is False and cfg["signup"] == "open"

    r = httpx.post(server + "/auth/signup", json={"email": "Yasmeen@Example.com", "name": "yasmeen", "password": "s3cret-pass"})
    assert r.status_code == 201, r.text
    admin = r.json()
    assert admin["user"]["role"] == "admin" and admin["user"]["email"] == "yasmeen@example.com"
    assert admin["token"].startswith("aos_")
    assert httpx.post(server + "/auth/signup", json={"email": "yasmeen@example.com", "name": "x", "password": "another-pass"}).status_code == 409
    assert httpx.post(server + "/auth/signup", json={"email": "nope", "name": "x", "password": "another-pass"}).status_code == 422
    assert httpx.post(server + "/auth/signup", json={"email": "a@b.co", "name": "x", "password": "short"}).status_code == 422

    sai = httpx.post(server + "/auth/signup", json={"email": "sai@example.com", "name": "sai", "password": "frontend-dev"}).json()
    assert sai["user"]["role"] == "viewer"

    # sign in, wrong password, me
    assert httpx.post(server + "/auth/login", json={"email": "sai@example.com", "password": "wrong-pass"}).status_code == 401
    tok = httpx.post(server + "/auth/login", json={"email": "SAI@example.com", "password": "frontend-dev"}).json()["token"]
    me = httpx.get(server + "/auth/me", headers=_bearer(tok)).json()
    assert me["role"] == "viewer" and me["user"]["name"] == "sai"

    # viewer: can read everything, can't act
    assert httpx.get(server + "/incidents", headers=_bearer(tok)).status_code == 200
    target = next(i for i in api.get("/incidents").json() if i["status"] == "FAILED")
    r = httpx.post(server + f"/incidents/{target['id']}/reject", headers=_bearer(tok), json={"approver": "sai", "reason": "x"})
    assert r.status_code == 403 and "view-only" in r.json()["detail"]
    assert httpx.post(server + "/projects", headers=_bearer(tok), json={"name": "x", "repo_url": "y"}).status_code == 403
    assert httpx.get(server + "/users", headers=_bearer(tok)).status_code == 403
    with httpx.stream("GET", server + f"/incidents/stream?api_key={tok}", timeout=10) as s:
        assert s.status_code == 200

    # admin promotes sai to member; the recorded actor is the account, not the name in the body
    users = httpx.get(server + "/users", headers=_bearer(admin["token"])).json()
    sai_id = next(u["id"] for u in users if u["email"] == "sai@example.com")
    assert httpx.patch(server + f"/users/{sai_id}", headers=_bearer(admin["token"]), json={"role": "member"}).json()["role"] == "member"
    r = httpx.post(server + f"/incidents/{target['id']}/reject", headers=_bearer(tok), json={"approver": "someone-else", "reason": "dup"})
    assert r.status_code == 200, r.text
    assert api.get(f"/incidents/{target['id']}").json()["decisions"][-1]["decided_by"] == "sai"

    # guard rails
    admin_id = admin["user"]["id"]
    assert httpx.patch(server + f"/users/{admin_id}", headers=_bearer(admin["token"]), json={"role": "viewer"}).status_code == 409
    assert httpx.delete(server + f"/users/{admin_id}", headers=_bearer(admin["token"])).status_code == 409

    # sign out kills the session; service API keys keep working
    assert httpx.post(server + "/auth/logout", headers=_bearer(tok)).status_code == 200
    assert httpx.get(server + "/incidents", headers=_bearer(tok)).status_code == 401
    assert httpx.get(server + "/incidents").status_code == 401
    assert api.get("/incidents").status_code == 200


def test_login_rate_limited(server):
    httpx.post(server + "/auth/signup", json={"email": "rl@example.com", "name": "rl", "password": "right-pass-1"})
    codes = [httpx.post(server + "/auth/login", json={"email": "rl@example.com", "password": "bad"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_github_sign_in(server, monkeypatch):
    from app.config import get_settings
    from app.routers import accounts

    s = get_settings()
    monkeypatch.setattr(s, "github_oauth_client_id", "cid")
    monkeypatch.setattr(s, "github_oauth_client_secret", "csecret")
    monkeypatch.setattr(s, "frontend_url", "https://dash.test")
    seen = []

    def gh(req: httpx.Request):
        seen.append((req.method, str(req.url)))
        if req.url.host == "github.com":
            assert b"code=abc" in req.content and b"client_secret=csecret" in req.content
            return httpx.Response(200, json={"access_token": "gho_x"})
        if req.url.path == "/user":
            return httpx.Response(200, json={"id": 4242, "login": "octo", "name": "Octo Cat", "email": None, "avatar_url": "https://a/x.png"})
        if req.url.path == "/user/emails":
            return httpx.Response(200, json=[{"email": "octo@example.com", "primary": True, "verified": True}])
        return httpx.Response(404)
    accounts.set_transport(httpx.MockTransport(gh))
    try:
        assert httpx.get(server + "/auth/config").json()["github"] is True
        r = httpx.get(server + "/auth/github/start", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("https://github.com/login/oauth/authorize?")
        state = dict(x.split("=", 1) for x in r.headers["location"].split("?", 1)[1].split("&"))["state"]
        r = httpx.get(server + "/auth/github/callback", params={"code": "abc", "state": state}, follow_redirects=False)
        loc = r.headers["location"]
        assert r.status_code == 302 and loc.startswith("https://dash.test/auth/callback#token=aos_"), loc
        token = loc.split("token=", 1)[1]
        me = httpx.get(server + "/auth/me", headers=_bearer(token)).json()
        assert me["user"]["github_login"] == "octo" and me["user"]["name"] == "Octo Cat"
        assert me["user"]["email"] == "octo@example.com" and me["role"] == "viewer" and me["user"]["has_password"] is False
        # replayed state is rejected
        r = httpx.get(server + "/auth/github/callback", params={"code": "abc", "state": state}, follow_redirects=False)
        assert "error=" in r.headers["location"]
        # a GitHub-only account can't use password sign-in
        assert httpx.post(server + "/auth/login", json={"email": "octo@example.com", "password": "whatever1"}).json()["detail"] == "this account uses GitHub sign-in"
    finally:
        accounts.set_transport(None)

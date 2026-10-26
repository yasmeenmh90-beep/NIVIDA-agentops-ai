"""End-to-end tests against a real setup:

- AgentOps runs as a real HTTP server (uvicorn in a thread).
- The project is a real git repository; AgentOps clones it and installs nothing (python_env=system)
  except in test_venv_setup, which creates a real virtualenv and pip-installs the requirements.
- Errors come from really executing the service's code in a separate process, shipped by the
  bundled Python logging handler over HTTP.
- Tests run in the subprocess sandbox with real pytest.
Only the LLM (scripted, see fake_llm.py) and the GitHub API (httpx MockTransport) are stand-ins.
"""

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from app import llm
from app.main import app
from app.tools import github
API = {"X-API-Key": "test-key"}
from fake_llm import GOOD_FIX_BUG1, INVALID_FIX, INVESTIGATE_BUG1, USELESS_FIX_BUG1, ScriptedLLM

CLIENT_DIR = Path(__file__).resolve().parents[2] / "clients" / "python"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server():
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
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
def project(api, remote_repo):
    r = api.post("/projects", json={
        "name": "orders", "repo_url": str(remote_repo), "app_path": "services/orders",
        "github_repo": "acme/shop", "python_env": "system",
    })
    assert r.status_code == 201, r.text
    p = wait(lambda: (d := api.get("/projects/orders").json())["setup_status"] in ("ready", "failed") and d)
    assert p["setup_status"] == "ready", p["setup_log"]
    return p


@pytest.fixture(autouse=True)
def reset_fakes():
    yield
    llm.set_client(None)
    github.set_transport(None)


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
            if i["exception_type"] == exc_type and i["status"] not in ("PR_CREATED", "REJECTED", "NEEDS_HUMAN", "FAILED"):
                return i
        return None
    return wait(find, timeout=15)


def settle(api, incident_id, timeout=120):
    def done():
        d = api.get(f"/incidents/{incident_id}").json()
        return d if d["status"] in ("AWAITING_APPROVAL", "NEEDS_HUMAN", "FAILED") and not d["running"] else None
    return wait(done, timeout=timeout)


def github_mock(record):
    def handler(req: httpx.Request):
        record.append((req.method, req.url.path, json.loads(req.content) if req.content else None, req.headers.get("authorization")))
        p = req.url.path
        if req.method == "POST" and p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if req.method == "GET" and "/contents/" in p:
            return httpx.Response(200, json={"sha": "filesha"})
        if req.method == "PUT" and "/contents/" in p:
            return httpx.Response(200, json={"commit": {}})
        if req.method == "POST" and p.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/acme/shop/pull/7", "number": 7})
        return httpx.Response(404, json={"message": "unexpected"})
    return httpx.MockTransport(handler)


def close(api, incident_id):
    api.post(f"/incidents/{incident_id}/reject", json={"approver": "test", "reason": "cleanup"})


# ---------------------------------------------------------------------------

def test_auth_required(server):
    assert httpx.get(server + "/incidents").status_code == 401
    assert httpx.get(server + "/incidents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert httpx.get(server + "/health").status_code == 200


def test_project_registered_and_cloned(api, project, remote_repo):
    assert project["github_repo"] == "acme/shop"
    assert project["effective_sandbox"] == "subprocess"
    assert project["github_token_present"] is True
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=remote_repo, capture_output=True, text=True).stdout.strip()
    assert project["head_sha"] == head


def test_rejects_unsafe_project_input(api):
    assert api.post("/projects", json={"name": "Bad Name", "repo_url": "https://github.com/a/b"}).status_code == 422
    assert api.post("/projects", json={"name": "x", "repo_url": "https://github.com/a/b", "app_path": "../etc"}).status_code == 422
    assert api.post("/projects", json={"name": "orders", "repo_url": "https://github.com/a/b"}).status_code == 409


def test_full_loop_real_error_to_pull_request(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    inc = latest_incident(api, "TypeError")
    assert inc["severity"] == "high"
    assert inc["location"]["file"] == "orders/pricing.py"

    d = settle(api, inc["id"])
    assert d["status"] == "AWAITING_APPROVAL", d["status_reason"]
    assert d["commit_sha"] == project["head_sha"]
    [patch] = d["patches"]
    ts = patch["test_summary"]
    assert ts["fixed"] == ["tests/test_orders.py::test_create_order_without_discount"]
    assert ts["regressions"] == [] and ts["runner"] == "subprocess"
    inv = next(s for s in d["steps"] if s["agent"] == "investigator")
    assert [c["tool"] for c in inv["tool_calls"]] == ["read_file", "web_search", "submit_root_cause"]

    # The remote repository is untouched: fixes only travel via PR.
    assert "DISCOUNT_CODES.get(code)\n" in (remote_repo / "services/orders/orders/pricing.py").read_text()

    calls = []
    github.set_transport(github_mock(calls))
    r = api.post(f"/incidents/{inc['id']}/approve", json={"approver": "muhammad", "note": "LGTM"})
    assert r.status_code == 200, r.text
    d = api.get(f"/incidents/{inc['id']}").json()
    assert d["status"] == "PR_CREATED"
    ref = next(c for c in calls if c[1].endswith("/git/refs"))
    assert ref[2]["sha"] == project["head_sha"]  # branch from the analysed commit
    put = next(c for c in calls if c[0] == "PUT")
    assert put[1] == "/repos/acme/shop/contents/services/orders/orders/pricing.py"
    assert put[3] == "Bearer ghp_test_token"
    pr = next(c for c in calls if c[1].endswith("/pulls"))
    assert "Approved by **muhammad**" in pr[2]["body"] and pr[2]["base"] == "main"
    assert not any("merge" in c[1] for c in calls)
    # Approving again is refused
    assert api.post(f"/incidents/{inc['id']}/approve", json={"approver": "x"}).status_code == 409


def test_invalid_edit_is_corrected_within_attempt(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [INVALID_FIX, GOOD_FIX_BUG1]}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "AWAITING_APPROVAL"
    fixer = next(s for s in d["steps"] if s["agent"] == "fixer")
    assert "REJECTED" in fixer["tool_calls"][0]["result_preview"]
    close(api, d["id"])


def test_needs_human_after_three_failed_attempts(api, server, project, remote_repo):
    fake = ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [USELESS_FIX_BUG1] * 3})
    llm.set_client(fake)
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert d["status"] == "NEEDS_HUMAN"
    assert [p["tests_passed"] for p in d["patches"]] == [False, False, False]
    assert api.post(f"/incidents/{d['id']}/approve", json={"approver": "x"}).status_code == 409
    close(api, d["id"])


def test_reject_requires_reason(api, server, project, remote_repo):
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]}))
    ship_real_error(server, remote_repo / "services" / "orders", bug=1)
    d = settle(api, latest_incident(api, "TypeError")["id"])
    assert api.post(f"/incidents/{d['id']}/reject", json={"approver": "lead", "reason": ""}).status_code == 422
    assert api.post(f"/incidents/{d['id']}/reject", json={"approver": "lead", "reason": "validate upstream"}).status_code == 200
    assert api.get(f"/incidents/{d['id']}").json()["status"] == "REJECTED"


def test_repeats_grouped_and_no_llm_key_fails_cleanly(api, server, project, remote_repo):
    # No LLM client override and no NEBIUS_API_KEY: incident is detected, then FAILED with a clear reason.
    for _ in range(3):
        api.post("/ingest/logs", json={"project": "orders", "source": "test", "lines": [json.dumps({
            "method": "GET", "path": "/orders/1",
            "traceback": 'Traceback (most recent call last):\n  File "/x/services/orders/orders/serializers.py", line 7, in serialize_order\n    "customer_id": record["customer_id"],\nKeyError: \'customer_id\'\n'})]})
    incs = [i for i in api.get("/incidents", params={"project": "orders"}).json() if i["exception_type"] == "KeyError"]
    assert len(incs) == 1
    d = settle(api, incs[0]["id"])
    assert d["status"] == "FAILED" and "NEBIUS_API_KEY" in d["status_reason"]


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

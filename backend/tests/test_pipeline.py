"""End-to-end pipeline tests: real detector, real tool loop, real sandboxed pytest on the demo
app, real approval gate; the LLM is scripted and GitHub is an httpx MockTransport."""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.tools import github
from fake_llm import GOOD_FIX_BUG1, INVALID_FIX, INVESTIGATE_BUG1, USELESS_FIX_BUG1, ScriptedLLM

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean():
    client.post("/demo/reset")
    yield
    llm.set_client(None)
    github.set_transport(None)


def wait_for(incident_id, statuses, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        d = client.get(f"/incidents/{incident_id}").json()
        if d["status"] in statuses and not d["running"]:
            return d
        time.sleep(0.3)
    raise AssertionError(f"timeout; last status {d['status']} {d['status_reason']}")


def trigger_bug1():
    r = client.post("/demo/trigger/1")
    assert r.json()["status_codes"] == [500]
    end = time.time() + 10
    while time.time() < end:
        incs = client.get("/incidents").json()
        if incs:
            return incs[0]
        time.sleep(0.2)
    raise AssertionError("no incident created")


def github_mock(record):
    def handler(req: httpx.Request):
        record.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        p = req.url.path
        if req.method == "GET" and p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "abc1234def"}})
        if req.method == "POST" and p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if req.method == "GET" and "/contents/" in p:
            return httpx.Response(200, json={"sha": "filesha"})
        if req.method == "PUT" and "/contents/" in p:
            return httpx.Response(200, json={"commit": {}})
        if req.method == "POST" and p.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/acme/agentops-demo/pull/7", "number": 7})
        return httpx.Response(404, json={"message": "unexpected"})
    return httpx.MockTransport(handler)


def test_bug1_full_loop_to_pull_request():
    fake = ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]})
    llm.set_client(fake)
    inc = trigger_bug1()
    assert inc["exception_type"] == "TypeError"
    assert inc["severity"] == "high"  # POST path
    assert inc["location"]["file"] == "orders/pricing.py"

    d = wait_for(inc["id"], {"AWAITING_APPROVAL", "NEEDS_HUMAN", "FAILED"})
    assert d["status"] == "AWAITING_APPROVAL", d["status_reason"]
    assert d["summary"] == "Orders API fails."
    assert d["root_cause"]["file"] == "orders/pricing.py"
    [patch] = d["patches"]
    assert patch["tests_passed"] is True
    ts = patch["test_summary"]
    assert ts["fixed"] == ["tests/test_orders.py::test_create_order_without_discount"]
    assert ts["regressions"] == []
    assert ts["runner"] == "subprocess"
    agents = [s["agent"] for s in d["steps"]]
    assert agents[:4] == ["detector", "detector", "investigator", "fixer"]
    assert "tester" in agents and agents[-1] == "approval_gate"
    inv = next(s for s in d["steps"] if s["agent"] == "investigator")
    assert [c["tool"] for c in inv["tool_calls"]] == ["read_file", "web_search", "submit_root_cause"]

    # Approve -> PR (mocked GitHub)
    calls = []
    github.set_transport(github_mock(calls))
    r = client.post(f"/incidents/{inc['id']}/approve", json={"approver": "muhammad", "note": "LGTM"})
    assert r.status_code == 200, r.text
    assert r.json()["pr_url"].endswith("/pull/7")
    d = client.get(f"/incidents/{inc['id']}").json()
    assert d["status"] == "PR_CREATED"
    assert d["decisions"][0]["decided_by"] == "muhammad"
    put = next(c for c in calls if c[0] == "PUT")
    assert put[1].endswith("/contents/demo-app/orders/pricing.py")
    pr = next(c for c in calls if c[1].endswith("/pulls"))
    assert "Approved by **muhammad**" in pr[2]["body"] and "Not merged automatically" in pr[2]["body"]
    assert not any(c[1].endswith("/merge") for c in calls)


def test_retry_after_failed_tests_then_success():
    fake = ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [INVALID_FIX, GOOD_FIX_BUG1, USELESS_FIX_BUG1, GOOD_FIX_BUG1]})
    llm.set_client(fake)
    inc = trigger_bug1()
    d = wait_for(inc["id"], {"AWAITING_APPROVAL", "NEEDS_HUMAN", "FAILED"})
    # attempt 1: INVALID edit is rejected inside the loop and the model retries with GOOD_FIX in the same attempt
    assert d["status"] == "AWAITING_APPROVAL"
    fixer_steps = [s for s in d["steps"] if s["agent"] == "fixer"]
    assert "REJECTED" in fixer_steps[0]["tool_calls"][0]["result_preview"]


def test_needs_human_after_three_failed_attempts():
    fake = ScriptedLLM({"investigator": list(INVESTIGATE_BUG1),
                        "fixer": [USELESS_FIX_BUG1, USELESS_FIX_BUG1, USELESS_FIX_BUG1]})
    llm.set_client(fake)
    inc = trigger_bug1()
    d = wait_for(inc["id"], {"AWAITING_APPROVAL", "NEEDS_HUMAN", "FAILED"})
    assert d["status"] == "NEEDS_HUMAN"
    assert [p["tests_passed"] for p in d["patches"]] == [False, False, False]
    assert client.post(f"/incidents/{inc['id']}/approve", json={"approver": "x"}).status_code == 409
    # Fixer received failing output on retries
    assert fake.calls.count({"agent": "fixer", "n_messages": 2, "model": fake.calls[-1]["model"]}) == 3


def test_reject_closes_incident():
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]}))
    inc = trigger_bug1()
    wait_for(inc["id"], {"AWAITING_APPROVAL"})
    assert client.post(f"/incidents/{inc['id']}/reject", json={"approver": "lead", "reason": ""}).status_code == 422
    r = client.post(f"/incidents/{inc['id']}/reject", json={"approver": "lead", "reason": "prefer validating codes upstream"})
    assert r.status_code == 200
    d = client.get(f"/incidents/{inc['id']}").json()
    assert d["status"] == "REJECTED" and d["decisions"][0]["reason"].startswith("prefer")


def test_duplicate_errors_grouped_into_one_incident():
    llm.set_client(ScriptedLLM({"investigator": list(INVESTIGATE_BUG1), "fixer": [GOOD_FIX_BUG1]}))
    client.post("/demo/trigger/1?repeat=3")
    time.sleep(1.5)
    incs = client.get("/incidents").json()
    assert len(incs) == 1 and incs[0]["occurrences"] == 3


def test_no_llm_key_fails_cleanly():
    inc = trigger_bug1()
    d = wait_for(inc["id"], {"FAILED"})
    assert "NEBIUS_API_KEY" in d["status_reason"]

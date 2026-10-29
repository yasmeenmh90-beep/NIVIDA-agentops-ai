"""Regression checks for backend review fixes; no external integrations called."""
import hashlib
import hmac
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.auth import verify_password
from app.config import get_settings
from app.routers import ingest, webhooks
from app.schemas import ProjectUpdate
from app.tools.sandbox import TestRun as Run, compare


def test_regression_test_must_actually_pass_after_fix():
    test = "tests/test_regression.py::test_bug"
    before = Run("subprocess", 1, passed=["existing"], failed=[test])
    after = Run("subprocess", 0, passed=["existing"])
    result = compare(before, after, ["tests/test_regression.py"], True)
    assert not result["verified"]
    assert result["fixed"] == []
    assert result["missing_tests"] == [test]


def test_previously_passing_test_cannot_disappear():
    before = Run("subprocess", 1, passed=["existing"], failed=["bug"])
    after = Run("subprocess", 0, passed=["bug"])
    assert not compare(before, after)["verified"]


def test_invalid_baseline_cannot_verify_patch():
    before = Run("subprocess", 2, failed=["bug"], collection_error=True)
    after = Run("subprocess", 0, passed=["bug"])
    assert not compare(before, after)["verified"]


@pytest.mark.parametrize("field", list(ProjectUpdate.model_fields))
def test_repository_patch_rejects_null(field):
    with pytest.raises(ValidationError, match="cannot be null"):
        ProjectUpdate.model_validate({field: None})
    assert ProjectUpdate.model_validate({}).model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize("stored", ["scrypt$not-hex$bad", "scrypt$00$00", "garbage"])
def test_corrupt_password_hash_returns_false(stored):
    assert verify_password("password", stored) is False


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ingest.router)
    app.include_router(webhooks.router)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("data,status", [(b"x" * 5_000_001, 413), (b" \n", 422)])
def test_invalid_log_upload_rejected_before_ingestion(client, monkeypatch, data, status):
    def unexpected(*args):
        pytest.fail("invalid uploads must not be dispatched")
    monkeypatch.setattr(ingest, "ingest_and_dispatch", unexpected)
    assert client.post("/ingest/file", data={"project": "demo"}, files={"file": ("logs.txt", data)}).status_code == status


def test_valid_log_upload_dispatched(client, monkeypatch):
    calls = []
    def dispatch(*args):
        calls.append(args)
        return {"new": [1]}
    monkeypatch.setattr(ingest, "ingest_and_dispatch", dispatch)
    assert client.post("/ingest/file", data={"project": "demo"}, files={"file": ("logs.txt", b"Error: broken\n")} ).status_code == 200
    assert calls == [("demo", ["Error: broken"], "logs.txt")]


def test_sentry_rejects_unsigned_request_even_with_open_auth(client, monkeypatch):
    monkeypatch.setattr(webhooks, "require_api_key", lambda request: None)
    assert client.post("/webhooks/sentry/demo", json={}).status_code == 401


@pytest.mark.parametrize("body", [b"broken", b"[]", b"null"])
def test_signed_webhook_invalid_json_returns_400(client, body):
    secret = get_settings().github_webhook_secret
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post("/webhooks/github", content=body, headers={"X-Hub-Signature-256": signature})
    assert response.status_code == 400


def test_github_public_email_is_not_used_without_verification(monkeypatch):
    import time
    import httpx
    from app.routers import accounts
    settings = get_settings()
    monkeypatch.setattr(settings, "github_oauth_client_id", "test-client")
    monkeypatch.setattr(settings, "github_oauth_client_secret", "test-secret")
    def github(request):
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": "test-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 321, "login": "unverified", "email": "someone@example.com"})
        return httpx.Response(200, json=[{"email": "someone@example.com", "primary": True, "verified": False}])
    accounts._states["unverified-test-state"] = time.time()
    accounts.set_transport(httpx.MockTransport(github))
    try:
        response = accounts.github_callback(code="code", state="unverified-test-state")
        assert "error=" in response.headers["location"]
        assert "verified" in response.headers["location"]
        assert "token=" not in response.headers["location"]
    finally:
        accounts.set_transport(None)

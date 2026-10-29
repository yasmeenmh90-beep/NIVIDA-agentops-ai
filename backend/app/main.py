"""AgentOps AI backend: FastAPI orchestrator for the five-stage incident pipeline."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import orchestrator
from .auth import auth_enabled, require_api_key
from .config import get_settings
from .db import init_db
from . import notify
from .routers import accounts, activity, incidents, ingest, metrics, projects, webhooks
from .tools.sandbox import resolve_mode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agentops")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    n = orchestrator.recover_interrupted()
    if n:
        log.warning("marked %d interrupted incident(s) as FAILED", n)
    from .auth import auth_enabled
    if not auth_enabled():
        log.warning("No API_KEYS and no user accounts yet: authentication is OFF until the first account "
                    "is created or API_KEYS is set. Do that before exposing this server.")
    if not settings.llm_configured:
        log.warning("NEBIUS_API_KEY is empty: incidents will be detected but not investigated.")
    yield


app = FastAPI(title="AgentOps AI", version="0.2.0", lifespan=lifespan,
              description="Autonomous incident engineer: detect → investigate → fix → test → human approval → PR")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
auth = [Depends(require_api_key)]
app.include_router(projects.router, dependencies=auth)
app.include_router(ingest.router, dependencies=auth)
app.include_router(incidents.router, dependencies=auth)
app.include_router(metrics.router, dependencies=auth)
app.include_router(activity.router, dependencies=auth)
app.include_router(accounts.router)  # sign-up / sign-in are public; the rest checks roles itself
app.include_router(webhooks.router)  # HMAC-authenticated, see routers/webhooks.py


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status", dependencies=auth)
def status():
    """Which integrations are configured (no secrets returned)."""
    s = get_settings()
    return {
        "llm": {"configured": s.llm_configured, "provider": "Nebius Token Factory",
                "reasoning_model": s.model_reasoning, "triage_model": s.model_triage},
        "tavily": {"configured": bool(s.tavily_api_key)},
        "auth_enabled": auth_enabled(),
        "accounts": {"github_sign_in": bool(s.github_oauth_client_id and s.github_oauth_client_secret),
                     "signup": s.signup_mode, "default_role": s.default_role},
        "webhooks": {"github": bool(s.github_webhook_secret), "sentry": bool(s.sentry_client_secret)},
        "notifications": notify.channels(),
        "require_regression_test": s.require_regression_test,
        "default_sandbox": resolve_mode(),
        "max_fix_attempts": s.max_fix_attempts,
        "max_revisions": s.max_revisions,
        "public_base_url": s.public_base_url,
        "notify_min_severity": s.notify_min_severity,
        "auto_investigate": s.auto_investigate,
    }

"""AgentOps AI backend: FastAPI orchestrator for the five-stage incident pipeline."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import orchestrator
from .auth import require_api_key
from .config import get_settings
from .db import init_db
from .routers import incidents, ingest, projects
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
    if not settings.api_key_list:
        log.warning("API_KEYS is empty: authentication is DISABLED. Set it before exposing this server.")
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
        "auth_enabled": bool(s.api_key_list),
        "default_sandbox": resolve_mode(),
        "max_fix_attempts": s.max_fix_attempts,
        "auto_investigate": s.auto_investigate,
    }

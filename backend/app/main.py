"""AgentOps AI backend: FastAPI orchestrator for the five-stage incident pipeline."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db
from .routers import demo, incidents, ingest
from .tools.sandbox import resolve_mode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()
init_db()

app = FastAPI(title="AgentOps AI", version="0.1.0",
              description="Autonomous incident engineer: detect → investigate → fix → test → human approval → PR")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ingest.router)
app.include_router(incidents.router)
app.include_router(demo.router)

if settings.embed_demo_app and not settings.demo_app_url:
    # The demo orders API also answers real HTTP traffic at /demo-app (e.g. traffic.py --base-url .../demo-app)
    app.mount("/demo-app", demo.load_embedded_app())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    """Which integrations are configured (no secrets returned)."""
    s = get_settings()
    return {
        "llm": {"configured": s.llm_configured, "provider": "Nebius Token Factory",
                "reasoning_model": s.model_reasoning, "triage_model": s.model_triage},
        "tavily": {"configured": bool(s.tavily_api_key)},
        "github": {"configured": s.github_configured, "repo": s.github_repo, "base": s.github_base_branch},
        "sandbox": {"mode": resolve_mode(), "requested": s.sandbox_mode},
        "target_repo": s.target_repo_path,
        "max_fix_attempts": s.max_fix_attempts,
        "auto_investigate": s.auto_investigate,
    }

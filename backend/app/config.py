"""Runtime configuration, read from environment variables or backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), extra="ignore")

    # --- LLM: NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible) ---
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    # Confirm exact IDs with `python scripts/check_nebius.py`
    model_reasoning: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    model_triage: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout_s: float = 120.0
    max_tool_rounds: int = 12
    max_tokens_per_incident: int = 400_000

    # --- Tavily web search (optional) ---
    tavily_api_key: str = ""

    # --- Security ---
    # Comma-separated keys accepted in `X-API-Key` / `Authorization: Bearer`. Empty = auth disabled (local dev only).
    api_keys: str = ""

    # --- Where AgentOps keeps its own clones and virtualenvs of registered repositories ---
    workspace_dir: str = str(BACKEND_DIR / "workspace")
    git_timeout_s: int = 180
    allow_local_repos: bool = False  # allow repo_url to be a local path / file:// URL (self-hosted mirrors, tests)
    setup_timeout_s: int = 900

    # --- Defaults for new projects (each project can override) ---
    default_test_command: str = "python -m pytest -q -p no:cacheprovider"
    sandbox_mode: str = "auto"  # auto | docker | subprocess
    sandbox_image: str = "agentops-sandbox:latest"
    sandbox_timeout_s: int = 300
    max_fix_attempts: int = 3
    max_patch_lines: int = 40  # application code lines changed
    max_test_lines: int = 120  # regression test lines added
    require_regression_test: bool = True  # a fix must ship a test that fails before and passes after
    max_revisions: int = 3  # reviewer-requested revisions per PR
    default_protected_paths: str = (
        ".github/,Dockerfile,docker-compose.yml,requirements.txt,requirements-dev.txt,pyproject.toml,"
        "setup.cfg,setup.py,.env,pytest.ini,tox.ini,conftest.py,alembic/,migrations/"
    )
    auto_investigate: bool = True
    worker_threads: int = 2

    # --- GitHub (only used after human approval) ---
    github_api_url: str = "https://api.github.com"
    github_webhook_secret: str = ""  # required for /webhooks/github
    # Who may trigger a PR revision with a review or an "/agentops" comment
    revision_author_associations: str = "OWNER,MEMBER,COLLABORATOR"

    # --- Sentry (optional) ---
    sentry_client_secret: str = ""  # internal integration client secret, verifies Sentry-Hook-Signature

    # --- Notifications (all optional) ---
    public_base_url: str = ""  # used for links in notifications, e.g. https://agentops.example.com
    slack_webhook_url: str = ""
    notify_webhook_url: str = ""
    notify_min_severity: str = "medium"  # for "new incident" alerts
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    notify_email_to: str = ""  # comma-separated

    # --- App ---
    database_url: str = f"sqlite:///{BACKEND_DIR / 'agentops.db'}"
    cors_origins: str = "http://localhost:5173"

    @property
    def llm_configured(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

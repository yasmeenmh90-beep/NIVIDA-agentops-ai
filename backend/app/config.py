"""Runtime configuration, read from environment variables or backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), extra="ignore")

    # --- LLM: NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible) ---
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    # Large model for investigation and fixing; small model for triage summaries.
    # Confirm exact IDs with `python scripts/check_nebius.py` (lists available Nemotron models).
    model_reasoning: str = "nvidia/nemotron-3-ultra-550b-a55b"
    model_triage: str = "nvidia/nemotron-3-nano-omni"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout_s: float = 120.0
    max_tool_rounds: int = 12
    max_tokens_per_incident: int = 400_000  # hard budget across all agent calls

    # --- Tavily web search (optional; Investigator works without it) ---
    tavily_api_key: str = ""

    # --- Target repository that AgentOps reads and patches ---
    target_repo_path: str = str(PROJECT_ROOT / "demo-app")
    test_command: str = "python -m pytest -q -p no:cacheprovider"
    sandbox_mode: str = "auto"  # auto | docker | subprocess
    sandbox_image: str = "agentops-sandbox:latest"
    sandbox_timeout_s: int = 120

    # --- Fix policy ---
    max_fix_attempts: int = 3
    max_patch_lines: int = 40
    protected_paths: str = ".github/,Dockerfile,docker-compose.yml,requirements.txt,pyproject.toml,setup.cfg,.env,pytest.ini,tests/conftest.py"
    auto_investigate: bool = True

    # --- GitHub PR creation (only after human approval) ---
    github_token: str = ""
    github_repo: str = ""  # "owner/name"
    github_base_branch: str = "main"
    github_path_prefix: str = "demo-app"  # where the target repo lives inside the GitHub repo ("" if it is the root)
    github_api_url: str = "https://api.github.com"

    # --- App ---
    database_url: str = f"sqlite:///{BACKEND_DIR / 'agentops.db'}"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    embed_demo_app: bool = True  # mount the demo orders API at /demo-app inside this process
    demo_app_url: str = ""  # used instead of the embedded app when set (e.g. http://localhost:8001)

    @property
    def llm_configured(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token and self.github_repo)

    @property
    def protected_list(self) -> list[str]:
        return [p.strip() for p in self.protected_paths.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

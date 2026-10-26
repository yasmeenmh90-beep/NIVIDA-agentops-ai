import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
_tmp = tempfile.mkdtemp(prefix="agentops-test-")

# Configure BEFORE the app is imported (settings are cached).
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "TARGET_REPO_PATH": str(ROOT / "demo-app"),
    "NEBIUS_API_KEY": "",
    "TAVILY_API_KEY": "",
    "GITHUB_TOKEN": "test-token",
    "GITHUB_REPO": "acme/agentops-demo",
    "GITHUB_PATH_PREFIX": "demo-app",
    "SANDBOX_MODE": "subprocess",
    "EMBED_DEMO_APP": "true",
    "DEMO_APP_URL": "",
    "AUTO_INVESTIGATE": "true",
})
sys.path.insert(0, str(BACKEND))

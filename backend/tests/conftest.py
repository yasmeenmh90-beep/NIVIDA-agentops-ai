import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_service"
_tmp = Path(tempfile.mkdtemp(prefix="agentops-test-"))

# Configure BEFORE the app is imported (settings are cached).
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "WORKSPACE_DIR": str(_tmp / "workspace"),
    "ALLOW_LOCAL_REPOS": "true",
    "API_KEYS": "test-key",
    "NEBIUS_API_KEY": "",
    "TAVILY_API_KEY": "",
    "SANDBOX_MODE": "subprocess",
    "AUTO_INVESTIGATE": "true",
    "GITHUB_TOKEN": "ghp_test_token",
})
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent / "clients" / "python"))

API = {"X-API-Key": "test-key"}


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture(scope="session")
def remote_repo() -> Path:
    """A real git repository (stand-in for GitHub) containing a service with known bugs, under services/orders."""
    repo = _tmp / "remote"
    shutil.copytree(FIXTURE, repo / "services" / "orders",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (repo / "README.md").write_text("sample monorepo\n")
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "initial import", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "store: rename customer_id -> customer_ref", cwd=repo)
    return repo

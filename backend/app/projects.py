"""Registered repositories: cloning, syncing and dependency setup.

AgentOps keeps its own clone of each repository under WORKSPACE_DIR and never pushes from
it. The only way code reaches the user's repository is a pull request opened after a human
approves. Before each investigation the clone is hard-reset to the latest default branch.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import dotenv_values

from .config import BACKEND_DIR, get_settings
from .context import ProjectContext
from .db import SessionLocal
from .models import Project, utcnow

log = logging.getLogger("agentops.projects")
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
GITHUB_RX = re.compile(r"github\.com[:/](?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


class ProjectError(RuntimeError):
    pass


def project_lock(project_id: int) -> threading.Lock:
    """One pipeline / sync at a time per project, so the clone is never changed under an agent."""
    with _locks_guard:
        return _locks.setdefault(project_id, threading.Lock())


def secret(name: str) -> str:
    """Read a secret from the process environment, falling back to backend/.env."""
    if not name:
        return ""
    if os.environ.get(name):
        return os.environ[name]
    env_file = BACKEND_DIR / ".env"
    return (dotenv_values(env_file).get(name) or "") if env_file.exists() else ""


def github_repo_from_url(url: str) -> str:
    m = GITHUB_RX.search(url.strip())
    return f"{m['owner']}/{m['repo']}" if m else ""


def validate_repo_url(url: str) -> None:
    url = url.strip()
    if url.startswith("https://"):
        return
    if get_settings().allow_local_repos and (url.startswith("file://") or url.startswith("/")):
        return
    raise ProjectError("repo_url must be an https:// URL (local paths need ALLOW_LOCAL_REPOS=true)")


def validate_name(name: str) -> None:
    if not NAME_RX.match(name):
        raise ProjectError("name must be lowercase letters, digits, '-' or '_' (max 100)")


def validate_app_path(app_path: str) -> str:
    p = (app_path or "").strip().strip("/")
    if p and (".." in p.split("/") or p.startswith("~")):
        raise ProjectError("app_path must be a relative subdirectory")
    return p


# ---------------- paths ----------------

def project_dir(project_id: int) -> Path:
    # Always absolute: tests run with cwd set to a temp dir, so a relative venv path would break.
    ws = Path(get_settings().workspace_dir).expanduser()
    if not ws.is_absolute():
        ws = BACKEND_DIR / ws
    return ws.resolve() / "projects" / str(project_id)


def checkout_dir(project_id: int) -> Path:
    return project_dir(project_id) / "repo"


def venv_dir(project_id: int) -> Path:
    return project_dir(project_id) / "venv"


def venv_python(project_id: int) -> Path:
    d = venv_dir(project_id)
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


# ---------------- git ----------------

def _git(args: list[str], cwd: Path | None, token: str = "", url: str = "") -> str:
    cmd = ["git"]
    if token and url.startswith("https://"):
        # Token goes in a per-command header, never into the stored remote URL.
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    cmd += args
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=get_settings().git_timeout_s, env=env)
    except subprocess.TimeoutExpired as e:
        raise ProjectError(f"git {args[0]} timed out") from e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        if token:
            msg = msg.replace(token, "***")
        raise ProjectError(f"git {args[0]} failed: {msg[-500:]}")
    return r.stdout.strip()


def sync(p: Project) -> str:
    """Clone or hard-reset AgentOps' copy to origin/<default_branch>. Returns the HEAD sha."""
    token = secret(p.github_token_env)
    repo = checkout_dir(p.id)
    url = p.repo_url[7:] if p.repo_url.startswith("file://") else p.repo_url
    if not (repo / ".git").exists():
        if repo.exists():
            shutil.rmtree(repo)
        repo.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--branch", p.default_branch, "--single-branch", url, str(repo)], None, token, url)
    else:
        _git(["fetch", "--prune", "origin", p.default_branch], repo, token, url)
        _git(["reset", "--hard", "FETCH_HEAD"], repo)
        _git(["clean", "-fdx"], repo)
    sha = _git(["rev-parse", "HEAD"], repo)
    with SessionLocal() as db:
        row = db.get(Project, p.id)
        row.head_sha = sha
        row.synced_at = utcnow()
        db.commit()
    return sha


def checkout(project_id: int, sha: str) -> None:
    """Detach AgentOps' clone at `sha` (used to revise a PR on the commit it was branched from)."""
    repo = checkout_dir(project_id)
    try:
        _git(["checkout", "-q", "--detach", sha], repo)
    except ProjectError:
        _git(["fetch", "-q", "origin", sha], repo)
        _git(["checkout", "-q", "--detach", sha], repo)
    _git(["clean", "-fdx", "-q"], repo)


# ---------------- dependency setup ----------------

def _run(cmd: str | list[str], cwd: Path, env: dict, log_lines: list[str]) -> None:
    shown = cmd if isinstance(cmd, str) else " ".join(shlex.quote(c) for c in cmd)
    log_lines.append(f"$ {shown}")
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str), capture_output=True, text=True,
                       timeout=get_settings().setup_timeout_s)
    log_lines.append(((r.stdout or "") + (r.stderr or ""))[-4000:])
    if r.returncode != 0:
        raise ProjectError(f"setup command failed (exit {r.returncode}): {shown}")


def _install(p: Project, app_root: Path, log_lines: list[str]) -> None:
    if p.python_env == "system":
        log_lines.append("python_env=system: using the server's interpreter, no install step")
        return
    vpy = venv_python(p.id)
    if not vpy.exists():
        _run([sys.executable, "-m", "venv", str(venv_dir(p.id))], app_root, dict(os.environ), log_lines)
    env = {**os.environ, "VIRTUAL_ENV": str(venv_dir(p.id)), "PATH": f"{vpy.parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.pop("PYTHONHOME", None)
    if p.install_command.strip():
        _run(p.install_command, app_root, env, log_lines)
    else:
        pip = [str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "-q"]
        found = False
        for req in ("requirements.txt", "requirements-dev.txt", "requirements/dev.txt", "requirements/test.txt"):
            if (app_root / req).is_file():
                _run(pip + ["-r", req], app_root, env, log_lines)
                found = True
        if not found and (app_root / "pyproject.toml").is_file():
            _run(pip + ["-e", "."], app_root, env, log_lines)
            found = True
        if not found:
            log_lines.append("no requirements*.txt or pyproject.toml found; installing pytest only")
    _run([str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "-q", "pytest"], app_root, env, log_lines)


def setup(project_id: int) -> None:
    """Clone/sync and install dependencies. Runs in a background thread; result is stored on the project."""
    with project_lock(project_id):
        with SessionLocal() as db:
            p = db.get(Project, project_id)
            p.setup_status = "running"
            p.setup_log = ""
            db.commit()
        log_lines: list[str] = []
        status = "ready"
        try:
            sha = sync(p)
            log_lines.append(f"checked out {p.default_branch} at {sha[:12]}")
            app_root = checkout_dir(project_id) / p.app_path if p.app_path else checkout_dir(project_id)
            if not app_root.is_dir():
                raise ProjectError(f"app_path '{p.app_path}' does not exist in the repository")
            _install(p, app_root, log_lines)
        except Exception as e:
            status = "failed"
            log_lines.append(f"ERROR: {e}")
            log.warning("setup failed for project %s: %s", project_id, e)
        with SessionLocal() as db:
            row = db.get(Project, project_id)
            row.setup_status = status
            row.setup_log = "\n".join(log_lines)[-20000:]
            db.commit()


def start_setup(project_id: int) -> None:
    threading.Thread(target=setup, args=(project_id,), daemon=True, name=f"setup-{project_id}").start()


# ---------------- context ----------------

def context_for(p: Project) -> ProjectContext:
    s = get_settings()
    checkout = checkout_dir(p.id)
    app_root = checkout / p.app_path if p.app_path else checkout
    if p.python_env == "venv":
        python, env_bin = str(venv_python(p.id)), str(venv_python(p.id).parent)
    else:
        python, env_bin = sys.executable, None
    protected = [x.strip() for x in (s.default_protected_paths + "," + (p.protected_paths or "")).split(",") if x.strip()]
    return ProjectContext(
        id=p.id, name=p.name, checkout=checkout, app_root=app_root, app_path=p.app_path,
        python=python, env_bin=env_bin,
        test_command=p.test_command or s.default_test_command,
        sandbox_mode=p.sandbox_mode or s.sandbox_mode,
        sandbox_image=p.sandbox_image or s.sandbox_image,
        protected=protected,
        github_repo=p.github_repo, github_token=secret(p.github_token_env),
        default_branch=p.default_branch, head_sha=p.head_sha,
    )


def load_context(project_id: int) -> ProjectContext:
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        if p is None:
            raise ProjectError(f"project {project_id} not found")
        return context_for(p)

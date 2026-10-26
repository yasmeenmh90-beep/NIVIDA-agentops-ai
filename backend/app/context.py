"""The project an agent is currently working on.

Agents and tools run in worker threads; each pipeline run binds its project here so the
repo tools, patch validator, sandbox and GitHub client all act on the right repository.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProjectContext:
    id: int
    name: str
    checkout: Path  # AgentOps-owned clone of the repository
    app_root: Path  # checkout / app_path: where code is read and tests run
    app_path: str  # "" or a subdirectory, used as the path prefix for PRs
    python: str  # interpreter used to run tests in subprocess mode
    env_bin: str | None  # venv bin dir to put first on PATH
    test_command: str
    sandbox_mode: str
    sandbox_image: str
    protected: list[str] = field(default_factory=list)
    github_repo: str = ""
    github_token: str = ""
    default_branch: str = "main"
    head_sha: str = ""


_current: contextvars.ContextVar[ProjectContext | None] = contextvars.ContextVar("agentops_project", default=None)


class NoProjectError(RuntimeError):
    pass


def current() -> ProjectContext:
    ctx = _current.get()
    if ctx is None:
        raise NoProjectError("no project bound to this operation")
    return ctx


@contextmanager
def use_project(ctx: ProjectContext):
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)

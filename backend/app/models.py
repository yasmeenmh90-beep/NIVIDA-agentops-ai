"""Database tables: incidents, agent steps, patches, decisions and an audit log."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Status(StrEnum):
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    FIXING = "FIXING"
    TESTING = "TESTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PR_CREATED = "PR_CREATED"
    REJECTED = "REJECTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"  # tests still failing after max attempts
    FAILED = "FAILED"  # pipeline error (LLM/API/sandbox)


OPEN_STATUSES = {
    Status.DETECTED, Status.INVESTIGATING, Status.FIXING, Status.TESTING,
    Status.AWAITING_APPROVAL, Status.NEEDS_HUMAN, Status.FAILED,
}
RUNNING_STATUSES = {Status.INVESTIGATING, Status.FIXING, Status.TESTING}


class Project(Base):
    """A real repository AgentOps watches. AgentOps owns its own clone; the user's repo is only changed via PRs."""

    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    repo_url: Mapped[str] = mapped_column(String(500))  # https://github.com/owner/repo(.git) or a local path / file:// URL
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    app_path: Mapped[str] = mapped_column(String(300), default="")  # subdirectory holding the Python app ("" = repo root)
    github_repo: Mapped[str] = mapped_column(String(200), default="")  # owner/name for PRs ("" = PRs disabled)
    github_token_env: Mapped[str] = mapped_column(String(100), default="GITHUB_TOKEN")  # env var holding the token
    test_command: Mapped[str] = mapped_column(String(500), default="")
    install_command: Mapped[str] = mapped_column(String(500), default="")  # "" = auto (requirements*.txt / pyproject)
    python_env: Mapped[str] = mapped_column(String(16), default="venv")  # venv | system
    sandbox_mode: Mapped[str] = mapped_column(String(16), default="")  # "" = global default
    sandbox_image: Mapped[str] = mapped_column(String(200), default="")
    protected_paths: Mapped[str] = mapped_column(Text, default="")  # extra, comma-separated
    setup_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|ready|failed
    setup_log: Mapped[str] = mapped_column(Text, default="")
    head_sha: Mapped[str] = mapped_column(String(64), default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), default="")  # code version the agents analysed
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default=Status.DETECTED)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    error_signature: Mapped[str] = mapped_column(String(300), index=True)
    exception_type: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(String(200), default="")
    service: Mapped[str] = mapped_column(String(120), default="")
    traceback: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[dict] = mapped_column(JSON, default=dict)  # {file, line, function}
    root_cause: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|error
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)  # [{tool, args, result_preview, ts}]
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Patch(Base):
    __tablename__ = "patches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    diff: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[dict] = mapped_column(JSON, default=dict)  # {path: new_content}
    tests_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    test_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    test_log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(16))  # approve|reject
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(120))
    pr_url: Mapped[str] = mapped_column(String(300), default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(120))  # agent name, "system" or a human
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

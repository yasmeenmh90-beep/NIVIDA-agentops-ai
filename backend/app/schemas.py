from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, model_validator


def _utc(v):
    """SQLite drops tzinfo; all stored times are UTC, so mark naive values as UTC."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


UTCDateTime = Annotated[datetime, BeforeValidator(_utc)]


class IngestIn(BaseModel):
    project: str  # project name (or numeric id as a string)
    source: str = ""
    lines: list[str] = Field(min_length=1, max_length=5000)


class ApproveIn(BaseModel):
    approver: str = Field(min_length=1, max_length=120)
    note: str = ""


class RejectIn(BaseModel):
    approver: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1)


class RevisionIn(BaseModel):
    author: str = Field(min_length=1, max_length=120)
    feedback: str = Field(min_length=1, max_length=4000)


class CancelIn(BaseModel):
    by: str = Field(min_length=1, max_length=120)


class IncidentOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    project_id: int
    commit_sha: str
    title: str
    summary: str
    severity: str
    status: str
    status_reason: str
    error_signature: str
    exception_type: str
    message: str
    endpoint: str
    service: str
    location: dict
    occurrences: int
    tokens_used: int
    pr_number: int | None = None
    pr_url: str = ""
    pr_branch: str = ""
    revisions: int = 0
    regression_of: int | None = None
    resolved_at: UTCDateTime | None = None
    first_seen: UTCDateTime
    last_seen: UTCDateTime
    updated_at: UTCDateTime


class StepOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    agent: str
    status: str
    input_summary: str
    output: dict | None
    tool_calls: list
    model: str
    prompt_tokens: int
    completion_tokens: int
    started_at: UTCDateTime
    finished_at: UTCDateTime | None


class PatchOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    attempt: int
    kind: str = "initial"
    feedback: str = ""
    regression_tests: list = []
    diff: str
    explanation: str
    tests_passed: bool | None
    test_summary: dict | None
    test_log: str
    created_at: UTCDateTime


class DecisionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    decision: str
    reason: str
    decided_by: str
    pr_url: str
    decided_at: UTCDateTime


class AuditOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    actor: str
    action: str
    detail: dict
    ts: UTCDateTime


class IncidentDetail(IncidentOut):
    traceback: str
    root_cause: dict | None
    similar_incidents: list = []
    running: bool = False
    steps: list[StepOut] = []
    patches: list[PatchOut] = []
    decisions: list[DecisionOut] = []
    audit: list[AuditOut] = []


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_url: str = Field(min_length=1, max_length=500)
    default_branch: str = "main"
    app_path: str = ""
    github_repo: str | None = None  # owner/name; derived from a github.com repo_url when omitted
    github_token_env: str = "GITHUB_TOKEN"
    test_command: str = ""
    install_command: str = ""
    python_env: str = Field(default="venv", pattern="^(venv|system)$")
    sandbox_mode: str = Field(default="", pattern="^(|auto|docker|subprocess)$")
    sandbox_image: str = ""
    protected_paths: str = ""


class ProjectUpdate(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, value):
        if isinstance(value, dict) and any(v is None for k, v in value.items() if k in cls.model_fields):
            raise ValueError("repository fields cannot be null; omit unchanged fields or use an empty string")
        return value

    default_branch: str | None = None
    app_path: str | None = None
    github_repo: str | None = None
    github_token_env: str | None = None
    test_command: str | None = None
    install_command: str | None = None
    python_env: str | None = Field(default=None, pattern="^(venv|system)$")
    sandbox_mode: str | None = Field(default=None, pattern="^(|auto|docker|subprocess)$")
    sandbox_image: str | None = None
    protected_paths: str | None = None


class ProjectOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    repo_url: str
    default_branch: str
    app_path: str
    github_repo: str
    github_token_env: str
    test_command: str
    install_command: str
    python_env: str
    sandbox_mode: str
    sandbox_image: str
    protected_paths: str
    setup_status: str
    head_sha: str
    synced_at: UTCDateTime | None
    created_at: UTCDateTime


class ProjectDetail(ProjectOut):
    setup_log: str
    github_token_present: bool = False
    effective_test_command: str = ""
    effective_sandbox: str = ""


class EventIn(BaseModel):
    """One structured error event, e.g. from a logging handler or an APM webhook relay."""
    traceback: str = Field(min_length=1)
    method: str = ""
    path: str = ""
    service: str = ""
    message: str = ""


class EventsIn(BaseModel):
    project: str
    source: str = ""
    events: list[EventIn] = Field(min_length=1, max_length=1000)

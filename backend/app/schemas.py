from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field


def _utc(v):
    """SQLite drops tzinfo; all stored times are UTC, so mark naive values as UTC."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


UTCDateTime = Annotated[datetime, BeforeValidator(_utc)]


class IngestIn(BaseModel):
    source: str = ""
    lines: list[str] = Field(min_length=1, max_length=5000)


class ApproveIn(BaseModel):
    approver: str = Field(min_length=1, max_length=120)
    note: str = ""


class RejectIn(BaseModel):
    approver: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1)


class IncidentOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
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
    running: bool = False
    steps: list[StepOut] = []
    patches: list[PatchOut] = []
    decisions: list[DecisionOut] = []
    audit: list[AuditOut] = []

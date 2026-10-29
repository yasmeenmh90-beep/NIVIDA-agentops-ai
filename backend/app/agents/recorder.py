"""Persists each agent step and its tool calls as they happen, so the dashboard updates live."""

from __future__ import annotations

import json

from .. import cancel
from ..config import get_settings
from ..db import SessionLocal
from ..models import AgentStep, AuditEvent, Incident, utcnow

PREVIEW = 1200


class BudgetExceeded(RuntimeError):
    pass


class StepRecorder:
    def __init__(self, incident_id: int, agent: str, input_summary: str = "", model: str = ""):
        self.incident_id = incident_id
        self.agent = agent
        with SessionLocal() as db:
            step = AgentStep(incident_id=incident_id, agent=agent, input_summary=input_summary, model=model, tool_calls=[])
            db.add(step)
            db.commit()
            self.step_id = step.id
            self._base_tokens = db.get(Incident, incident_id).tokens_used

    def tool_call(self, name: str, args: dict, result: str) -> None:
        cancel.check(self.incident_id)  # stop between tool calls if a human cancelled the run
        with SessionLocal() as db:
            step = db.get(AgentStep, self.step_id)
            calls = list(step.tool_calls or [])
            calls.append({
                "tool": name,
                "args": {k: (v if len(json.dumps(v, default=str)) < 600 else "<large>") for k, v in (args or {}).items()},
                "result_preview": (result or "")[:PREVIEW],
                "ts": utcnow().isoformat(),
            })
            step.tool_calls = calls
            db.add(AuditEvent(incident_id=self.incident_id, actor=self.agent, action=f"tool:{name}",
                              detail={"args": step.tool_calls[-1]["args"]}))
            db.commit()

    def budget_check(self, tokens_in_step: int) -> None:
        cancel.check(self.incident_id)  # also checked once per model round
        limit = get_settings().max_tokens_per_incident
        if self._base_tokens + tokens_in_step > limit:
            raise BudgetExceeded(f"token budget exceeded ({self._base_tokens + tokens_in_step} > {limit})")

    def finish(self, output: dict, prompt_tokens: int = 0, completion_tokens: int = 0, status: str = "done") -> None:
        with SessionLocal() as db:
            step = db.get(AgentStep, self.step_id)
            step.output = output
            step.status = status
            step.prompt_tokens = prompt_tokens
            step.completion_tokens = completion_tokens
            step.finished_at = utcnow()
            inc = db.get(Incident, self.incident_id)
            inc.tokens_used = (inc.tokens_used or 0) + prompt_tokens + completion_tokens
            inc.updated_at = utcnow()
            db.commit()

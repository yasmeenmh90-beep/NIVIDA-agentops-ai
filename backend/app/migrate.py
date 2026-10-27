"""Additive schema migration: adds columns that newer versions introduced to existing tables.

Only ADD COLUMN (never drop/rename), so existing data is kept. Enough for this project's
SQLite/Postgres deployments; switch to Alembic if the schema starts changing in other ways.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import JSON, Boolean, DateTime, Integer, inspect, select, text

from .db import Base, engine

log = logging.getLogger("agentops.migrate")


def _ddl_default(col) -> str:
    d = col.default.arg if col.default is not None and not callable(col.default.arg) else None
    if isinstance(col.type, Boolean):
        return "" if d is None else f" DEFAULT {'1' if d else '0'}"
    if isinstance(col.type, Integer):
        return "" if d is None else f" DEFAULT {int(d)}"
    if isinstance(col.type, DateTime) or d is None:
        return ""
    return " DEFAULT '" + str(d).replace("'", "''") + "'"


def upgrade() -> list[str]:
    insp = inspect(engine)
    added = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue  # create_all makes new tables
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                if isinstance(col.type, JSON) and engine.dialect.name == "sqlite":
                    coltype = "JSON"
                default = col.default.arg if col.default is not None else None
                if callable(default):  # e.g. default=list / dict
                    default = default.__name__ if hasattr(default, "__name__") else None
                ddl = f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {coltype}'
                if isinstance(col.type, JSON):
                    ddl += " DEFAULT '[]'" if default == "list" else " DEFAULT '{}'" if default == "dict" else ""
                else:
                    ddl += _ddl_default(col)
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.info("schema upgraded: added %s", ", ".join(added))
        _backfill()
    return added


def _backfill() -> None:
    """Fill PR fields for incidents approved before these columns existed."""
    from .db import SessionLocal
    from .models import AgentStep, Decision, Incident

    with SessionLocal() as db:
        for d in db.scalars(select(Decision).where(Decision.decision == "approve", Decision.pr_url != "")).all():
            inc = db.get(Incident, d.incident_id)
            if inc and not inc.pr_number:
                m = re.search(r"/pull/(\d+)", d.pr_url)
                inc.pr_url = d.pr_url
                inc.pr_number = int(m.group(1)) if m else None
                step = db.scalars(select(AgentStep).where(AgentStep.incident_id == inc.id,
                                                          AgentStep.agent == "approval_gate")).first()
                inc.pr_branch = ((step.output or {}).get("branch") if step else "") or ""
        db.commit()

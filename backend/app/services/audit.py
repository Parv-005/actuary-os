"""Audit service (§18) — the single code path for every persisted event."""
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.utils.logging import json_log


def record_event(
    session: Session,
    *,
    workflow_id: Any,
    actor_type: str,
    actor: str,
    action: str,
    from_status: str | None = None,
    to_status: str | None = None,
    entity_type: str = "",
    entity_id: Any = None,
    summary: str = "",
    details: dict | None = None,
) -> AuditEvent:
    """Append one audit event. Commit is the caller's responsibility (§17:
    stage commit = agent_runs + outputs + audit in one transaction)."""
    evt = AuditEvent(
        workflow_id=workflow_id,
        actor_type=actor_type,
        actor=actor,
        action=action,
        from_status=from_status,
        to_status=to_status,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        details=details or {},
    )
    session.add(evt)
    json_log(
        "audit_event",
        workflow_id=str(workflow_id),
        actor=actor,
        action=action,
        from_status=from_status,
        to_status=to_status,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
    )
    return evt

"""Activity router (§10/§18): audit timeline + agent-run table for a workflow."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AgentRun, AuditEvent, Workflow

router = APIRouter(prefix="/workflows", tags=["activity"])


@router.get("/{workflow_id}/audit-log")
def get_audit_log(
    workflow_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    after: str | None = Query(default=None,
                              description="event-id cursor; returns events "
                                          "strictly after it in timeline order"),
    session: Session = Depends(get_session),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    order = (AuditEvent.created_at, AuditEvent.id)
    q = select(AuditEvent).where(AuditEvent.workflow_id == wf.id)
    if after:
        # id cursor (timestamps collide for same-transaction events)
        try:
            cursor = session.get(AuditEvent, uuid.UUID(str(after)))
        except (ValueError, AttributeError):
            cursor = None
        if cursor is None or cursor.workflow_id != wf.id:
            raise HTTPException(400, f"bad after cursor: {after!r}",
                                {"code": "bad_cursor"})
        q = q.where(tuple_(*order) > (cursor.created_at, cursor.id))
    rows = session.execute(
        q.order_by(*order).limit(limit)).scalars().all()
    return {"events": [
        {"id": str(e.id),
         "ts": e.created_at.isoformat() if e.created_at else None,
         "actor_type": e.actor_type, "actor": e.actor, "action": e.action,
         "from_status": e.from_status, "to_status": e.to_status,
         "entity_type": e.entity_type,
         "entity_id": str(e.entity_id) if e.entity_id else None,
         "summary": e.summary}
        for e in rows]}


@router.get("/{workflow_id}/agent-runs")
def get_agent_runs(
    workflow_id: str,
    session: Session = Depends(get_session),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    rows = session.execute(
        select(AgentRun).where(AgentRun.workflow_id == wf.id)
        .order_by(AgentRun.started_at)).scalars().all()
    return {"runs": [
        {"id": str(r.id), "agent": r.agent, "stage": r.stage,
         "attempt": r.attempt, "status": r.status,
         "duration_ms": r.duration_ms, "llm_calls": r.llm_calls,
         "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
         "cost_usd": float(r.cost_usd) if r.cost_usd is not None else 0.0,
         "error": r.error,
         "started_at": r.started_at.isoformat() if r.started_at else None,
         "finished_at": (r.finished_at.isoformat()
                         if r.finished_at else None)}
        for r in rows]}

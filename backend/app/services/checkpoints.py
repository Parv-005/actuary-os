"""Checkpoints service (§12): raise/resolve human checkpoints + decision
validation + decision→state application. Every human action flows through here."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import File, HumanCheckpoint, HumanDecision, Workflow
from app.orchestrator import states
from app.utils.logging import json_log

DECISION_ENUMS = {
    "accept", "reject", "override", "investigate_further", "request_revision",
    "approve", "select_file", "accept_exception", "reject_data", "request_rerun",
    "monitor", "no_change_required", "confirm_mapping", "ignore_column",
    "review_assumption", "escalate", "comment",
}

RATIONALE_REQUIRED = {"reject", "override", "accept_exception"}
RATIONALE_MIN = 20


def raise_checkpoint(
    session: Session,
    wf: Workflow,
    *,
    checkpoint_type: str,
    severity: str,
    blocking: bool,
    title: str,
    context: dict,
    options: list[dict],
) -> HumanCheckpoint:
    existing = session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.workflow_id == wf.id,
            HumanCheckpoint.checkpoint_type == checkpoint_type,
            HumanCheckpoint.status == "pending",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    cp = HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type=checkpoint_type, severity=severity,
        blocking=blocking, title=title, context=context, options=options,
    )
    session.add(cp)
    session.flush()
    record_cp_event(session, wf, cp, action="checkpoint_raised")
    return cp


def record_cp_event(session: Session, wf: Workflow, cp: HumanCheckpoint, action: str) -> None:
    from app.services.audit import record_event

    record_event(
        session, workflow_id=wf.id,
        actor_type="agent", actor="orchestrator", action=action,
        entity_type="human_checkpoint", entity_id=cp.id,
        summary=f"{cp.checkpoint_type}: {cp.title}",
        details={"severity": cp.severity, "blocking": cp.blocking},
    )


def get_pending(session: Session, workflow_id: Any) -> list[HumanCheckpoint]:
    return list(
        session.execute(
            select(HumanCheckpoint)
            .where(HumanCheckpoint.workflow_id == workflow_id,
                   HumanCheckpoint.status == "pending")
            .order_by(HumanCheckpoint.raised_at)
        ).scalars().all()
    )


def resolve(
    session: Session,
    wf: Workflow,
    cp: HumanCheckpoint,
    decision_row: HumanDecision,
    actor_name: str,
) -> None:
    cp.status = "resolved"
    cp.resolved_at = cp.resolved_at or datetime.now(UTC)
    cp.resolved_by = decision_row.decided_by
    record_cp_event(session, wf, cp, action="checkpoint_resolved")


def validate_decision(decision: str, rationale: str) -> str | None:
    """Returns an error message or None if valid."""
    if decision not in DECISION_ENUMS:
        return f"unknown decision: {decision}"
    if decision in RATIONALE_REQUIRED and len(rationale.strip()) < RATIONALE_MIN:
        return (f"rationale of at least {RATIONALE_MIN} characters is required "
                f"for '{decision}'")
    return None


def apply_cp1_decision(
    session: Session,
    wf: Workflow,
    cp: HumanCheckpoint,
    decision: str,
    payload: dict,
    actor: Any,
) -> str:
    """Apply a CP-1 (input exception) decision. Returns next workflow status."""
    if decision == "select_file":
        file_id = payload.get("file_id")
        if not file_id:
            raise ValueError("select_file requires payload.file_id")
        chosen = session.get(File, uuid.UUID(str(file_id)))
        if chosen is None or chosen.workflow_id != wf.id:
            raise ValueError("file_id does not belong to this workflow")
        kind = chosen.kind
        for f in session.execute(
            select(File).where(File.workflow_id == wf.id, File.kind == kind)
        ).scalars().all():
            f.role = "primary" if f.id == chosen.id else "superseded"
        states.apply_transition(session, wf, states.INGESTING,
                                actor_type="human", actor=actor.name,
                                reason=f"CP-1: selected {chosen.filename} as authoritative {kind}")
        return wf.status
    if decision == "reject_data":
        states.apply_transition(session, wf, states.REJECTED,
                                actor_type="human", actor=actor.name,
                                reason="CP-1: data rejected")
        return wf.status
    if decision == "request_rerun":
        states.apply_transition(session, wf, states.INGESTING,
                                actor_type="human", actor=actor.name,
                                reason="CP-1: re-run requested")
        return wf.status
    raise ValueError(f"decision '{decision}' is not valid for input_exception checkpoint")


def log_decision(session: Session, wf: Workflow, decision_row: HumanDecision) -> None:
    json_log(
        "human_decision", workflow_id=str(wf.id), decision=decision_row.decision,
        checkpoint=str(decision_row.checkpoint_id) if decision_row.checkpoint_id else None,
    )

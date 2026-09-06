"""Workflow state machine (§7): constants, ALLOWED_TRANSITIONS, guards.

Single writer: all transitions go through apply_transition() — tested illegal
transitions raise; every transition writes an audit event.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Workflow
from app.services.audit import record_event
from app.utils.logging import json_log

CREATED = "CREATED"
INPUT_WAIT = "INPUT_WAIT"
INGESTING = "INGESTING"
VALIDATING = "VALIDATING"
VALIDATED = "VALIDATED"
ANALYZING = "ANALYZING"
ANALYZED = "ANALYZED"
INVESTIGATING = "INVESTIGATING"
INSIGHTS_READY = "INSIGHTS_READY"
REPORTING = "REPORTING"
QA = "QA"
WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
APPROVED = "APPROVED"
COMPLETED = "COMPLETED"
BLOCKED = "BLOCKED"
RETRYING = "RETRYING"
FAILED = "FAILED"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"

TERMINAL = {COMPLETED, REJECTED, CANCELLED}
PAUSED = {BLOCKED, WAITING_FOR_HUMAN, FAILED}
# RUNNABLE: every non-terminal state the engine may advance FROM, including
# the "resting" states between stages (VALIDATED -> ANALYZING, ANALYZED ->
# INVESTIGATING). Missing VALIDATED here silently stranded post-validation
# workflows (run_workflow returned immediately) — caught by Phase 9.
RUNNABLE = {
    INGESTING, VALIDATING, VALIDATED, ANALYZING, ANALYZED, INVESTIGATING,
    INSIGHTS_READY, REPORTING, QA, RETRYING,
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    CREATED: {INPUT_WAIT, CANCELLED},
    INPUT_WAIT: {INGESTING, CANCELLED},
    INGESTING: {VALIDATING, BLOCKED, FAILED, CANCELLED},
    # VALIDATING/VALIDATED -> INGESTING: checkpoint-driven re-standardization
    # (CP-3 confirm_mapping) + data-prep exception recovery (§10 decision map)
    VALIDATING: {VALIDATED, BLOCKED, FAILED, INGESTING, CANCELLED},
    VALIDATED: {ANALYZING, INGESTING, CANCELLED},
    ANALYZING: {ANALYZED, FAILED, CANCELLED},
    ANALYZED: {INVESTIGATING, CANCELLED},
    INVESTIGATING: {INSIGHTS_READY, FAILED, CANCELLED},
    INSIGHTS_READY: {REPORTING, WAITING_FOR_HUMAN, CANCELLED},
    REPORTING: {QA, FAILED, CANCELLED},
    QA: {WAITING_FOR_HUMAN, REPORTING, FAILED, CANCELLED},
    WAITING_FOR_HUMAN: {APPROVED, REPORTING, REJECTED, CANCELLED,
                        INVESTIGATING},
    # ^ INVESTIGATING: CP-4 investigate_further re-runs insight (bounded 1x)
    APPROVED: {COMPLETED},
    # BLOCKED -> VALIDATED: CP-2 accept_exception — validation is complete,
    # the exception is carried into the report (§10 decision map). BLOCKED ->
    # VALIDATING covers re-runs (CP-2 request_rerun, prep-blocker recovery).
    BLOCKED: {INGESTING, VALIDATING, VALIDATED, REJECTED, CANCELLED},
    RETRYING: {
        INGESTING, VALIDATING, ANALYZING, INVESTIGATING,
        REPORTING, QA, FAILED, CANCELLED,
    },
    FAILED: {RETRYING, CANCELLED},
    REJECTED: set(),
    COMPLETED: set(),
    CANCELLED: set(),
}


class IllegalTransition(Exception):
    pass


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


def apply_transition(
    session: Session,
    wf: Workflow,
    to_status: str,
    *,
    actor_type: str = "system",
    actor: str = "orchestrator",
    reason: str = "",
) -> Workflow:
    if not can_transition(wf.status, to_status):
        raise IllegalTransition(f"{wf.status} -> {to_status} not allowed")
    frm = wf.status
    wf.status = to_status
    wf.updated_at = datetime.now(UTC)
    record_event(
        session,
        workflow_id=wf.id,
        actor_type=actor_type,
        actor=actor,
        action=f"transition_{frm.lower()}_to_{to_status.lower()}",
        from_status=frm,
        to_status=to_status,
        entity_type="workflow",
        entity_id=wf.id,
        summary=reason or f"{frm} -> {to_status}",
    )
    json_log("transition", workflow_id=str(wf.id), **{"from": frm, "to": to_status})
    return wf

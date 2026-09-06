"""Decisions router (§10/§12): the ONLY path out of BLOCKED/WAITING_FOR_HUMAN.
Covers CP-1 (input exception), CP-3 (schema mapping, yellow), the data-prep
validation blocker, and CP-2 (validation blocker). Later phases extend further."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.actor import get_current_actor
from app.db import get_session
from app.models import (
    AgentRun,
    DatasetVersion,
    HumanCheckpoint,
    HumanDecision,
    Metric,
    User,
    ValidationResult,
    Workflow,
)
from app.orchestrator import engine, states
from app.schemas.workflow import DecisionRequest
from app.services import checkpoints as cp_svc
from app.services.audit import record_event

router = APIRouter(prefix="/workflows", tags=["decisions"])

CP1_DECISIONS = {"select_file", "reject_data", "request_rerun"}
CP3_DECISIONS = {"confirm_mapping", "ignore_column"}
PREP_BLOCKER_DECISIONS = {"accept_exception", "request_rerun", "reject_data"}

RESTDANDARDIZE_ELIGIBLE = {states.INGESTING, states.VALIDATING, states.VALIDATED}


def _reset_for_restandardize(session: Session, wf: Workflow) -> None:
    """confirm_mapping: drop prep/validation/analysis outputs so the engine
    re-runs them (stale metrics must not survive: resume skips succeeded
    stages)."""
    for stage in ("data_prep", "validation", "analysis"):
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == stage))
    session.execute(delete(DatasetVersion).where(DatasetVersion.workflow_id == wf.id))
    session.execute(delete(ValidationResult).where(ValidationResult.workflow_id == wf.id))
    session.execute(delete(Metric).where(Metric.workflow_id == wf.id))
    wf.stage = "data_prep"
    wf.error = None
    if wf.status != states.INGESTING:
        states.apply_transition(session, wf, states.INGESTING, actor_type="human",
                                actor="checkpoint_decision",
                                reason="CP-3 confirm_mapping — re-standardize")


def _apply_cp3(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User) -> str:
    if body.decision == "confirm_mapping":
        if wf.status not in RESTDANDARDIZE_ELIGIBLE:
            raise HTTPException(409, "too late to remap (analysis already ran)",
                                {"code": "too_late"})
        column = body.payload.get("column")
        canonical = body.payload.get("canonical")
        if not column or not canonical:
            raise HTTPException(400, "confirm_mapping requires payload.column and "
                                     "payload.canonical", {"code": "bad_payload"})
        cfg = dict(wf.config or {})
        overrides = dict(cfg.get("column_overrides", {}))
        overrides[column] = canonical
        cfg["column_overrides"] = overrides
        wf.config = cfg
        _reset_for_restandardize(session, wf)
    else:  # ignore_column
        cfg = dict(wf.config or {})
        ignored = list(cfg.get("ignored_columns", []))
        for col in body.payload.get("columns", [body.payload.get("column")]):
            if col and col not in ignored:
                ignored.append(col)
        cfg["ignored_columns"] = ignored
        wf.config = cfg
    return wf.status


def _mark_blockers_accepted(session: Session, wf: Workflow, decision_id,
                            rationale: str, actor: User) -> int:
    """CP-2 accept: every BLOCKER validation check -> ACCEPTED_EXCEPTION."""
    rows = session.execute(select(ValidationResult).where(
        ValidationResult.workflow_id == wf.id,
        ValidationResult.status == "BLOCKER")).scalars().all()
    now = datetime.now(UTC)
    for r in rows:
        r.status = "ACCEPTED_EXCEPTION"
        r.resolved_by = actor.id
        r.resolved_at = now
        r.resolution = {"decision_id": str(decision_id), "rationale": rationale,
                        "decided_by": actor.name, "decided_at": now.isoformat()}
    return len(rows)


def _apply_cp2(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User, decision_id) -> str:
    """CP-2 (§12): accept carries the exception into the report; re-run
    re-executes validation only (datasets are kept — results upsert)."""
    if body.decision == "accept_exception":
        n = _mark_blockers_accepted(session, wf, decision_id,
                                    body.rationale.strip(), actor)
        cfg = dict(wf.config or {})
        accepted = list(cfg.get("accepted_exceptions", []))
        accepted.append({
            "source": "validation", "decision_id": str(decision_id),
            "note": body.rationale.strip(),
        })
        cfg["accepted_exceptions"] = accepted
        wf.config = cfg
        states.apply_transition(session, wf, states.VALIDATED, actor_type="human",
                                actor=actor.name,
                                reason=f"CP-2 accepted ({n} checks) — carried "
                                       f"into report Exceptions")
    elif body.decision == "request_rerun":
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == "validation"))
        states.apply_transition(session, wf, states.VALIDATING, actor_type="human",
                                actor=actor.name, reason="CP-2: re-run validation")
    else:  # reject_data
        states.apply_transition(session, wf, states.REJECTED, actor_type="human",
                                actor=actor.name, reason="CP-2: data rejected")
    return wf.status


def _apply_prep_blocker(session: Session, wf: Workflow, body: DecisionRequest,
                        actor: User, cp: HumanCheckpoint,
                        decision_id) -> str:
    if (cp.context or {}).get("stage") == "validation":
        return _apply_cp2(session, wf, body, actor, decision_id)
    if body.decision == "accept_exception":
        cfg = dict(wf.config or {})
        accepted = list(cfg.get("accepted_exceptions", []))
        accepted.append({
            "source": "data_prep", "decision_id": str(decision_id),
            "note": body.rationale or "accepted by actuary",
        })
        cfg["accepted_exceptions"] = accepted
        wf.config = cfg
        states.apply_transition(session, wf, states.VALIDATING, actor_type="human",
                                actor=actor.name,
                                reason="data-prep blocker accepted as exception")
    elif body.decision == "request_rerun":
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == "data_prep"))
        states.apply_transition(session, wf, states.INGESTING, actor_type="human",
                                actor=actor.name, reason="re-run data prep")
    else:  # reject_data
        states.apply_transition(session, wf, states.REJECTED, actor_type="human",
                                actor=actor.name, reason="data rejected at prep blocker")
    return wf.status


@router.post("/{workflow_id}/decisions", status_code=202)
def post_decision(
    workflow_id: str,
    body: DecisionRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(get_current_actor),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")

    err = cp_svc.validate_decision(body.decision, body.rationale)
    if err:
        code = "rationale_required" if "rationale" in err else "bad_decision"
        raise HTTPException(422, err, {"code": code})

    if not body.checkpoint_id:
        raise HTTPException(400, "checkpoint_id required", {"code": "bad_target"})
    cp = session.get(HumanCheckpoint, body.checkpoint_id)
    if cp is None or cp.workflow_id != wf.id:
        raise HTTPException(400, "checkpoint does not belong to this workflow",
                            {"code": "bad_target"})
    if cp.status != "pending":
        raise HTTPException(409, "checkpoint already resolved", {"code": "not_pending"})

    cp_type = cp.checkpoint_type
    if cp_type == "input_exception":
        if body.decision not in CP1_DECISIONS:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for input_exception",
                                {"code": "bad_decision"})
    elif cp_type == "schema_mapping":
        if body.decision not in CP3_DECISIONS:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for schema_mapping",
                                {"code": "bad_decision"})
    elif cp_type == "validation_blocker":
        if body.decision not in PREP_BLOCKER_DECISIONS:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for validation_blocker",
                                {"code": "bad_decision"})
    else:
        raise HTTPException(400, f"checkpoint type '{cp_type}' not yet supported",
                            {"code": "not_implemented"})

    row = HumanDecision(
        workflow_id=wf.id, checkpoint_id=cp.id, decision=body.decision,
        rationale=body.rationale.strip(), payload=body.payload, decided_by=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(session, workflow_id=wf.id, actor_type="human", actor=actor.name,
                 action=f"decision_{body.decision}", entity_type="human_decision",
                 entity_id=row.id,
                 summary=body.rationale[:200] or body.decision,
                 details={"checkpoint": str(cp.id), "payload": body.payload})

    if cp_type == "input_exception":
        new_status = cp_svc.apply_cp1_decision(session, wf, cp, body.decision,
                                               body.payload, actor)
    elif cp_type == "schema_mapping":
        new_status = _apply_cp3(session, wf, body, actor)
    else:
        new_status = _apply_prep_blocker(session, wf, body, actor, cp, row.id)

    cp_svc.resolve(session, wf, cp, row, actor.name)
    session.commit()
    cp_svc.log_decision(session, wf, row)

    if wf.status in states.RUNNABLE:
        engine.launch(wf.id)
    return {"workflow_status": new_status, "applied": [body.decision]}

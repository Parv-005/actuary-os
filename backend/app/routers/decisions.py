"""Decisions router (§10/§12): the ONLY path out of BLOCKED/WAITING_FOR_HUMAN.
Phase 6 implements CP-1 (input exception); later phases extend the mapping."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.actor import get_current_actor
from app.db import get_session
from app.models import HumanCheckpoint, HumanDecision, User, Workflow
from app.orchestrator import engine
from app.schemas.workflow import DecisionRequest
from app.services import checkpoints as cp_svc
from app.services.audit import record_event

router = APIRouter(prefix="/workflows", tags=["decisions"])

CP1_DECISIONS = {"select_file", "reject_data", "request_rerun"}


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
        raise HTTPException(422, err, {"code": "rationale_required"
                                       if "rationale" in err else "bad_decision"})

    if not body.checkpoint_id:
        raise HTTPException(400, "checkpoint_id required (Phase 6 supports checkpoints)",
                            {"code": "bad_target"})
    cp = session.get(HumanCheckpoint, body.checkpoint_id)
    if cp is None or cp.workflow_id != wf.id:
        raise HTTPException(400, "checkpoint does not belong to this workflow",
                            {"code": "bad_target"})
    if cp.status != "pending":
        raise HTTPException(409, "checkpoint already resolved", {"code": "not_pending"})

    if cp.checkpoint_type == "input_exception":
        if body.decision not in CP1_DECISIONS:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for input_exception",
                                {"code": "bad_decision"})
    else:
        raise HTTPException(400, f"checkpoint type '{cp.checkpoint_type}' not yet supported",
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

    if cp.checkpoint_type == "input_exception":
        new_status = cp_svc.apply_cp1_decision(session, wf, cp, body.decision,
                                               body.payload, actor)
    cp_svc.resolve(session, wf, cp, row, actor.name)
    session.commit()
    cp_svc.log_decision(session, wf, row)

    if wf.status in ("INGESTING", "VALIDATING"):
        engine.launch(wf.id)
    return {"workflow_status": new_status, "applied": [body.decision]}

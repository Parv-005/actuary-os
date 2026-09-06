"""Validation router (§10): GET validation results for a workflow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ValidationResult, Workflow

router = APIRouter(prefix="/workflows", tags=["validation"])


@router.get("/{workflow_id}/validation")
def get_validation(workflow_id: str,
                   session: Session = Depends(get_session)) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    rows = session.execute(
        select(ValidationResult).where(ValidationResult.workflow_id == wf.id)
        .order_by(ValidationResult.check_id)
    ).scalars().all()
    return {"results": [
        {"check_id": r.check_id, "name": r.check_name, "category": r.category,
         "severity": r.severity, "status": r.status, "message": r.message,
         "details": r.details, "affected_row_count": r.affected_row_count,
         "resolution": r.resolution}
        for r in rows
    ]}

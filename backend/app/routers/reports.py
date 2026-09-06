"""Reports router (§10): GET versioned report + history for a workflow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Report, Workflow

router = APIRouter(prefix="/workflows", tags=["reports"])


def _row_to_dict(r: Report) -> dict:
    return {
        "id": str(r.id), "version": r.version, "status": r.status,
        "sections": r.sections or {},
        "body_markdown": r.body_markdown or "",
        "qa_result": r.qa_result,
        "approved_by": str(r.approved_by) if r.approved_by else None,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
    }


@router.get("/{workflow_id}/report")
def get_report(
    workflow_id: str,
    version: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    rows = session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version)
    ).scalars().all()
    if not rows:
        raise HTTPException(404, "no report for this workflow",
                            {"code": "no_report"})
    picked = next((r for r in rows if r.version == version), None) \
        if version is not None else rows[-1]
    if picked is None:
        raise HTTPException(404, f"report version {version} not found",
                            {"code": "no_report_version"})
    return {"report": _row_to_dict(picked),
            "history": [{"version": r.version, "status": r.status,
                         "generated_at": r.generated_at.isoformat()
                         if r.generated_at else None} for r in rows]}

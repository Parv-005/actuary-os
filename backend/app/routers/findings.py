"""Findings router (§10/§18): findings list + full evidence-chain drill-down.

Finding -> Evidence (immutable snapshots) -> Metric (formula + inputs) ->
Dataset version -> Source files. Knowledge evidence resolves to documents;
prior-finding links resolve to the earlier workflow's findings.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    DatasetVersion,
    Evidence,
    File,
    Finding,
    KnowledgeDocument,
    Metric,
    Workflow,
)

router = APIRouter(prefix="/workflows", tags=["findings"])


def _finding_summary(f: Finding, evidence_count: int) -> dict:
    return {
        "id": str(f.id), "title": f.title, "severity": f.severity,
        "confidence": float(f.confidence),
        "evidence_count": evidence_count,
        "alternatives": f.alternatives or [],
        "possible_drivers": f.possible_drivers or [],
        "status": f.status,
        "human_review_required": f.human_review_required,
        "decision_question": f.decision_question,
        "agent": f.agent, "model": f.model,
        "data_quality": f.data_quality,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


@router.get("/{workflow_id}/findings")
def list_findings(workflow_id: str,
                  session: Session = Depends(get_session)) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    findings = session.execute(
        select(Finding).where(Finding.workflow_id == wf.id)
        .order_by(Finding.created_at)
    ).scalars().all()
    out = []
    for f in findings:
        n = session.execute(
            select(Evidence.id).where(Evidence.finding_id == f.id)
        ).scalars().all()
        out.append(_finding_summary(f, len(n)))
    return {"findings": out}


def _metric_chain(session, wf_id, ref_id: str) -> dict:
    """Metric -> dataset versions -> source files (§18 drill-down)."""
    try:
        mid = uuid.UUID(str(ref_id))
    except ValueError:
        return {"metric": None, "error": f"unresolvable ref_id {ref_id!r}"}
    m = session.get(Metric, mid)
    if m is None or m.workflow_id != wf_id:
        return {"metric": None, "error": f"metric {ref_id} not in workflow"}
    dv_ids = (m.inputs or {}).get("dataset_version_ids", [])
    datasets, files = [], []
    for raw in dv_ids:
        try:
            dv = session.get(DatasetVersion, uuid.UUID(str(raw)))
        except ValueError:
            continue
        if dv is None or dv.workflow_id != wf_id:
            continue
        datasets.append({"id": str(dv.id), "kind": dv.kind,
                         "storage_path": dv.storage_path,
                         "row_count": dv.row_count, "checksum": dv.checksum})
        for fid in dv.source_file_ids or []:
            try:
                f = session.get(File, uuid.UUID(str(fid)))
            except ValueError:
                continue
            if f is not None and f.workflow_id == wf_id:
                files.append({"id": str(f.id), "filename": f.filename,
                              "kind": f.kind, "storage_path": f.storage_path,
                              "checksum": f.checksum,
                              "row_count": f.row_count})
    return {
        "metric": {"id": str(m.id), "metric_key": m.metric_key,
                   "dimensions": m.dimensions, "period": m.period,
                   "value": float(m.value) if m.value is not None else None,
                   "prev_value": (float(m.prev_value)
                                  if m.prev_value is not None else None),
                   "delta_pp": (float(m.delta_pp)
                                if m.delta_pp is not None else None),
                   "formula": m.formula,
                   "module_version": m.module_version},
        "datasets": datasets,
        "files": files,
    }


@router.get("/{workflow_id}/findings/{finding_id}")
def finding_detail(workflow_id: str, finding_id: str,
                   session: Session = Depends(get_session)) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    f = session.get(Finding, finding_id)
    if f is None or f.workflow_id != wf.id:
        raise HTTPException(404, "finding not found")
    rows = session.execute(
        select(Evidence).where(Evidence.finding_id == f.id)
        .order_by(Evidence.created_at)
    ).scalars().all()
    evidence = []
    for e in rows:
        item: dict = {"id": str(e.id), "type": e.evidence_type,
                      "ref_id": e.ref_id, "description": e.description,
                      "snapshot": e.snapshot,
                      "created_at": (e.created_at.isoformat()
                                     if e.created_at else None)}
        if e.evidence_type == "metric":
            item["chain"] = _metric_chain(session, wf.id, e.ref_id)
        elif e.evidence_type == "knowledge":
            doc = None
            try:
                doc = session.get(KnowledgeDocument, uuid.UUID(str(e.ref_id)))
            except ValueError:
                doc = None
            item["document"] = ({"title": doc.title,
                                 "version": doc.version,
                                 "doc_type": doc.doc_type} if doc else None)
        evidence.append(item)
    prior = []
    for raw in f.links or []:
        try:
            pf = session.get(Finding, uuid.UUID(str(raw)))
        except ValueError:
            continue
        if pf is None:
            continue
        pwf = session.get(Workflow, pf.workflow_id)
        prior.append({"id": str(pf.id), "title": pf.title,
                      "status": pf.status,
                      "workflow_ref": pwf.human_ref if pwf else None})
    detail = _finding_summary(f, len(rows))
    detail["narrative"] = f.narrative
    detail["correlation_caveat"] = f.correlation_caveat
    detail["links"] = f.links or []
    return {"finding": detail, "evidence": evidence,
            "prior_findings": prior}

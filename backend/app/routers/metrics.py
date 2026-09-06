"""Metrics router (§10): GET metrics (filters + series) for a workflow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Metric, ReferenceValue, Workflow

router = APIRouter(prefix="/workflows", tags=["metrics"])

DIM_KEYS = ("product", "segment", "region")
KNOWN_SERIES = {"loss_ratio", "claim_frequency", "claim_severity",
                "ave_variance", "deterioration_contribution"}


def _row_to_dict(m: Metric) -> dict:
    return {
        "id": str(m.id), "metric_key": m.metric_key, "dimensions": m.dimensions,
        "period": m.period,
        "value": float(m.value) if m.value is not None else None,
        "prev_value": float(m.prev_value) if m.prev_value is not None else None,
        "expected_value": (float(m.expected_value)
                           if m.expected_value is not None else None),
        "delta_pp": float(m.delta_pp) if m.delta_pp is not None else None,
        "unit": m.unit, "undefined_reason": m.undefined_reason,
        "flags": m.flags, "formula": m.formula, "inputs": m.inputs,
        "module_version": m.module_version,
        "computed_at": m.computed_at.isoformat() if m.computed_at else None,
    }


@router.get("/{workflow_id}/metrics")
def get_metrics(
    workflow_id: str,
    metric_key: str | None = Query(default=None),
    group_by: str | None = Query(default=None),
    period: str | None = Query(default=None),
    series: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    wf = session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "workflow not found")
    if series is not None:
        return _series(session, wf, series)

    wanted: set[str] | None = None
    if group_by is not None:
        g = group_by.strip()
        if g in ("", "portfolio"):
            wanted = set()
        else:
            wanted = {p.strip() for p in g.split(",") if p.strip()}
            if not wanted or not wanted.issubset(DIM_KEYS):
                raise HTTPException(400, "group_by must be a subset of "
                                         "product,segment,region (or 'portfolio')",
                                    {"code": "bad_group_by"})

    rows = session.execute(
        select(Metric).where(Metric.workflow_id == wf.id)
        .order_by(Metric.metric_key)
    ).scalars().all()
    out, undefined = [], []
    for m in rows:
        if metric_key is not None and m.metric_key != metric_key:
            continue
        if period is not None and m.period != period:
            continue
        if wanted is not None and set((m.dimensions or {}).keys()) != wanted:
            continue
        out.append(_row_to_dict(m))
        if m.value is None:
            undefined.append({"metric_key": m.metric_key,
                              "dimensions": m.dimensions,
                              "reason": m.undefined_reason})
    return {"metrics": out, "undefined": undefined}


def _series(session: Session, wf: Workflow, series: str) -> dict:
    if series not in KNOWN_SERIES:
        raise HTTPException(400, f"unknown series: {series}",
                            {"code": "bad_series"})
    points = []
    if series == "loss_ratio":
        refs = session.execute(
            select(ReferenceValue).where(
                ReferenceValue.metric_key == "historical_loss_ratio",
                ReferenceValue.period <= wf.reporting_period,
            ).order_by(ReferenceValue.period)
        ).scalars().all()
        points += [{"period": r.period, "value": float(r.value),
                    "source": r.source} for r in refs]
    cur = session.execute(
        select(Metric).where(Metric.workflow_id == wf.id,
                             Metric.metric_key == series,
                             Metric.dimensions == {})
    ).scalars().all()
    points += [{"period": m.period,
                "value": float(m.value) if m.value is not None else None,
                "source": "computed"} for m in cur]
    points.sort(key=lambda p: p["period"])
    return {"metric": series, "series": points}

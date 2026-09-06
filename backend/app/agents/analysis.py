"""Analysis Agent (§6.5, deterministic): execute the metric catalog.

Loads processed datasets + prior-workflow baselines + expected LR, builds
metric rows with analytics/metrics.py (pure), upserts by natural key
(workflow, key, dimensions, period) and prunes stale cells. Unavailable
metrics (NULL + reason) surface downstream in report Open questions —
the agent returns WARNING when any exist, never BLOCKER.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.analytics import loader
from app.analytics import metrics as met
from app.analytics import quality as q
from app.models import DatasetVersion, Metric, ReferenceValue, Workflow
from app.services.audit import record_event

KINDS = ("claims", "premium", "exposure")


async def _prior_inputs(session, storage, wf: Workflow):
    """Returns (prior_workflow|None, prior_aggs|None, prior_metrics dict).

    Prefers real prior aggregates (recomputed from the prior workflow's
    processed datasets at identical granularity); falls back to stored
    prior metric values for prev_value lookup.
    """
    cands = session.execute(
        select(Workflow).where(
            Workflow.portfolio == wf.portfolio,
            Workflow.reporting_period < wf.reporting_period,
            Workflow.id != wf.id,
        ).order_by(Workflow.reporting_period.desc())
    ).scalars().all()
    for pw in cands:
        dvs = {dv.kind: dv for dv in session.execute(
            select(DatasetVersion).where(DatasetVersion.workflow_id == pw.id)
        ).scalars().all()}
        aggs = None
        if all(k in dvs for k in KINDS):
            try:
                frames = {k: await loader.load_processed(
                    storage, dvs[k].storage_path, dvs[k].checksum) for k in KINDS}
                aggs = met.aggregate_frames(
                    frames["claims"], frames["premium"], frames["exposure"])
            except Exception:  # noqa: BLE001 — prior datasets unreadable: metrics fallback
                aggs = None
        mets = {}
        for mrow in session.execute(
            select(Metric).where(Metric.workflow_id == pw.id)
        ).scalars().all():
            if mrow.value is not None:
                mets[(mrow.metric_key,
                      json.dumps(mrow.dimensions or {}, sort_keys=True))] = float(
                    mrow.value)
        if aggs is not None or mets:
            return pw, aggs, mets
    return None, None, {}


def _upsert_and_prune(session, wf: Workflow, rows: list[dict]) -> int:
    existing = session.execute(
        select(Metric).where(Metric.workflow_id == wf.id)).scalars().all()
    by_key = {(m.metric_key, json.dumps(m.dimensions or {}, sort_keys=True),
               m.period): m for m in existing}
    seen: set = set()
    for r in rows:
        key = (r["metric_key"], json.dumps(r["dimensions"], sort_keys=True),
               r["period"])
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            row = Metric(workflow_id=wf.id, metric_key=r["metric_key"],
                         dimensions=r["dimensions"], period=r["period"])
            session.add(row)
        row.value = r["value"]
        row.prev_value = r["prev_value"]
        row.expected_value = r["expected_value"]
        row.delta_pp = r["delta_pp"]
        row.unit = r["unit"]
        row.undefined_reason = r["undefined_reason"]
        row.flags = r["flags"]
        row.formula = r["formula"]
        row.inputs = r["inputs"]
        row.dataset_version_ids = r["inputs"].get("dataset_version_ids", [])
        row.module_version = r["module_version"]
    for key, m in by_key.items():
        if key not in seen:
            session.delete(m)
    session.flush()
    return len(rows)


async def run_analysis(ctx: WorkflowContext) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    period = wf.reporting_period
    dvs = {dv.kind: dv for dv in session.execute(
        select(DatasetVersion).where(DatasetVersion.workflow_id == wf.id)
    ).scalars().all()}
    missing = [k for k in KINDS if k not in dvs]
    if missing:
        return StageResult(
            status="FAILED",
            error=f"analysis: no processed dataset for: {', '.join(missing)}")

    frames = {k: await loader.load_processed(
        ctx.storage, dvs[k].storage_path, dvs[k].checksum) for k in KINDS}
    refs = {r.metric_key: float(r.value) for r in session.execute(
        select(ReferenceValue).where(ReferenceValue.period == period)
    ).scalars().all()}
    prior_wf, prior_aggs, prior_metrics = await _prior_inputs(
        session, ctx.storage, wf)

    cur = met.aggregate_frames(frames["claims"], frames["premium"],
                               frames["exposure"])

    # outlier cells: global scan, then map flagged claims back to finest cells
    cl = frames["claims"].copy()
    for c in ("product", "segment", "region"):
        cl[c] = cl[c].fillna("").astype(str).str.strip() if c in cl else ""
    amts, ids = [], []
    for _, row in cl.iterrows():
        try:
            amts.append(float(row["incurred_amount"]))
            ids.append(str(row["claim_id"]))
        except (TypeError, ValueError):
            continue
    out = q.outlier_flags(amts, ids)
    flagged = {f["claim_id"] for f in out.get("flagged", [])}
    cell_of = dict(zip(cl["claim_id"].astype(str),
                       zip(cl["product"], cl["segment"], cl["region"], strict=True),
                       strict=True))
    outlier_cells = {cell_of[c] for c in flagged if c in cell_of}

    rows = met.build_rows(
        cur, period=period,
        dv_ids=[str(dvs[k].id) for k in KINDS],
        prior=prior_aggs, prior_metrics=prior_metrics,
        expected_lr=refs.get("expected_loss_ratio"),
        outlier_cells=outlier_cells,
        prior_ref=prior_wf.human_ref if prior_wf else None,
    )
    n = _upsert_and_prune(session, wf, rows)
    unavailable = [{"metric_key": r["metric_key"], "dimensions": r["dimensions"],
                    "reason": r["undefined_reason"]} for r in rows
                   if r["value"] is None]

    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="analysis_agent",
        action="analysis_complete", entity_type="workflow", entity_id=wf.id,
        summary=f"analysis: {n} metrics, {len(unavailable)} unavailable",
        details={"metrics": n, "unavailable": unavailable,
                 "prior_ref": prior_wf.human_ref if prior_wf else None},
    )
    if unavailable:
        return StageResult(status="WARNING",
                           outputs={"metrics": n, "unavailable": unavailable})
    return StageResult(status="PASS", outputs={"metrics": n, "unavailable": []})

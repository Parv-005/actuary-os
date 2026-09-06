"""Insight / Investigation Agent (§6.6, LLM tool loop over deterministic tools).

Investigates drivers of metric movements, persists ONE evidence-backed
finding + immutable metric evidence snapshots, and deterministically
computes the CP-4 assumption-variance gate (variance detection is never
left to LLM judgment). Low-confidence/conflicting findings are flagged
human_review_required + yellow CP-5 (non-blocking).
"""
from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.agents.usage import record_llm_usage
from app.analytics import tools as T
from app.config import settings
from app.llm.client import LLMError, LLMUsage, get_llm_client, load_prompt, prompt_hash
from app.llm.schemas import InsightFindings
from app.models import (
    Evidence,
    Finding,
    KnowledgeDocument,
    Metric,
    ReferenceValue,
    ValidationResult,
)
from app.services import checkpoints
from app.services.audit import record_event

AGENT_VERSION = "v1"
MAX_LLM_ATTEMPTS = 3  # tool-loop attempts before the single-shot fallback


class _BadEvidence(Exception):
    pass


def _snapshot_metrics(session, wf_id) -> list[dict]:
    return [{
        "id": str(m.id), "metric_key": m.metric_key,
        "dimensions": m.dimensions or {}, "period": m.period,
        "value": float(m.value) if m.value is not None else None,
        "prev_value": float(m.prev_value) if m.prev_value is not None else None,
        "delta_pp": float(m.delta_pp) if m.delta_pp is not None else None,
        "expected_value": (float(m.expected_value)
                           if m.expected_value is not None else None),
        "unit": m.unit, "undefined_reason": m.undefined_reason,
        "flags": m.flags or {}, "formula": m.formula,
    } for m in session.execute(
        select(Metric).where(Metric.workflow_id == wf_id)).scalars().all()]


def _snapshot_validation(session, wf_id) -> list[dict]:
    return [{
        "check_id": v.check_id, "check_name": v.check_name,
        "category": v.category, "severity": v.severity, "status": v.status,
        "message": v.message, "details": v.details or {},
    } for v in session.execute(
        select(ValidationResult).where(
            ValidationResult.workflow_id == wf_id)
        .order_by(ValidationResult.check_id)).scalars().all()]


def _data_quality_summary(validation: list[dict]) -> str:
    by_status: dict[str, int] = {}
    for v in validation:
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    parts = [f"{n} {s}" for s, n in sorted(by_status.items())]
    return "validation: " + (", ".join(parts) if parts else "no checks")


def _material_movement(metrics: list[dict]) -> bool:
    for m in metrics:
        if m["metric_key"] == "loss_ratio" and not m["dimensions"]:
            if m["delta_pp"] is not None \
                    and abs(m["delta_pp"]) >= settings.lr_shift_warn_pp:
                return True
        if m["metric_key"] == "ave_variance" and m["value"] is not None \
                and abs(m["value"]) >= settings.lr_shift_warn_pp:
            return True
    return False


def _validate_evidence_ids(output: InsightFindings, metric_ids: set[str]) -> None:
    if not output.evidence_ids:
        raise _BadEvidence("finding cites no evidence_ids")
    unknown = [e for e in output.evidence_ids if e not in metric_ids]
    if unknown:
        raise _BadEvidence(f"unknown evidence_ids: {unknown}")


def _assumption_variances(session, wf_period: str,
                         metrics: list[dict]) -> list[dict]:
    """Deterministic CP-4 computation: observed severity delta vs the
    configured trend from reference_values (gate: assumption_variance_gate_pp).
    Prefers the most material descendant cell; falls back to the exact cell.
    """
    refs = session.execute(
        select(ReferenceValue).where(
            ReferenceValue.period == wf_period)
    ).scalars().all()
    trends = [r for r in refs if r.metric_key == "expected_severity_trend"]
    sev = [m for m in metrics if m["metric_key"] == "claim_severity"]
    out = []
    for ref in trends:
        rdims = ref.dimensions or {}
        cands = [m for m in sev
                 if all(m["dimensions"].get(d) == v for d, v in rdims.items())
                 and m["delta_pp"] is not None]
        if not cands:
            continue
        best = max(cands, key=lambda m: (m["delta_pp"] or 0)
                   - float(ref.value) * 100)
        variance = (best["delta_pp"] or 0) - float(ref.value) * 100
        if variance >= settings.assumption_variance_gate_pp:
            out.append({
                "product": rdims.get("product"), "segment": rdims.get("segment"),
                "region": best["dimensions"].get("region"),
                "observed_delta_pct": round(best["delta_pp"], 2),
                "expected_trend_pct": round(float(ref.value) * 100, 2),
                "variance_pp": round(variance, 2),
                "metric_id": best["id"],
            })
    return out


def _methodology_doc(session) -> dict | None:
    doc = session.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.superseded_by.is_(None))
        .order_by(KnowledgeDocument.effective_date.desc())
    ).scalars().first()
    # prefer an assumptions/methodology doc when several current docs exist
    docs = session.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.superseded_by.is_(None))
    ).scalars().all()
    for d in docs:
        if "assumptions" in (d.tags or []) or d.doc_type == "methodology":
            doc = d
            break
    if doc is None:
        return None
    return {"id": str(doc.id), "title": doc.title, "version": doc.version,
            "effective_date": (doc.effective_date.isoformat()
                               if doc.effective_date else None),
            "excerpt": (doc.content_text or "")[:500]}


def _persist_finding(session, wf, output: InsightFindings,
                     metrics_by_id: dict, data_quality: str,
                     model: str) -> Finding:
    prior_ids = [str(f.id) for f in session.execute(
        select(Finding).where(Finding.workflow_id == wf.id,
                              Finding.agent == "insight")).scalars().all()]
    if prior_ids:
        # replace per re-run, linked to prior (§17); evidence cascades
        for f in session.execute(
                select(Finding).where(Finding.workflow_id == wf.id,
                                      Finding.agent == "insight")).scalars().all():
            session.delete(f)
        session.flush()
    hr = bool(output.human_review_required or output.confidence < 0.6)
    finding = Finding(
        workflow_id=wf.id, agent="insight", agent_version=AGENT_VERSION,
        model=model, title=output.finding,
        narrative=(f"Evidence: {output.narrative.evidence}\n"
                   f"Hypothesis: {output.narrative.hypothesis}\n"
                   f"Conclusion: {output.narrative.conclusion}"),
        severity=output.severity, confidence=output.confidence,
        possible_drivers=list(output.possible_drivers),
        alternatives=list(output.alternatives),
        correlation_caveat=output.correlation_caveat,
        human_review_required=hr,
        decision_question=output.decision_question,
        data_quality=data_quality, status="draft", links=prior_ids,
    )
    session.add(finding)
    session.flush()
    for mid in output.evidence_ids:
        m = metrics_by_id[mid]
        session.add(Evidence(
            workflow_id=wf.id, finding_id=finding.id, evidence_type="metric",
            ref_id=mid,
            snapshot={"metric_key": m["metric_key"],
                      "dimensions": m["dimensions"], "period": m["period"],
                      "value": m["value"], "prev_value": m["prev_value"],
                      "delta_pp": m["delta_pp"],
                      "expected_value": m["expected_value"], "unit": m["unit"],
                      "formula": m["formula"]},
            description=(f"{m['metric_key']} {m['dimensions'] or 'portfolio'}: "
                         f"{m['value']} (prev {m['prev_value']})"),
        ))
    session.flush()
    return finding


async def run_insight(ctx: WorkflowContext, llm_client=None) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    metrics = _snapshot_metrics(session, wf.id)
    if not metrics:
        return StageResult(status="FAILED",
                           error="insight: no metrics to investigate")
    validation = _snapshot_validation(session, wf.id)
    metric_ids = {m["id"] for m in metrics}
    metrics_by_id = {m["id"]: m for m in metrics}

    port_lr = next((m for m in metrics
                    if m["metric_key"] == "loss_ratio" and not m["dimensions"]),
                   None)
    bundle = {
        "workflow": wf.human_ref, "period": wf.reporting_period,
        "portfolio_loss_ratio": port_lr,
        "validation_summary": _data_quality_summary(validation),
        "material_movement": _material_movement(metrics),
        "instruction": ("Call tools (max 6) to investigate drivers, then "
                        "return the final JSON finding only."),
    }
    system = load_prompt("insight_system")
    user = json.dumps(bundle)
    phash = prompt_hash(system, user)

    bound = T.bind_tools(metrics, validation, wf.reporting_period)
    client = llm_client or get_llm_client()
    model = settings.llm_model if settings.llm_provider != "fake" else "fake"

    output: InsightFindings | None = None
    usage = LLMUsage()
    tool_calls = 0
    errors: list[str] = []
    for attempt in range(MAX_LLM_ATTEMPTS):
        ask = user if attempt == 0 else (
            f"{user}\n\nPrevious attempt failed: {errors[-1]} "
            "Fix the error and retry (same tools, same schema).")
        try:
            result = await client.run_tool_loop(
                system, ask, bound.schemas, bound.executor,
                InsightFindings, max_rounds=8)
            _validate_evidence_ids(result.output, metric_ids)
            output = result.output
            usage.calls += result.usage.calls
            usage.tokens_in += result.usage.tokens_in
            usage.tokens_out += result.usage.tokens_out
            tool_calls = bound.calls
            break
        except (LLMError, ValidationError, _BadEvidence) as e:
            errors.append(str(e)[:300])
    if output is None:
        # fallback: single-shot with a deterministically pre-fetched bundle
        try:
            pre = {"portfolio_summary": T.TOOL_REGISTRY["portfolio_summary"].func(
                {}, bound.snapshot),
                "top_contributors": T.TOOL_REGISTRY["top_contributors"].func(
                {}, bound.snapshot),
                "claim_outliers": T.TOOL_REGISTRY["claim_outliers"].func(
                {}, bound.snapshot)}
            fb = await client.json_call(
                system, f"{user}\n\nPre-fetched tool results (no more tool "
                f"calls): {json.dumps(pre, default=str)}", InsightFindings)
            _validate_evidence_ids(fb.output, metric_ids)
            output = fb.output
            usage.calls += fb.usage.calls
            usage.tokens_in += fb.usage.tokens_in
            usage.tokens_out += fb.usage.tokens_out
        except (LLMError, ValidationError, _BadEvidence) as e:
            errors.append(str(e)[:300])
            return StageResult(
                status="FAILED",
                error=f"insight: LLM failed after {MAX_LLM_ATTEMPTS} attempts "
                      f"+ fallback: {'; '.join(errors)}"[:500])
    record_llm_usage(session, wf.id, "insight", "insight", usage, phash)

    finding = _persist_finding(session, wf, output, metrics_by_id,
                               _data_quality_summary(validation), model)

    # deterministic CP-4: observed vs configured severity trend
    variances = _assumption_variances(session, wf.reporting_period, metrics)
    cp4_id = None
    if variances:
        method = _methodology_doc(session)
        cp = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="assumption_variance",
            severity="red", blocking=True,
            title=("Assumption variance: observed severity "
                   f"{variances[0]['observed_delta_pct']:+.1f}% vs configured "
                   f"{variances[0]['expected_trend_pct']:+.1f}%"),
            context={
                "variances": variances,
                "methodology": method,
                "finding_ids": [str(finding.id)],
                "disclaimer": "AI does NOT recommend an assumption change — "
                              "the actuary decides.",
            },
            options=[
                {"decision": "no_change_required",
                 "label": "No Change Required — monitor"},
                {"decision": "investigate_further",
                 "label": "Investigate Further"},
                {"decision": "review_assumption",
                 "label": "Review Assumption"},
                {"decision": "escalate", "label": "Escalate"},
            ],
        )
        cp4_id = str(cp.id)

    # yellow CP-5 for flagged findings (non-blocking)
    cp5_id = None
    if finding.human_review_required:
        cp5 = checkpoints.raise_checkpoint(
            session, wf, checkpoint_type="finding_review",
            severity="yellow", blocking=False,
            title=f"Finding review: {finding.title[:100]}",
            context={"finding_id": str(finding.id),
                     "confidence": float(finding.confidence),
                     "reason": ("confidence below 0.6"
                                if finding.confidence < 0.6
                                else "flagged by investigation")},
            options=[{"decision": d, "label": d.replace("_", " ").title()}
                     for d in ("accept", "reject", "override", "monitor",
                               "investigate_further", "comment")],
        )
        cp5_id = str(cp5.id)

    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="insight_agent",
        action="insight_complete", entity_type="finding", entity_id=finding.id,
        summary=f"finding: {finding.title[:120]} (confidence "
                f"{float(finding.confidence):.2f})",
        details={"finding_id": str(finding.id),
                 "evidence": len(output.evidence_ids),
                 "tool_calls": tool_calls, "attempts": len(errors) + 1,
                 "cp4": cp4_id, "cp5": cp5_id},
    )
    return StageResult(status="PASS",
                       outputs={"finding_id": str(finding.id),
                                "evidence": len(output.evidence_ids),
                                "tool_calls": tool_calls,
                                "cp4_checkpoint_id": cp4_id,
                                "cp5_checkpoint_id": cp5_id})

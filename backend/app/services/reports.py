"""Deterministic report assembly (§6.8): numbers bundle, key-metrics tables,
exceptions/decisions/citations sections, Recharts specs, markdown render.

The LLM writes prose ONLY (executive summary + open questions); every
number it may cite comes from build_numbers_bundle, and QA re-verifies
each numeral against the same bundle pool.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models import (
    DatasetVersion,
    Evidence,
    Finding,
    HumanCheckpoint,
    HumanDecision,
    Metric,
    ReferenceValue,
    ValidationResult,
)

REQUIRED_PORTFOLIO_METRICS = ("loss_ratio", "claim_frequency",
                              "claim_severity", "ave_variance")
CHART_TYPES = ("line", "bar")


def _f(v):
    return float(v) if v is not None else None


def load_report_inputs(session, wf) -> dict:
    """Everything the report needs, as plain dicts."""
    metrics = [{
        "id": str(m.id), "metric_key": m.metric_key,
        "dimensions": m.dimensions or {}, "period": m.period,
        "value": _f(m.value), "prev_value": _f(m.prev_value),
        "expected_value": _f(m.expected_value),
        "delta_pp": _f(m.delta_pp), "unit": m.unit,
        "undefined_reason": m.undefined_reason, "flags": m.flags or {},
        "formula": m.formula,
    } for m in session.execute(
        select(Metric).where(Metric.workflow_id == wf.id)).scalars().all()]
    findings = session.execute(
        select(Finding).where(Finding.workflow_id == wf.id)
        .order_by(Finding.created_at)).scalars().all()
    ev_counts: dict[str, int] = {}
    for (fid,) in session.execute(
            select(Evidence.finding_id).where(
                Evidence.workflow_id == wf.id,
                Evidence.finding_id.is_not(None))).all():
        ev_counts[str(fid)] = ev_counts.get(str(fid), 0) + 1
    finding_rows = [{
        "id": str(f.id), "title": f.title, "severity": f.severity,
        "confidence": float(f.confidence),
        "evidence_count": ev_counts.get(str(f.id), 0),
        "narrative": f.narrative,
        "possible_drivers": f.possible_drivers or [],
        "alternatives": f.alternatives or [],
        "decision_question": f.decision_question,
        "status": f.status,
    } for f in findings]
    validation = [{
        "check_id": v.check_id, "name": v.check_name,
        "category": v.category, "severity": v.severity, "status": v.status,
        "message": v.message, "details": v.details or {},
        "resolution": v.resolution,
    } for v in session.execute(
        select(ValidationResult).where(
            ValidationResult.workflow_id == wf.id)
        .order_by(ValidationResult.check_id)).scalars().all()]
    cps = {str(c.id): c.checkpoint_type for c in session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.workflow_id == wf.id)).scalars().all()}
    decisions = [{
        "decision": d.decision, "rationale": d.rationale,
        "checkpoint_type": cps.get(str(d.checkpoint_id)),
        "decided_at": d.decided_at.isoformat() if d.decided_at else None,
    } for d in session.execute(
        select(HumanDecision).where(HumanDecision.workflow_id == wf.id)
        .order_by(HumanDecision.decided_at)).scalars().all()]
    citations = [{
        "title": (e.snapshot or {}).get("title"),
        "version": (e.snapshot or {}).get("version"),
        "excerpt": ((e.snapshot or {}).get("excerpt") or "")[:300],
    } for e in session.execute(
        select(Evidence).where(Evidence.workflow_id == wf.id,
                               Evidence.evidence_type == "knowledge"))
        .scalars().all()]
    refs = {r.metric_key: float(r.value) for r in session.execute(
        select(ReferenceValue).where(
            ReferenceValue.period == wf.reporting_period)).scalars().all()}
    history = sorted(
        (r.period, float(r.value)) for r in session.execute(
            select(ReferenceValue).where(
                ReferenceValue.metric_key == "historical_loss_ratio"))
        .scalars().all())
    dvs = {dv.kind: dv for dv in session.execute(
        select(DatasetVersion).where(
            DatasetVersion.workflow_id == wf.id)).scalars().all()}
    return {"metrics": metrics, "findings": finding_rows,
            "validation": validation, "decisions": decisions,
            "citations": citations, "refs": refs, "history": history,
            "datasets": dvs, "config": wf.config or {}}


def compute_aggregates(frames: dict) -> dict:
    """Portfolio totals from processed frames (citable bundle values)."""
    import pandas as pd

    earned = float(pd.to_numeric(
        frames["premium"]["earned_premium"], errors="coerce").fillna(0).sum())
    incurred = float(pd.to_numeric(
        frames["claims"]["incurred_amount"], errors="coerce").fillna(0).sum())
    return {"earned_premium_total": earned, "incurred_total": incurred,
            "claim_count": int(len(frames["claims"])),
            "policy_count": int(len(frames["exposure"]))}


def _display(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer() and abs(v) > 100:
        return int(v)
    return round(v, 1) if isinstance(v, float) else v


def build_numbers_bundle(inputs: dict, aggregates: dict,
                         counts: dict) -> dict:
    """Prompt bundle (1-decimal display) + full-precision pool for QA."""
    metrics = [{
        "metric_key": m["metric_key"], "dimensions": m["dimensions"],
        "value": _display(m["value"]), "prev_value": _display(m["prev_value"]),
        "expected_value": _display(m["expected_value"]),
        "delta_pp": _display(m["delta_pp"]), "unit": m["unit"],
    } for m in inputs["metrics"]]
    pool: list[float] = []
    for m in inputs["metrics"]:
        for k in ("value", "prev_value", "expected_value", "delta_pp"):
            if m[k] is not None:
                pool.append(float(m[k]))
        for k in ("claim_count", "policy_count"):
            pool.append(float((m["flags"] or {}).get(k, 0)))
    for v in list(aggregates.values()) + list(counts.values()) \
            + list(inputs["refs"].values()):
        pool.append(float(v))
    unavailable = [{"metric_key": m["metric_key"],
                    "dimensions": m["dimensions"],
                    "reason": m["undefined_reason"]}
                   for m in inputs["metrics"] if m["value"] is None]
    return {"metrics": metrics, "aggregates": aggregates,
            "references": inputs["refs"], "counts": counts,
            "unavailable": unavailable, "pool": pool}


def build_key_metrics(inputs: dict) -> tuple[list[dict], list[str]]:
    """Deterministic key-metrics table. Missing required metrics are omitted
    and surface as deterministic open questions (spec §11)."""
    by_key = {(m["metric_key"], tuple(sorted(m["dimensions"].items()))): m
              for m in inputs["metrics"]}
    table, open_questions = [], []
    for key in REQUIRED_PORTFOLIO_METRICS:
        m = by_key.get((key, ()))
        if m is None or m["value"] is None:
            reason = (m or {}).get("undefined_reason") or "not computed"
            open_questions.append(
                f"Metric {key} unavailable at portfolio: {reason}")
            continue
        table.append({"metric_key": key, "dimensions": {},
                      "value": m["value"], "prev_value": m["prev_value"],
                      "expected_value": m["expected_value"],
                      "delta_pp": m["delta_pp"], "unit": m["unit"]})
    products = sorted({(m["dimensions"] or {}).get("product")
                       for m in inputs["metrics"]
                       if set(m["dimensions"] or {}) == {"product"}})
    for product in products:
        m = by_key.get(("loss_ratio", (("product", product),)))
        if m is not None and m["value"] is not None:
            table.append({"metric_key": "loss_ratio",
                          "dimensions": {"product": product},
                          "value": m["value"],
                          "prev_value": m["prev_value"],
                          "delta_pp": m["delta_pp"], "unit": m["unit"]})
    contribs = sorted(
        (m for m in inputs["metrics"]
         if m["metric_key"] == "deterioration_contribution"
         and m["value"] is not None),
        key=lambda m: -(m["value"] or 0))[:5]
    for m in contribs:
        table.append({"metric_key": "deterioration_contribution",
                      "dimensions": m["dimensions"], "value": m["value"],
                      "unit": m["unit"]})
    return table, open_questions


def build_exceptions(inputs: dict) -> list[dict]:
    out = [{
        "check_id": v["check_id"], "name": v["name"],
        "severity": v["severity"], "status": v["status"],
        "message": v["message"], "resolution": v["resolution"],
    } for v in inputs["validation"] if v["status"] != "PASS"]
    for acc in (inputs["config"] or {}).get("accepted_exceptions", []):
        out.append({"check_id": None, "name": "accepted exception",
                    "severity": "INFO", "status": "ACCEPTED_EXCEPTION",
                    "message": f"{acc.get('source')}: {acc.get('note')}",
                    "resolution": {"decision_id": acc.get("decision_id")}})
    return out


def generate_chart_spec(chart_type: str, title: str, data: list,
                        x_key: str, series: list) -> dict:
    """Recharts-ready spec constructor (pure)."""
    if chart_type not in CHART_TYPES:
        raise ValueError(f"unknown chart type: {chart_type}")
    return {"chart": chart_type, "title": title, "data": data,
            "xKey": x_key, "series": series}


def build_charts(inputs: dict) -> list[dict]:
    by_key = {(m["metric_key"], tuple(sorted(m["dimensions"].items()))): m
              for m in inputs["metrics"]}
    charts = []
    port = by_key.get(("loss_ratio", ()))
    trend = [{"period": p, "loss_ratio": v}
             for p, v in inputs["history"]]
    if port is not None and port["value"] is not None:
        trend.append({"period": port["period"],
                      "loss_ratio": port["value"]})
    if trend:
        charts.append(generate_chart_spec(
            "line", "Portfolio loss ratio trend", trend, "period",
            [{"key": "loss_ratio", "name": "Loss ratio"}]))
    prod_rows = []
    for m in inputs["metrics"]:
        if m["metric_key"] == "loss_ratio" \
                and set(m["dimensions"]) == {"product"} \
                and m["value"] is not None:
            prod_rows.append({"product": m["dimensions"]["product"],
                              "current": m["value"],
                              "prior": m["prev_value"]})
    if prod_rows:
        charts.append(generate_chart_spec(
            "bar", "Loss ratio by product — current vs prior",
            sorted(prod_rows, key=lambda r: r["product"]), "product",
            [{"key": "current", "name": "Current"},
             {"key": "prior", "name": "Prior"}]))
    contrib_rows = [{
        "cell": " / ".join(f"{k} {v}" for k, v in
                           sorted(m["dimensions"].items())),
        "share": m["value"],
    } for m in sorted(
        (m for m in inputs["metrics"]
         if m["metric_key"] == "deterioration_contribution"
         and m["value"] is not None),
        key=lambda m: -(m["value"] or 0))[:8]]
    if contrib_rows:
        charts.append(generate_chart_spec(
            "bar", "Deterioration contribution by cell", contrib_rows,
            "cell", [{"key": "share", "name": "Share (%)"}]))
    sev_rows = []
    for m in inputs["metrics"]:
        d = m["dimensions"] or {}
        if m["metric_key"] == "claim_severity" \
                and set(d) == {"product", "segment"} \
                and m["delta_pp"] is not None:
            fq = by_key.get(("claim_frequency",
                             tuple(sorted(d.items()))))
            sev_rows.append({
                "segment": f"{d.get('product')} / {d.get('segment')}",
                "severity_delta": m["delta_pp"],
                "frequency_delta": fq["delta_pp"] if fq else None})
    if sev_rows:
        charts.append(generate_chart_spec(
            "bar", "Severity vs frequency change by segment", sev_rows,
            "segment",
            [{"key": "severity_delta", "name": "Severity Δ (%)"},
             {"key": "frequency_delta", "name": "Frequency Δ (%)"}]))
    return charts


def assemble_report(inputs: dict, aggregates: dict, counts: dict,
                    executive_summary: str,
                    llm_open_questions: list[str]) -> tuple[dict, list[str]]:
    """Merge deterministic sections with LLM prose. Returns (sections,
    deterministic_open_questions)."""
    key_metrics, omitted_oqs = build_key_metrics(inputs)
    det_oqs = list(omitted_oqs)
    covered = {(m["metric_key"], tuple(sorted(m["dimensions"].items())))
               for m in inputs["metrics"]
               if m["value"] is None and not m["dimensions"]
               and m["metric_key"] in REQUIRED_PORTFOLIO_METRICS}
    for u in inputs["metrics"]:
        key = (u["metric_key"], tuple(sorted(u["dimensions"].items())))
        if u["value"] is None and key not in covered:
            det_oqs.append(
                f"Metric {u['metric_key']} {u['dimensions'] or 'portfolio'} "
                f"unavailable: {u['undefined_reason']}")
    sections = {
        "executive_summary": executive_summary,
        "key_metrics": key_metrics,
        "findings": [{
            "title": f["title"], "severity": f["severity"],
            "confidence": f["confidence"],
            "evidence_count": f["evidence_count"],
            "narrative": f["narrative"],
            "decision_question": f["decision_question"],
        } for f in inputs["findings"]],
        "exceptions": build_exceptions(inputs),
        "decisions": inputs["decisions"],
        "open_questions": det_oqs + list(llm_open_questions or []),
        "charts": build_charts(inputs),
        "citations": inputs["citations"],
    }
    return sections, det_oqs


def render_markdown(sections: dict, human_ref: str, period: str) -> str:
    """Deterministic markdown render of the assembled sections."""
    lines = [f"# Monthly Portfolio Review — {period} ({human_ref})", "",
             "## Executive summary", "", sections["executive_summary"], "",
             "## Key metrics", ""]
    for row in sections["key_metrics"]:
        dims = ", ".join(f"{k} {v}" for k, v in
                         sorted((row.get("dimensions") or {}).items()))
        label = f"{row['metric_key']} {dims}".strip() or row["metric_key"]
        lines.append(f"- {label}: {row.get('value')} "
                     f"(prev {row.get('prev_value')}, "
                     f"Δ {row.get('delta_pp')})")
    lines += ["", "## Findings", ""]
    for f in sections["findings"]:
        lines.append(f"### {f['title']} [{f['severity']}]")
        lines.append(f["narrative"])
        lines.append("")
    lines += ["## Exceptions", ""]
    for e in sections["exceptions"]:
        lines.append(f"- [{e['status']}] {e.get('name')}: {e.get('message')}")
    lines += ["", "## Decisions", ""]
    for d in sections["decisions"]:
        lines.append(f"- {d['decision']}: {d.get('rationale') or ''}")
    lines += ["", "## Open questions", ""]
    for q in sections["open_questions"]:
        lines.append(f"- {q}")
    if sections["citations"]:
        lines += ["", "## Citations", ""]
        for c in sections["citations"]:
            lines.append(f"- {c.get('title')} ({c.get('version')})")
    return "\n".join(lines).strip() + "\n"

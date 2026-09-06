"""Reporting Agent (§6.8, LLM prose only): decision-ready draft report.

One LLM call produces the executive summary + open questions from a
deterministic numbers bundle; every table, finding, exception, decision,
chart and citation is assembled deterministically in services/reports.py.
Regeneration (QA auto-regen, human revision) creates version+1 — history
is kept, never overwritten.
"""
from __future__ import annotations

import json

from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.agents.usage import record_llm_usage
from app.analytics import loader
from app.llm.client import LLMError, LLMUsage, get_llm_client, load_prompt, prompt_hash
from app.llm.schemas import ReportDraft
from app.models import Report
from app.services import reports as rep
from app.services.audit import record_event

AGENT_VERSION = "v1"
MAX_LLM_ATTEMPTS = 3
KINDS = ("claims", "premium", "exposure")


async def run_reporting(ctx: WorkflowContext, llm_client=None,
                        regen_feedback: str | None = None) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    inputs = rep.load_report_inputs(session, wf)
    if not inputs["metrics"]:
        return StageResult(status="FAILED",
                           error="reporting: no metrics to report on")
    dvs = inputs["datasets"]
    missing = [k for k in KINDS if k not in dvs]
    if missing:
        return StageResult(
            status="FAILED",
            error=f"reporting: no processed dataset for: {', '.join(missing)}")
    try:
        frames = {k: await loader.load_processed(
            ctx.storage, dvs[k].storage_path, dvs[k].checksum) for k in KINDS}
    except Exception as e:  # noqa: BLE001 — storage failure surfaces as FAILED
        return StageResult(status="FAILED",
                           error=f"reporting: cannot load datasets: {e}"[:300])
    aggregates = rep.compute_aggregates(frames)
    counts = {
        "findings": len(inputs["findings"]),
        "evidence_items": sum(f["evidence_count"] for f in inputs["findings"]),
        "metrics_computed": len(inputs["metrics"]),
        "validation_warnings": sum(
            1 for v in inputs["validation"] if v["status"] == "WARNING"),
    }
    bundle = rep.build_numbers_bundle(inputs, aggregates, counts)

    system = load_prompt("reporting_system")
    user = json.dumps({
        "workflow": wf.human_ref, "period": wf.reporting_period,
        "numbers_bundle": bundle,
        "findings": inputs["findings"],
        "exceptions": rep.build_exceptions(inputs),
        "decisions": inputs["decisions"],
        "citations": inputs["citations"],
        **({"qa_feedback": regen_feedback} if regen_feedback else {}),
    })
    phash = prompt_hash(system, user)
    client = llm_client or get_llm_client()

    prose: ReportDraft | None = None
    usage = LLMUsage()
    errors: list[str] = []
    for _ in range(MAX_LLM_ATTEMPTS):
        try:
            result = await client.json_call(system, user, ReportDraft)
            prose = result.output
            usage.calls += result.usage.calls
            usage.tokens_in += result.usage.tokens_in
            usage.tokens_out += result.usage.tokens_out
            break
        except (LLMError, ValidationError) as e:
            errors.append(str(e)[:300])
    if prose is None:
        return StageResult(
            status="FAILED",
            error="reporting: LLM invalid output "
                  f"x{MAX_LLM_ATTEMPTS}: {'; '.join(errors)}"[:500])
    record_llm_usage(session, wf.id, "reporting", "reporting", usage, phash)

    version = (session.execute(
        select(func.max(Report.version)).where(
            Report.workflow_id == wf.id)).scalar() or 0) + 1
    sections, det_oqs = rep.assemble_report(
        inputs, aggregates, counts, prose.executive_summary,
        prose.open_questions)
    report = Report(
        workflow_id=wf.id, version=version, status="draft",
        sections=sections,
        body_markdown=rep.render_markdown(sections, wf.human_ref,
                                          wf.reporting_period),
    )
    session.add(report)
    session.flush()
    record_event(
        session, workflow_id=wf.id, actor_type="agent",
        actor="reporting_agent", action="report_drafted",
        entity_type="report", entity_id=report.id,
        summary=f"report v{version} drafted "
                f"({len(sections['findings'])} findings, "
                f"{len(det_oqs)} deterministic open questions"
                f"{', regen' if regen_feedback else ''})",
        details={"version": version, "report_id": str(report.id),
                 "regen": bool(regen_feedback)},
    )
    return StageResult(status="PASS",
                       outputs={"report_id": str(report.id),
                                "version": version,
                                "regen": bool(regen_feedback)})

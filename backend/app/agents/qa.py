"""QA / Audit Agent (§6.9, deterministic): final consistency gate.

Runs content + structural checks over the latest draft report. Content
failures of number-consistency get exactly one auto-regeneration cycle
(REPORTING v+1 via the reporting agent, then re-QA); structural failures
go straight to CP-7. Pass marks the report qa_passed and raises CP-6.
Both terminal paths self-transition QA -> WAITING_FOR_HUMAN (the engine
on_pass then no-ops), mirroring how data agents own BLOCKED.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents import base as agent_base
from app.agents.base import StageResult
from app.analytics import loader
from app.models import (
    AgentRun,
    DatasetVersion,
    Evidence,
    Finding,
    HumanCheckpoint,
    HumanDecision,
    Metric,
    Report,
    ValidationResult,
)
from app.orchestrator import states
from app.services import checkpoints, qa_verify
from app.services import reports as rep
from app.services.audit import record_event

AGENT_VERSION = "v1"
KINDS = ("claims", "premium", "exposure")
# only a prose-number failure can be fixed by regenerating prose
REGEN_ELIGIBLE = {"numbers_consistent"}


def _latest_report(session, wf) -> Report | None:
    return session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version.desc())).scalars().first()


def _is_stale(metrics: list, dvs: dict) -> bool:
    if not metrics or not dvs:
        return False
    ds_ts = [dv.created_at for dv in dvs.values() if dv.created_at]
    m_ts = [m.computed_at for m in metrics if m.computed_at]
    return bool(ds_ts and m_ts and min(m_ts) < max(ds_ts))


def _pool_from_bundle(bundle: dict) -> list[float]:
    return [float(v) for v in bundle["pool"]]


async def _run_checks(session, wf, report, storage) -> tuple[list[dict], dict]:
    """Returns (checks, context bundle). Pure reads + deterministic assembly."""
    metrics = session.execute(
        select(Metric).where(Metric.workflow_id == wf.id)).scalars().all()
    findings = session.execute(
        select(Finding).where(Finding.workflow_id == wf.id)
        .order_by(Finding.created_at)).scalars().all()
    ev_counts: dict[str, int] = {}
    for (fid,) in session.execute(
            select(Evidence.finding_id).where(
                Evidence.workflow_id == wf.id,
                Evidence.finding_id.is_not(None))).all():
        ev_counts[str(fid)] = ev_counts.get(str(fid), 0) + 1
    validation = session.execute(
        select(ValidationResult).where(
            ValidationResult.workflow_id == wf.id)).scalars().all()
    pending_blocking = session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.workflow_id == wf.id,
            HumanCheckpoint.status == "pending",
            HumanCheckpoint.blocking.is_(True))).scalars().all()
    resolved_blocking = session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.workflow_id == wf.id,
            HumanCheckpoint.status == "resolved",
            HumanCheckpoint.blocking.is_(True))).scalars().all()
    decided_cps = {str(d.checkpoint_id) for d in session.execute(
        select(HumanDecision).where(
            HumanDecision.workflow_id == wf.id,
            HumanDecision.checkpoint_id.is_not(None))).scalars().all()}
    latest_runs: dict[str, str] = {}
    for r in session.execute(
            select(AgentRun).where(AgentRun.workflow_id == wf.id)
            .order_by(AgentRun.started_at.desc())).scalars().all():
        latest_runs.setdefault((r.stage, r.agent), r.status)
    sections = report.sections or {}
    checks: list[dict] = []

    # 1. numbers_consistent (content — regen-eligible)
    inputs = rep.load_report_inputs(session, wf)
    dvs = inputs["datasets"]
    try:
        frames = {k: await loader.load_processed(
            storage, dvs[k].storage_path, dvs[k].checksum) for k in KINDS
            if k in dvs}
        aggregates = rep.compute_aggregates(frames) if len(frames) == 3 else {}
    except Exception:  # noqa: BLE001 — missing aggregates shrink the pool
        aggregates = {}
    counts = {
        "findings": len(findings),
        "evidence_items": sum(ev_counts.values()),
        "metrics_computed": len(metrics),
        "validation_warnings": sum(
            1 for v in validation if v.status == "WARNING"),
    }
    bundle = rep.build_numbers_bundle(inputs, aggregates, counts)
    texts = [sections.get("executive_summary") or ""] \
        + list(sections.get("open_questions") or [])
    mismatches = qa_verify.verify_prose(texts, _pool_from_bundle(bundle))
    checks.append({"check": "numbers_consistent",
                   "status": "PASS" if not mismatches else "FAIL",
                   "details": {"checked": sum(
                       len(qa_verify.extract_numerals(t)) for t in texts),
                       "mismatches": mismatches}})

    # 2. required_metrics (structural)
    have = {(m.metric_key, tuple(sorted((m.dimensions or {}).items())))
            for m in metrics if m.value is not None}
    missing = [k for k in rep.REQUIRED_PORTFOLIO_METRICS
               if (k, ()) not in have]
    checks.append({"check": "required_metrics",
                   "status": "PASS" if not missing else "FAIL",
                   "details": {"missing": missing}})

    # 3. evidence_complete (structural)
    no_evidence = [str(f.id) for f in findings
                   if ev_counts.get(str(f.id), 0) == 0]
    ok = bool(findings) and not no_evidence
    checks.append({"check": "evidence_complete",
                   "status": "PASS" if ok else "FAIL",
                   "details": {"findings": len(findings),
                               "without_evidence": no_evidence}})

    # 4. no_unresolved_red (structural)
    checks.append({"check": "no_unresolved_red",
                   "status": "PASS" if not pending_blocking else "FAIL",
                   "details": {"pending": [
                       {"type": c.checkpoint_type, "title": c.title}
                       for c in pending_blocking]}})

    # 5. warnings_visible (structural)
    exc_ids = {e.get("check_id") for e in sections.get("exceptions", [])
               if e.get("check_id")}
    invisible = [v.check_id for v in validation
                 if v.status in ("WARNING", "BLOCKER", "ACCEPTED_EXCEPTION")
                 and v.check_id not in exc_ids]
    checks.append({"check": "warnings_visible",
                   "status": "PASS" if not invisible else "FAIL",
                   "details": {"invisible": invisible}})

    # 6. stages_succeeded (structural)
    need = {("intake", "intake"), ("data_prep", "data_prep"),
            ("validation", "validation"), ("analysis", "analysis"),
            ("insight", "insight"), ("knowledge", "knowledge"),
            ("reporting", "reporting")}
    bad = sorted(f"{s}/{a}" for s, a in need
                 if latest_runs.get((s, a)) != "succeeded")
    checks.append({"check": "stages_succeeded",
                   "status": "PASS" if not bad else "FAIL",
                   "details": {"not_succeeded": bad}})

    # 7. decisions_recorded (structural)
    undecided = [c.checkpoint_type for c in resolved_blocking
                 if str(c.id) not in decided_cps]
    checks.append({"check": "decisions_recorded",
                   "status": "PASS" if not undecided else "FAIL",
                   "details": {"undecided": undecided}})

    # 8. metrics_fresh (informational — refresh happens before checks)
    checks.append({"check": "metrics_fresh", "status": "PASS",
                   "details": {}})
    return checks, {"metrics": metrics, "bundle_pool": bundle["pool"]}


def _storage():
    from app.storage.supabase import get_storage

    return get_storage()


def _fail_names(checks: list[dict]) -> list[str]:
    return [c["check"] for c in checks if c["status"] == "FAIL"]


def _pass_qa(session, wf, report, checks: list[dict], regenerated: bool,
             refreshed: bool) -> StageResult:
    report.status = "qa_passed"
    report.qa_result = {"passed": True, "checks": checks,
                        "regenerated": regenerated,
                        "refreshed_metrics": refreshed,
                        "report_version": report.version}
    session.flush()
    pending = session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.workflow_id == wf.id,
            HumanCheckpoint.status == "pending")
        .order_by(HumanCheckpoint.raised_at)).scalars().all()
    cp = checkpoints.raise_checkpoint(
        session, wf, checkpoint_type="final_approval", severity="red",
        blocking=True,
        title=f"Final approval: {wf.human_ref} report v{report.version}",
        context={
            "report_id": str(report.id), "version": report.version,
            "checklist": {"data": True, "calculations": True,
                          "findings_traceable": True,
                          "exceptions_reviewed": True, "qa_passed": True},
            "open_checkpoints": [
                {"type": c.checkpoint_type, "severity": c.severity,
                 "title": c.title} for c in pending],
        },
        options=[{"decision": "approve", "label": "Approve"},
                 {"decision": "request_revision",
                  "label": "Request Revision"},
                 {"decision": "reject", "label": "Reject"}],
    )
    states.apply_transition(session, wf, states.WAITING_FOR_HUMAN,
                            actor_type="agent", actor="qa_agent",
                            reason=f"QA passed report v{report.version} — CP-6")
    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="qa_agent",
        action="qa_passed", entity_type="report", entity_id=report.id,
        summary=f"QA passed report v{report.version} "
                f"({'regen' if regenerated else 'first pass'})",
        details={"report_id": str(report.id), "version": report.version,
                 "checkpoint_id": str(cp.id), "regenerated": regenerated,
                 "refreshed_metrics": refreshed},
    )
    return StageResult(status="PASS",
                       outputs={"report_id": str(report.id),
                                "version": report.version,
                                "qa_passed": True, "regenerated": regenerated,
                                "checkpoint_id": str(cp.id)})


def _fail_cp7(session, wf, report, checks: list[dict], fails: list[str],
              regenerated: bool, refreshed: bool,
              regen_error: str | None = None) -> StageResult:
    report.qa_result = {"passed": False, "checks": checks,
                        "regenerated": regenerated,
                        "refreshed_metrics": refreshed,
                        "report_version": report.version,
                        "regen_error": regen_error}
    session.flush()
    failed = [c for c in checks if c["status"] == "FAIL"]
    mismatches = [m for c in failed for m in
                  c.get("details", {}).get("mismatches", [])]
    cp = checkpoints.raise_checkpoint(
        session, wf, checkpoint_type="qa_failure", severity="red",
        blocking=True,
        title=f"QA failure: report v{report.version} "
              f"({', '.join(fails)})",
        context={"report_id": str(report.id), "version": report.version,
                 "failed_checks": failed, "mismatches": mismatches,
                 "regen_error": regen_error},
        options=[{"decision": "request_revision",
                  "label": "Request Revision"},
                 {"decision": "escalate", "label": "Escalate"}],
    )
    states.apply_transition(session, wf, states.WAITING_FOR_HUMAN,
                            actor_type="agent", actor="qa_agent",
                            reason=f"QA failed report v{report.version} — CP-7")
    record_event(
        session, workflow_id=wf.id, actor_type="agent", actor="qa_agent",
        action="qa_failed", entity_type="report", entity_id=report.id,
        summary=f"QA failed report v{report.version}: {', '.join(fails)}",
        details={"report_id": str(report.id), "version": report.version,
                 "failed_checks": fails, "checkpoint_id": str(cp.id),
                 "regenerated": regenerated},
    )
    return StageResult(status="PASS",
                       outputs={"report_id": str(report.id),
                                "version": report.version,
                                "qa_passed": False, "regenerated": regenerated,
                                "checkpoint_id": str(cp.id),
                                "failed_checks": fails})


async def run_qa(ctx, llm_client=None) -> StageResult:
    session, wf = ctx.session, ctx.workflow
    report = _latest_report(session, wf)
    if report is None:
        return StageResult(status="FAILED", error="qa: no draft report")
    refreshed = False

    # staleness (§13.7): metrics older than the latest dataset upload are
    # recomputed via the analysis agent before any check runs
    metrics = session.execute(
        select(Metric).where(Metric.workflow_id == wf.id)).scalars().all()
    dvs = {dv.kind: dv for dv in session.execute(
        select(DatasetVersion).where(
            DatasetVersion.workflow_id == wf.id)).scalars().all()}
    if _is_stale(metrics, dvs):
        from app.agents.analysis import run_analysis

        result = await agent_base.run_agent(
            ctx, "analysis", "analysis", run_analysis)
        if result.status == "FAILED":
            return StageResult(
                status="FAILED",
                error=f"qa: analysis refresh failed: {result.error}"[:300])
        refreshed = True
        record_event(
            session, workflow_id=wf.id, actor_type="agent",
            actor="qa_agent", action="metrics_refreshed",
            entity_type="workflow", entity_id=wf.id,
            summary="stale metrics recomputed before QA checks",
            details={})

    checks, _cx = await _run_checks(session, wf, report, ctx.storage)
    # stamp freshness outcome onto the informational check
    for c in checks:
        if c["check"] == "metrics_fresh":
            c["details"] = {"refreshed": refreshed}
    fails = _fail_names(checks)
    if not fails:
        return _pass_qa(session, wf, report, checks, regenerated=False,
                        refreshed=refreshed)

    structural = [f for f in fails if f not in REGEN_ELIGIBLE]
    if structural:
        # regenerating prose cannot fix data/state failures — straight to CP-7
        return _fail_cp7(session, wf, report, checks, fails,
                         regenerated=False, refreshed=refreshed)

    # numbers-only failure: exactly one auto-regeneration cycle (§13.7)
    mismatches = [m for c in checks if c["status"] == "FAIL"
                  for m in c.get("details", {}).get("mismatches", [])]
    feedback = ("Previous draft failed QA number-consistency. Fix these "
                "EXACT numerals by copying bundle values verbatim "
                "(1 decimal); cite nothing outside the bundle:\n" + "\n".join(
                    f"- {m['message']}" for m in mismatches))
    regen_error: str | None = None
    try:
        from app.agents.reporting import run_reporting

        regen = await agent_base.run_agent(
            ctx, "reporting", "reporting",
            lambda c: run_reporting(c, llm_client=llm_client,
                                    regen_feedback=feedback))
        if regen.status == "FAILED":
            regen_error = regen.error
    except Exception as e:  # noqa: BLE001 — regen must never crash QA
        regen_error = f"{type(e).__name__}: {e}"[:300]
    if regen_error is not None:
        return _fail_cp7(session, wf, report, checks, fails,
                         regenerated=False, refreshed=refreshed,
                         regen_error=regen_error)

    report2 = _latest_report(session, wf)
    checks2, _cx2 = await _run_checks(session, wf, report2, ctx.storage)
    fails2 = _fail_names(checks2)
    if not fails2:
        return _pass_qa(session, wf, report2, checks2, regenerated=True,
                        refreshed=refreshed)
    return _fail_cp7(session, wf, report2, checks2, fails2,
                     regenerated=True, refreshed=refreshed)

"""Decisions router (§10/§12): the ONLY path out of BLOCKED/WAITING_FOR_HUMAN.

Covers every human gate: CP-1 (input exception), CP-3 (schema mapping,
yellow), the data-prep validation blocker, CP-2 (validation blocker), CP-4
(assumption variance), CP-5 (finding review, yellow), CP-6 (final approval)
and CP-7 (QA failure). Targets: exactly one of checkpoint_id / finding_id /
report_id per the §10 contract.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.actor import get_current_actor
from app.db import get_session
from app.models import (
    AgentRun,
    DatasetVersion,
    Evidence,
    Finding,
    HumanCheckpoint,
    HumanDecision,
    Metric,
    Report,
    User,
    ValidationResult,
    Workflow,
)
from app.orchestrator import engine, states
from app.schemas.workflow import DecisionRequest
from app.services import checkpoints as cp_svc
from app.services.audit import record_event

router = APIRouter(prefix="/workflows", tags=["decisions"])

CP1_DECISIONS = {"select_file", "reject_data", "request_rerun"}
CP3_DECISIONS = {"confirm_mapping", "ignore_column"}
PREP_BLOCKER_DECISIONS = {"accept_exception", "request_rerun", "reject_data"}
CP4_DECISIONS = {"no_change_required", "monitor", "investigate_further",
                 "review_assumption", "escalate"}
# CP-5: yellow finding review — never pauses the machine (except the
# investigate_further re-run, which needs a parked workflow)
CP5_DECISIONS = {"accept", "reject", "override", "monitor", "comment",
                 "investigate_further"}
FINDING_STATUS = {"accept": "accepted", "reject": "rejected",
                  "override": "overridden", "monitor": "monitoring",
                  "investigate_further": "investigate_further"}
CP6_DECISIONS = {"approve", "request_revision", "reject"}
CP7_DECISIONS = {"request_revision", "escalate"}
# checkpoint types raised by the QA agent (§6.9)
REPORT_CHECKPOINTS = {"final_approval": CP6_DECISIONS,
                      "qa_failure": CP7_DECISIONS}

RESTDANDARDIZE_ELIGIBLE = {states.INGESTING, states.VALIDATING, states.VALIDATED}


def _check_investigation_budget(cfg: dict) -> int:
    """Shared 1x bound for CP-4/CP-5 investigate_further (§10 decision map)."""
    rounds = int(cfg.get("investigation_rounds", 0))
    if rounds >= 1:
        raise HTTPException(409, "investigation already re-run once "
                                 "(bounded 1x)", {"code": "bounded"})
    return rounds


def _detach_decided_findings(session: Session, wf: Workflow) -> int:
    """An insight re-run deletes prior insight findings (evidence cascades),
    but human_decisions.finding_id has no ON DELETE action — null the FK
    first, stashing the original id in payload so the audit trail keeps
    the linkage."""
    insight_ids = {str(f.id) for f in session.execute(
        select(Finding).where(Finding.workflow_id == wf.id,
                              Finding.agent == "insight")).scalars().all()}
    n = 0
    for d in session.execute(
            select(HumanDecision).where(
                HumanDecision.workflow_id == wf.id,
                HumanDecision.finding_id.is_not(None))).scalars().all():
        if str(d.finding_id) in insight_ids:
            payload = dict(d.payload or {})
            payload["detached_finding_id"] = str(d.finding_id)
            d.payload = payload
            d.finding_id = None
            n += 1
    return n


def _latest_report(session: Session, wf: Workflow) -> Report | None:
    return session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version.desc())).scalars().first()


def _resolve_report_checkpoints(session: Session, wf: Workflow, actor: User,
                                decision: str, rationale: str,
                                finding_id=None) -> int:
    """A tail re-run (CP-5 investigate_further) invalidates any pending
    final_approval/qa_failure checkpoint: resolve each with its own linked
    decision row (append-only — the parked CP-6/CP-7 decision history
    survives), so the re-run QA raises a fresh checkpoint."""
    n = 0
    for cp in session.execute(
            select(HumanCheckpoint).where(
                HumanCheckpoint.workflow_id == wf.id,
                HumanCheckpoint.status == "pending",
                HumanCheckpoint.checkpoint_type.in_(REPORT_CHECKPOINTS))
            .order_by(HumanCheckpoint.raised_at)).scalars().all():
        row = HumanDecision(
            workflow_id=wf.id, checkpoint_id=cp.id, finding_id=finding_id,
            decision=decision, rationale=rationale, payload={},
            decided_by=actor.id)
        session.add(row)
        session.flush()
        cp_svc.resolve(session, wf, cp, row, actor.name)
        n += 1
    return n


def _prep_tail_rerun(session: Session, wf: Workflow, body: DecisionRequest,
                     actor: User, finding_id) -> None:
    """CP-5 investigate_further (§10): fresh insight→knowledge→reporting→QA
    tail. Requires a parked workflow; findings are replaced (linked to
    prior by the insight agent), the stale draft is superseded, and any
    pending report checkpoint is resolved-with-linkage (see above)."""
    if wf.status != states.WAITING_FOR_HUMAN:
        raise HTTPException(409, "investigate_further needs a parked "
                                 "workflow (decide finding reviews while "
                                 "QA is running, re-run once parked)",
                            {"code": "not_parked"})
    cfg = dict(wf.config or {})
    rounds = _check_investigation_budget(cfg)
    _detach_decided_findings(session, wf)
    for stage in ("insight", "knowledge", "reporting", "qa"):
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == stage))
    latest = _latest_report(session, wf)
    if latest is not None and latest.status != "approved":
        latest.status = "superseded"
    _resolve_report_checkpoints(
        session, wf, actor, "investigate_further", body.rationale.strip(),
        finding_id=finding_id)
    cfg["investigation_rounds"] = rounds + 1
    cfg["insight_focus"] = body.payload
    wf.config = cfg
    states.apply_transition(session, wf, states.INVESTIGATING,
                            actor_type="human", actor=actor.name,
                            reason="CP-5: investigate further (bounded re-run)")


def _apply_finding(session: Session, wf: Workflow, body: DecisionRequest,
                   actor: User, finding: Finding, decision_id) -> str:
    """CP-5 (§12): status update + audit; only investigate_further moves
    the machine (bounded tail re-run). Comment never touches status."""
    prior = session.execute(select(HumanDecision).where(
        HumanDecision.workflow_id == wf.id,
        HumanDecision.finding_id == finding.id)).scalars().all()
    if body.decision == "comment":
        pass
    elif body.decision == "investigate_further":
        finding.status = "investigate_further"
        finding.updated_at = datetime.now(UTC)
        _prep_tail_rerun(session, wf, body, actor, finding.id)
    else:
        finding.status = FINDING_STATUS[body.decision]
        finding.updated_at = datetime.now(UTC)
    record_event(
        session, workflow_id=wf.id, actor_type="human", actor=actor.name,
        action=f"finding_{body.decision}", entity_type="finding",
        entity_id=finding.id,
        summary=f"{finding.title[:120]} — {body.decision}"
                + (f": {body.rationale[:150]}" if body.rationale else ""),
        details={"finding_id": str(finding.id),
                 "decision_id": str(decision_id),
                 "supersedes_prior": bool(prior)})
    return wf.status


def _require_latest_qa_passed(session: Session, wf: Workflow,
                              report: Report) -> None:
    latest = _latest_report(session, wf)
    if latest is None or latest.id != report.id:
        raise HTTPException(409, "report superseded by "
                                 f"v{latest.version if latest else '?'} — "
                                 "decide on the latest version",
                            {"code": "not_latest"})
    if report.status != "qa_passed":
        raise HTTPException(409, f"report v{report.version} is "
                                 f"{report.status}, not qa_passed",
                            {"code": "not_qa_passed"})


def _request_revision(session: Session, wf: Workflow, actor: User,
                      report: Report, from_cp: str) -> str:
    """CP-6/CP-7 request_revision (§10): reporting regenerates v+1, then QA
    re-runs. The revised-away draft is marked superseded (history kept)."""
    latest = _latest_report(session, wf)
    if latest is None or latest.id != report.id:
        raise HTTPException(409, "report superseded — revise the latest "
                                 "version", {"code": "not_latest"})
    report.status = "superseded"
    for stage in ("reporting", "qa"):
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == stage))
    states.apply_transition(session, wf, states.REPORTING, actor_type="human",
                            actor=actor.name,
                            reason=f"{from_cp}: revision requested — regen "
                                   f"v{report.version + 1}")
    return wf.status


def _apply_cp6(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User, cp: HumanCheckpoint, report: Report) -> str:
    """CP-6 (§12): approve locks the report and completes the workflow;
    revision regenerates; reject kills the run. No approve-without-QA."""
    _require_latest_qa_passed(session, wf, report)
    if body.decision == "approve":
        now = datetime.now(UTC)
        report.status = "approved"
        report.approved_by = actor.id
        report.approved_at = now
        states.apply_transition(session, wf, states.APPROVED,
                                actor_type="human", actor=actor.name,
                                reason=f"CP-6: report v{report.version} approved"
                                + (f" — {body.rationale.strip()}"
                                   if body.rationale.strip() else ""))
        states.apply_transition(session, wf, states.COMPLETED,
                                actor_type="human", actor=actor.name,
                                reason="audit finalized — workflow complete")
        wf.completed_at = now
    elif body.decision == "request_revision":
        _request_revision(session, wf, actor, report, "CP-6")
    else:  # reject (rationale enforced by enum rule)
        states.apply_transition(session, wf, states.REJECTED,
                                actor_type="human", actor=actor.name,
                                reason=f"CP-6: report v{report.version} rejected"
                                       f" — {body.rationale.strip()}")
    record_event(
        session, workflow_id=wf.id, actor_type="human", actor=actor.name,
        action=f"report_{body.decision}", entity_type="report",
        entity_id=report.id,
        summary=f"report v{report.version} {body.decision}"
                + (f": {body.rationale[:150]}" if body.rationale else ""),
        details={"report_id": str(report.id), "version": report.version,
                 "checkpoint": str(cp.id)})
    return wf.status


def _apply_cp7(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User, cp: HumanCheckpoint, report: Report) -> str:
    """CP-7 (§12): revision regenerates; escalate stays parked + flagged.
    There is no approve-anyway path — QA integrity is non-negotiable
    (approve is not in CP7_DECISIONS, so it 400s before reaching here)."""
    latest = _latest_report(session, wf)
    if latest is None or latest.id != report.id:
        raise HTTPException(409, "report superseded — decide on the latest "
                                 "version", {"code": "not_latest"})
    if body.decision == "request_revision":
        _request_revision(session, wf, actor, report, "CP-7")
    else:  # escalate — stays parked, flagged
        cfg = dict(wf.config or {})
        cfg["escalated"] = True
        wf.config = cfg
    record_event(
        session, workflow_id=wf.id, actor_type="human", actor=actor.name,
        action=f"report_{body.decision}", entity_type="report",
        entity_id=report.id,
        summary=f"report v{report.version} {body.decision}"
                + (f": {body.rationale[:150]}" if body.rationale else ""),
        details={"report_id": str(report.id), "version": report.version,
                 "checkpoint": str(cp.id)})
    return wf.status


def _apply_cp4(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User) -> str:
    """CP-4 (§12): the actuary decides about the assumption variance — the
    system records the decision, never a new assumption value."""
    cfg = dict(wf.config or {})
    if body.decision in ("no_change_required", "monitor"):
        states.apply_transition(session, wf, states.REPORTING, actor_type="human",
                                actor=actor.name,
                                reason=f"CP-4: {body.decision} — monitor")
    elif body.decision == "investigate_further":
        rounds = _check_investigation_budget(cfg)
        cfg["investigation_rounds"] = rounds + 1
        cfg["insight_focus"] = body.payload
        wf.config = cfg
        # fresh investigation round; the re-run replaces findings with links
        _detach_decided_findings(session, wf)
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id,
            AgentRun.stage.in_(("insight", "knowledge"))))
        states.apply_transition(session, wf, states.INVESTIGATING,
                                actor_type="human", actor=actor.name,
                                reason="CP-4: investigate further (bounded re-run)")
    elif body.decision == "review_assumption":
        cfg["assumption_review_requested"] = True
        cfg["assumption_review_note"] = body.rationale.strip()
        wf.config = cfg
        states.apply_transition(session, wf, states.REPORTING, actor_type="human",
                                actor=actor.name,
                                reason="CP-4: assumption review requested")
    else:  # escalate — stays parked, flagged
        cfg["escalated"] = True
        wf.config = cfg
    return wf.status


def _reset_for_restandardize(session: Session, wf: Workflow) -> None:
    """confirm_mapping: drop prep/validation/analysis/insight/knowledge outputs
    so the engine re-runs them (stale metrics/findings must not survive:
    resume skips succeeded stages)."""
    for stage in ("data_prep", "validation", "analysis", "insight",
                  "knowledge"):
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == stage))
    session.execute(delete(DatasetVersion).where(DatasetVersion.workflow_id == wf.id))
    session.execute(delete(ValidationResult).where(ValidationResult.workflow_id == wf.id))
    session.execute(delete(Metric).where(Metric.workflow_id == wf.id))
    session.execute(delete(Evidence).where(Evidence.workflow_id == wf.id))
    session.execute(delete(Finding).where(Finding.workflow_id == wf.id))
    wf.stage = "data_prep"
    wf.error = None
    if wf.status != states.INGESTING:
        states.apply_transition(session, wf, states.INGESTING, actor_type="human",
                                actor="checkpoint_decision",
                                reason="CP-3 confirm_mapping — re-standardize")


def _apply_cp3(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User) -> str:
    if body.decision == "confirm_mapping":
        if wf.status not in RESTDANDARDIZE_ELIGIBLE:
            raise HTTPException(409, "too late to remap (analysis already ran)",
                                {"code": "too_late"})
        column = body.payload.get("column")
        canonical = body.payload.get("canonical")
        if not column or not canonical:
            raise HTTPException(400, "confirm_mapping requires payload.column and "
                                     "payload.canonical", {"code": "bad_payload"})
        cfg = dict(wf.config or {})
        overrides = dict(cfg.get("column_overrides", {}))
        overrides[column] = canonical
        cfg["column_overrides"] = overrides
        wf.config = cfg
        _reset_for_restandardize(session, wf)
    else:  # ignore_column
        cfg = dict(wf.config or {})
        ignored = list(cfg.get("ignored_columns", []))
        for col in body.payload.get("columns", [body.payload.get("column")]):
            if col and col not in ignored:
                ignored.append(col)
        cfg["ignored_columns"] = ignored
        wf.config = cfg
    return wf.status


def _mark_blockers_accepted(session: Session, wf: Workflow, decision_id,
                            rationale: str, actor: User) -> int:
    """CP-2 accept: every BLOCKER validation check -> ACCEPTED_EXCEPTION."""
    rows = session.execute(select(ValidationResult).where(
        ValidationResult.workflow_id == wf.id,
        ValidationResult.status == "BLOCKER")).scalars().all()
    now = datetime.now(UTC)
    for r in rows:
        r.status = "ACCEPTED_EXCEPTION"
        r.resolved_by = actor.id
        r.resolved_at = now
        r.resolution = {"decision_id": str(decision_id), "rationale": rationale,
                        "decided_by": actor.name, "decided_at": now.isoformat()}
    return len(rows)


def _apply_cp2(session: Session, wf: Workflow, body: DecisionRequest,
               actor: User, decision_id) -> str:
    """CP-2 (§12): accept carries the exception into the report; re-run
    re-executes validation only (datasets are kept — results upsert)."""
    if body.decision == "accept_exception":
        n = _mark_blockers_accepted(session, wf, decision_id,
                                    body.rationale.strip(), actor)
        cfg = dict(wf.config or {})
        accepted = list(cfg.get("accepted_exceptions", []))
        accepted.append({
            "source": "validation", "decision_id": str(decision_id),
            "note": body.rationale.strip(),
        })
        cfg["accepted_exceptions"] = accepted
        wf.config = cfg
        states.apply_transition(session, wf, states.VALIDATED, actor_type="human",
                                actor=actor.name,
                                reason=f"CP-2 accepted ({n} checks) — carried "
                                       f"into report Exceptions")
    elif body.decision == "request_rerun":
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == "validation"))
        states.apply_transition(session, wf, states.VALIDATING, actor_type="human",
                                actor=actor.name, reason="CP-2: re-run validation")
    else:  # reject_data
        states.apply_transition(session, wf, states.REJECTED, actor_type="human",
                                actor=actor.name, reason="CP-2: data rejected")
    return wf.status


def _apply_prep_blocker(session: Session, wf: Workflow, body: DecisionRequest,
                        actor: User, cp: HumanCheckpoint,
                        decision_id) -> str:
    if (cp.context or {}).get("stage") == "validation":
        return _apply_cp2(session, wf, body, actor, decision_id)
    if body.decision == "accept_exception":
        cfg = dict(wf.config or {})
        accepted = list(cfg.get("accepted_exceptions", []))
        accepted.append({
            "source": "data_prep", "decision_id": str(decision_id),
            "note": body.rationale or "accepted by actuary",
        })
        cfg["accepted_exceptions"] = accepted
        wf.config = cfg
        states.apply_transition(session, wf, states.VALIDATING, actor_type="human",
                                actor=actor.name,
                                reason="data-prep blocker accepted as exception")
    elif body.decision == "request_rerun":
        session.execute(delete(AgentRun).where(
            AgentRun.workflow_id == wf.id, AgentRun.stage == "data_prep"))
        states.apply_transition(session, wf, states.INGESTING, actor_type="human",
                                actor=actor.name, reason="re-run data prep")
    else:  # reject_data
        states.apply_transition(session, wf, states.REJECTED, actor_type="human",
                                actor=actor.name, reason="data rejected at prep blocker")
    return wf.status


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
        code = "rationale_required" if "rationale" in err else "bad_decision"
        raise HTTPException(422, err, {"code": code})

    targets = [t for t in (body.checkpoint_id, body.finding_id,
                           body.report_id) if t]
    if len(targets) != 1:
        raise HTTPException(400, "exactly one of checkpoint_id, finding_id, "
                                 "report_id is required",
                            {"code": "bad_target"})

    cp: HumanCheckpoint | None = None
    finding: Finding | None = None
    report: Report | None = None
    if body.finding_id:
        try:
            finding = session.get(Finding, uuid.UUID(str(body.finding_id)))
        except (ValueError, AttributeError):
            finding = None
        if finding is None or finding.workflow_id != wf.id:
            raise HTTPException(400, "finding does not belong to this "
                                     "workflow", {"code": "bad_target"})
        if body.decision not in CP5_DECISIONS:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for "
                                "a finding review",
                                {"code": "bad_decision"})
    elif body.report_id:
        try:
            report = session.get(Report, uuid.UUID(str(body.report_id)))
        except (ValueError, AttributeError):
            report = None
        if report is None or report.workflow_id != wf.id:
            raise HTTPException(400, "report does not belong to this "
                                     "workflow", {"code": "bad_target"})
        # the pending report checkpoint determines CP-6 vs CP-7; no pending
        # review means nothing to decide (in particular: no approve-anyway)
        cp = session.execute(
            select(HumanCheckpoint).where(
                HumanCheckpoint.workflow_id == wf.id,
                HumanCheckpoint.status == "pending",
                HumanCheckpoint.checkpoint_type.in_(REPORT_CHECKPOINTS))
            .order_by(HumanCheckpoint.raised_at)).scalars().first()
        if cp is None or (cp.context or {}).get("report_id") != str(report.id):
            raise HTTPException(409, "no pending final-approval/QA review "
                                     "for this report",
                                {"code": "no_pending_review"})
        allowed = REPORT_CHECKPOINTS[cp.checkpoint_type]
        if body.decision not in allowed:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for "
                                f"{cp.checkpoint_type}",
                                {"code": "bad_decision"})
        cp_type = cp.checkpoint_type  # report path joins checkpoint dispatch
    else:
        try:
            cp = session.get(HumanCheckpoint, uuid.UUID(str(body.checkpoint_id)))
        except (ValueError, AttributeError):
            cp = None
        if cp is None or cp.workflow_id != wf.id:
            raise HTTPException(400, "checkpoint does not belong to this "
                                     "workflow", {"code": "bad_target"})
        if cp.status != "pending":
            raise HTTPException(409, "checkpoint already resolved",
                                {"code": "not_pending"})

        cp_type = cp.checkpoint_type
        if cp_type == "input_exception":
            allowed = CP1_DECISIONS
        elif cp_type == "schema_mapping":
            allowed = CP3_DECISIONS
        elif cp_type == "validation_blocker":
            allowed = PREP_BLOCKER_DECISIONS
        elif cp_type == "assumption_variance":
            allowed = CP4_DECISIONS
        elif cp_type in REPORT_CHECKPOINTS:
            allowed = REPORT_CHECKPOINTS[cp_type]
            try:
                report = session.get(
                    Report, uuid.UUID(str((cp.context or {}).get("report_id"))))
            except (ValueError, AttributeError, TypeError):
                report = None
            if report is None or report.workflow_id != wf.id:
                raise HTTPException(409, "review checkpoint has no report "
                                         "to decide on",
                                    {"code": "no_report"})
        else:
            raise HTTPException(400, f"checkpoint type '{cp_type}' not yet "
                                     "supported",
                                {"code": "not_implemented"})
        if body.decision not in allowed:
            raise HTTPException(400,
                                f"decision '{body.decision}' not valid for "
                                f"{cp_type}",
                                {"code": "bad_decision"})

    row = HumanDecision(
        workflow_id=wf.id, checkpoint_id=cp.id if cp is not None else None,
        finding_id=finding.id if finding is not None else None,
        report_id=report.id if report is not None else None,
        decision=body.decision,
        rationale=body.rationale.strip(), payload=body.payload, decided_by=actor.id,
    )
    session.add(row)
    session.flush()
    record_event(session, workflow_id=wf.id, actor_type="human", actor=actor.name,
                 action=f"decision_{body.decision}", entity_type="human_decision",
                 entity_id=row.id,
                 summary=body.rationale[:200] or body.decision,
                 details={"checkpoint": str(cp.id) if cp else None,
                          "finding": str(finding.id) if finding else None,
                          "report": str(report.id) if report else None,
                          "payload": body.payload})

    if finding is not None:
        new_status = _apply_finding(session, wf, body, actor, finding, row.id)
        supersedes = session.execute(select(HumanDecision).where(
            HumanDecision.workflow_id == wf.id,
            HumanDecision.finding_id == finding.id,
            HumanDecision.id != row.id)).scalars().first() is not None
        # investigate_further detaches the finding FK (the finding is about
        # to be replaced) — linkage survives in payload + audit
        if body.decision == "investigate_further":
            supersedes = True
        cp_svc.log_decision(session, wf, row)
        session.commit()
        if wf.status in states.RUNNABLE:
            engine.launch(wf.id)
        return {"workflow_status": new_status, "applied": [body.decision],
                "supersedes_prior": supersedes}

    if cp_type == "input_exception":
        new_status = cp_svc.apply_cp1_decision(session, wf, cp, body.decision,
                                               body.payload, actor)
    elif cp_type == "schema_mapping":
        new_status = _apply_cp3(session, wf, body, actor)
    elif cp_type == "assumption_variance":
        new_status = _apply_cp4(session, wf, body, actor)
    elif cp_type == "final_approval":
        new_status = _apply_cp6(session, wf, body, actor, cp, report)
    elif cp_type == "qa_failure":
        new_status = _apply_cp7(session, wf, body, actor, cp, report)
    else:
        new_status = _apply_prep_blocker(session, wf, body, actor, cp, row.id)

    cp_svc.resolve(session, wf, cp, row, actor.name)
    session.commit()
    cp_svc.log_decision(session, wf, row)

    if wf.status in states.RUNNABLE:
        engine.launch(wf.id)
    return {"workflow_status": new_status, "applied": [body.decision],
            "supersedes_prior": False}

"""Human-loop API tests (§10/§12 Phase 13): every decision->state mapping.

CP-5 finding review (finding_id target), CP-6 final approval (approve ->
COMPLETED, revision -> REPORTING v+1, reject -> REJECTED), CP-7 QA failure
(revision / escalate, no approve-anyway), exactly-one-target validation,
and audit completeness. Seeds rows directly (no engine) except the
revision->re-approve cycle, which drives the real engine tail.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.agents.reporting as reporting_mod
from app.llm.client import FakeLLMClient
from app.main import app
from app.models import (
    AgentRun,
    AuditEvent,
    DatasetVersion,
    Evidence,
    Finding,
    HumanCheckpoint,
    HumanDecision,
    Metric,
    Report,
    ValidationResult,
    Workflow,
)
from app.orchestrator import engine
from app.storage.supabase import get_storage

RATIONALE = "Known endorsement processing lag documented by the team"

CLAIMS_CSV = (
    "claim_id,policy_id,product,segment,region,claim_type,event_date,"
    "report_date,status,incurred_amount\n"
    "C1,P1,Commercial,Construction,South,Fire,2026-09-05,2026-09-06,Open,1000\n"
)
PREMIUM_CSV = (
    "policy_id,product,segment,region,period,written_premium,earned_premium\n"
    "P1,Commercial,Construction,South,2026-09,1000,1000\n"
)
EXPOSURE_CSV = (
    "policy_id,product,segment,region,period,active_policies,"
    "earned_exposure_units\n"
    "P1,Commercial,Construction,South,2026-09,1,1.0\n"
)
PROSE = {"executive_summary": "Portfolio loss ratio closed at 75.0%.",
         "open_questions": ["Anything else?"]}


def _wf(db_session, status="WAITING_FOR_HUMAN") -> Workflow:
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", portfolio="General Insurance",
                  status=status)
    db_session.add(wf)
    db_session.commit()
    return wf


def _finding(db_session, wf, status="draft") -> Finding:
    f = Finding(workflow_id=wf.id, agent="insight", title="Drift up",
                narrative="Evidence: up.\nConclusion: watch.",
                severity="high", confidence=0.9, status=status)
    db_session.add(f)
    db_session.commit()
    return f


def _report(db_session, wf, version=1, status="qa_passed") -> Report:
    r = Report(workflow_id=wf.id, version=version, status=status,
               sections={"executive_summary": "LR 75.0%.",
                         "open_questions": []},
               body_markdown="report", qa_result={"passed": True})
    db_session.add(r)
    db_session.commit()
    return r


def _cp(db_session, wf, ctype, report=None) -> HumanCheckpoint:
    cp = HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type=ctype, severity="red",
        blocking=True, title=f"{ctype} for test",
        context=({"report_id": str(report.id), "version": report.version}
                 if report else {}),
        options=[])
    db_session.add(cp)
    db_session.commit()
    return cp


def _post(client, wid, payload):
    return client.post(f"/workflows/{wid}/decisions", json=payload)


def _decisions(db_session, wf):
    return db_session.execute(
        select(HumanDecision).where(
            HumanDecision.workflow_id == wf.id)).scalars().all()


def _actions(db_session, wf):
    return [e.action for e in db_session.execute(
        select(AuditEvent).where(
            AuditEvent.workflow_id == wf.id)).scalars().all()]


# --- target validation (§10 contract) ------------------------------------

def test_no_target_400(db_session):
    wf = _wf(db_session)
    with TestClient(app) as client:
        r = _post(client, str(wf.id), {"decision": "comment"})
        assert r.status_code == 400


def test_two_targets_400(db_session):
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    cp = _cp(db_session, wf, "final_approval")
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id), "finding_id": str(f.id),
                   "decision": "comment"})
        assert r.status_code == 400


def test_foreign_finding_400(db_session):
    wf = _wf(db_session)
    other = _wf(db_session)
    f = _finding(db_session, other)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "accept"})
        assert r.status_code == 400


# --- CP-5 finding review ---------------------------------------------------

def test_finding_accept_then_monitor_supersedes(db_session):
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "accept"})
        assert r.status_code == 202
        body = r.json()
        assert body["workflow_status"] == "WAITING_FOR_HUMAN"
        assert body["applied"] == ["accept"]
        assert body["supersedes_prior"] is False
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "monitor"})
        assert r.status_code == 202
        assert r.json()["supersedes_prior"] is True
    db_session.refresh(f)
    assert f.status == "monitoring"  # later wins
    rows = _decisions(db_session, wf)
    assert len(rows) == 2  # append-only: both kept
    assert all(d.finding_id == f.id and d.checkpoint_id is None
               for d in rows)
    actions = _actions(db_session, wf)
    assert "finding_accept" in actions and "finding_monitor" in actions
    assert "decision_accept" in actions


def test_finding_reject_needs_rationale(db_session):
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    with TestClient(app) as client:
        assert _post(client, str(wf.id),
                     {"finding_id": str(f.id),
                      "decision": "reject"}) .status_code == 422
        assert _post(client, str(wf.id),
                     {"finding_id": str(f.id), "decision": "reject",
                      "rationale": "too short"}) .status_code == 422
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "reject",
                   "rationale": RATIONALE})
        assert r.status_code == 202
    db_session.refresh(f)
    assert f.status == "rejected"


def test_finding_override_and_comment(db_session):
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    with TestClient(app) as client:
        assert _post(client, str(wf.id),
                     {"finding_id": str(f.id), "decision": "override",
                      "rationale": "short"}) .status_code == 422
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "override",
                   "rationale": RATIONALE})
        assert r.status_code == 202
    db_session.refresh(f)
    assert f.status == "overridden"
    f2 = _finding(db_session, wf)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"finding_id": str(f2.id), "decision": "comment",
                   "rationale": "noted for next review"})
        assert r.status_code == 202
    db_session.refresh(f2)
    assert f2.status == "draft"  # comment never touches status


def test_finding_bad_decision_400(db_session):
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "approve"})
        assert r.status_code == 400


def test_finding_investigate_needs_parked_workflow(db_session):
    wf = _wf(db_session, status="ANALYZED")
    f = _finding(db_session, wf)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id),
                   "decision": "investigate_further"})
        assert r.status_code == 409
    db_session.refresh(f)
    assert f.status == "draft"  # rolled back, nothing applied


def test_finding_investigate_further_tail_rerun(db_session):
    wf = _wf(db_session)
    f = _finding(db_session, wf)
    rep = _report(db_session, wf)
    cp = _cp(db_session, wf, "final_approval", report=rep)
    for stage, agent in (("insight", "insight"), ("knowledge", "knowledge"),
                         ("reporting", "reporting"), ("qa", "qa")):
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    db_session.commit()
    with TestClient(app) as client:
        # a prior finding decision exists (accept) — the re-run must not
        # trip the finding FK when insight replaces findings
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id), "decision": "accept"})
        assert r.status_code == 202
        r = _post(client, str(wf.id),
                  {"finding_id": str(f.id),
                   "decision": "investigate_further",
                   "payload": {"focus": "frequency"}})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "INVESTIGATING"
    db_session.refresh(wf)
    assert wf.status == "INVESTIGATING"
    assert wf.config["investigation_rounds"] == 1
    assert wf.config["insight_focus"] == {"focus": "frequency"}
    assert db_session.query(AgentRun).filter(
        AgentRun.stage.in_(("insight", "knowledge", "reporting",
                             "qa"))).count() == 0
    db_session.refresh(rep)
    assert rep.status == "superseded"
    db_session.refresh(cp)
    assert cp.status == "resolved"  # parked CP-6 resolved-with-linkage
    linked = [d for d in _decisions(db_session, wf)
              if d.checkpoint_id == cp.id]
    assert len(linked) == 1 and linked[0].decision == "investigate_further"
    # the accept row was detached (finding about to be replaced) but kept
    accept = [d for d in _decisions(db_session, wf)
              if d.decision == "accept"]
    assert len(accept) == 1 and accept[0].finding_id is None
    assert accept[0].payload["detached_finding_id"] == str(f.id)
    db_session.refresh(f)
    assert f.status == "investigate_further"

    f2 = _finding(db_session, wf)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"finding_id": str(f2.id),
                   "decision": "investigate_further"})
        assert r.status_code == 409  # bounded 1x, shared with CP-4
    db_session.refresh(f2)
    assert f2.status == "draft"


# --- CP-6 final approval ----------------------------------------------------

def test_cp6_approve_completes(db_session):
    wf = _wf(db_session)
    rep = _report(db_session, wf)
    cp = _cp(db_session, wf, "final_approval", report=rep)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id), "decision": "approve",
                   "rationale": "reviewed — numbers trace to metrics"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "COMPLETED"
    db_session.refresh(wf)
    assert wf.status == "COMPLETED" and wf.completed_at is not None
    db_session.refresh(rep)
    assert rep.status == "approved" and rep.approved_at is not None
    assert rep.approved_by is not None
    db_session.refresh(cp)
    assert cp.status == "resolved"
    actions = _actions(db_session, wf)
    assert "report_approve" in actions
    assert "transition_waiting_for_human_to_approved" in actions
    assert "transition_approved_to_completed" in actions


def test_cp6_approve_by_report_id(db_session):
    wf = _wf(db_session)
    rep = _report(db_session, wf)
    _cp(db_session, wf, "final_approval", report=rep)
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"report_id": str(rep.id), "decision": "approve"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "COMPLETED"
    db_session.refresh(rep)
    assert rep.status == "approved"


def test_cp6_revision_regenerates(db_session):
    wf = _wf(db_session)
    rep = _report(db_session, wf)
    cp = _cp(db_session, wf, "final_approval", report=rep)
    for stage, agent in (("insight", "insight"),
                         ("reporting", "reporting"), ("qa", "qa")):
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    db_session.commit()
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id),
                   "decision": "request_revision",
                   "rationale": "tighten the summary wording"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REPORTING"
    db_session.refresh(wf)
    assert wf.status == "REPORTING"
    db_session.refresh(rep)
    assert rep.status == "superseded"
    remaining = {row.stage for row in db_session.query(AgentRun).all()}
    assert remaining == {"insight"}  # reporting + qa re-run, rest kept


def test_cp6_reject(db_session):
    wf = _wf(db_session)
    rep = _report(db_session, wf)
    cp = _cp(db_session, wf, "final_approval", report=rep)
    with TestClient(app) as client:
        assert _post(client, str(wf.id),
                     {"checkpoint_id": str(cp.id),
                      "decision": "reject"}).status_code == 422
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id), "decision": "reject",
                   "rationale": RATIONALE})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REJECTED"
    db_session.refresh(wf)
    assert wf.status == "REJECTED"


def test_cp6_guards(db_session):
    wf = _wf(db_session)
    old = _report(db_session, wf, version=1)
    new = _report(db_session, wf, version=2, status="draft")
    cp = _cp(db_session, wf, "final_approval", report=old)
    with TestClient(app) as client:
        # stale checkpoint version -> decide on latest
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id), "decision": "approve"})
        assert r.status_code == 409
        # no pending review at all -> no approve-without-QA
        r = _post(client, str(wf.id),
                  {"report_id": str(new.id), "decision": "approve"})
        assert r.status_code == 409


# --- CP-7 QA failure --------------------------------------------------------

def test_cp7_revision_and_escalate(db_session):
    wf = _wf(db_session)
    rep = _report(db_session, wf, status="draft")
    cp = _cp(db_session, wf, "qa_failure", report=rep)
    for stage, agent in (("reporting", "reporting"), ("qa", "qa")):
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    db_session.commit()
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id),
                   "decision": "request_revision",
                   "rationale": "fix the flagged numerals"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REPORTING"
    db_session.refresh(rep)
    assert rep.status == "superseded"

    wf2 = _wf(db_session)
    rep2 = _report(db_session, wf2, status="draft")
    cp2 = _cp(db_session, wf2, "qa_failure", report=rep2)
    with TestClient(app) as client:
        r = _post(client, str(wf2.id),
                  {"checkpoint_id": str(cp2.id), "decision": "escalate",
                   "rationale": "needs methodology owner input first"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "WAITING_FOR_HUMAN"
    db_session.refresh(wf2)
    assert wf2.status == "WAITING_FOR_HUMAN"  # stays parked + flagged
    assert wf2.config.get("escalated") is True
    db_session.refresh(cp2)
    assert cp2.status == "resolved"


def test_cp7_no_approve_anyway(db_session):
    wf = _wf(db_session)
    rep = _report(db_session, wf, status="draft")
    cp = _cp(db_session, wf, "qa_failure", report=rep)
    with TestClient(app) as client:
        assert _post(client, str(wf.id),
                     {"checkpoint_id": str(cp.id),
                      "decision": "approve"}).status_code == 400
        assert _post(client, str(wf.id),
                     {"report_id": str(rep.id),
                      "decision": "approve"}).status_code == 400
    db_session.refresh(rep)
    assert rep.status == "draft"


# --- revision -> regen -> re-approve cycle (real engine tail) ---------------

async def _seed_qa_ready(db_session, wf, storage):
    for kind, csv in (("claims", CLAIMS_CSV), ("premium", PREMIUM_CSV),
                      ("exposure", EXPOSURE_CSV)):
        path = f"workflows/{wf.id}/processed/{kind}.csv"
        await storage.upload_bytes(path, csv.encode())
        db_session.add(DatasetVersion(
            workflow_id=wf.id, kind=kind, source_file_ids=[],
            storage_path=path, row_count=1, column_map={},
            transform_log={}, checksum="x"))
    db_session.commit()
    for stage, agent in (("intake", "intake"), ("data_prep", "data_prep"),
                         ("validation", "validation"),
                         ("analysis", "analysis"), ("insight", "insight"),
                         ("knowledge", "knowledge")):
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    for key, dims, value in (
            ("loss_ratio", {}, 0.75), ("claim_frequency", {}, 1.0),
            ("claim_severity", {}, 1000.0), ("ave_variance", {}, 5.0)):
        db_session.add(Metric(
            workflow_id=wf.id, metric_key=key, dimensions=dims,
            period="2026-09", value=value, formula=f"{key} = f(inputs)"))
    f = Finding(workflow_id=wf.id, agent="insight", title="Drift up",
                narrative="Evidence: up.\nConclusion: watch.",
                severity="high", confidence=0.9, status="draft")
    db_session.add(f)
    db_session.commit()
    m = db_session.execute(select(Metric).where(
        Metric.workflow_id == wf.id)).scalars().first()
    db_session.add(Evidence(workflow_id=wf.id, finding_id=f.id,
                            evidence_type="metric", ref_id=str(m.id),
                            snapshot={}, description="ev"))
    db_session.add(ValidationResult(
        workflow_id=wf.id, check_id="struct_schema", check_name="Schema",
        category="structural", severity="INFO", status="PASS",
        message="ok", details={}))
    db_session.commit()


@pytest.mark.asyncio
async def test_cp6_revision_cycle_reapproves(db_session, monkeypatch):
    storage = get_storage()
    wf = _wf(db_session)
    await _seed_qa_ready(db_session, wf, storage)
    for stage, agent in (("reporting", "reporting"), ("qa", "qa")):
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    rep = _report(db_session, wf)
    cp = _cp(db_session, wf, "final_approval", report=rep)
    monkeypatch.setattr(reporting_mod, "get_llm_client",
                        lambda: FakeLLMClient(script=[{"json": dict(PROSE)}]
                                              * 10))
    with TestClient(app) as client:
        r = _post(client, str(wf.id),
                  {"checkpoint_id": str(cp.id),
                   "decision": "request_revision",
                   "rationale": "tighten the summary wording"})
        assert r.status_code == 202

        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            await engine.run_workflow(str(wf.id))
            st = client.get(f"/workflows/{wf.id}/status").json()
            if st["status"] == "WAITING_FOR_HUMAN":
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError(f"timed out, last={st}")
        cps = [c for c in st["pending_checkpoints"]
               if c["type"] == "final_approval"]
        assert len(cps) == 1
        body = client.get(f"/workflows/{wf.id}/report").json()
        assert body["report"]["version"] == 2
        assert body["report"]["status"] == "qa_passed"
        assert [(h["version"], h["status"]) for h in body["history"]] == [
            (1, "superseded"), (2, "qa_passed")]

        r = _post(client, str(wf.id),
                  {"report_id": body["report"]["id"],
                   "decision": "approve",
                   "rationale": "v2 addresses the wording — approve"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "COMPLETED"
    db_session.refresh(wf)
    assert wf.status == "COMPLETED"

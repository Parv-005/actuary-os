"""QA agent tests (§6.9/§13.7): all-pass path, mismatch regen cycle,
second-failure CP-7, staleness refresh, structural-failure CP-7, no report.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.qa import run_qa
from app.agents.reporting import run_reporting
from app.llm.client import FakeLLMClient
from app.models import (
    AgentRun,
    DatasetVersion,
    Evidence,
    Finding,
    HumanCheckpoint,
    HumanDecision,
    Metric,
    ReferenceValue,
    Report,
    User,
    ValidationResult,
    Workflow,
)
from app.storage.supabase import MemoryStorage

PROSE = {
    "executive_summary": "Portfolio loss ratio closed at 67.3%, up 4.2pp "
                         "on the prior 63.1% and 4.5pp above expected. "
                         "Construction South severity 292387 drove a 60.8% "
                         "contribution.",
    "open_questions": ["Is Construction South severity a sustained trend "
                       "or large-loss noise?"],
}
BAD_PROSE = {
    "executive_summary": "Portfolio loss ratio closed at 99.9%, up 4.2pp. "
                         "Nothing else to add.",
    "open_questions": ["Why?"],
}

CLAIMS_CSV = (
    "claim_id,policy_id,product,segment,region,claim_type,event_date,"
    "report_date,status,incurred_amount\n"
    "C1,P1,Commercial,Construction,South,Fire,2026-09-05,2026-09-06,Open,1000\n"
    "C2,P2,Commercial,SME,North,Theft,2026-09-06,2026-09-07,Closed,500\n"
)
PREMIUM_CSV = (
    "policy_id,product,segment,region,period,written_premium,earned_premium\n"
    "P1,Commercial,Construction,South,2026-09,1000,1000\n"
    "P2,Commercial,SME,North,2026-09,1000,1000\n"
)
EXPOSURE_CSV = (
    "policy_id,product,segment,region,period,active_policies,"
    "earned_exposure_units\n"
    "P1,Commercial,Construction,South,2026-09,1,1.0\n"
    "P2,Commercial,SME,North,2026-09,1,1.0\n"
)
UPSTREAM = (("intake", "intake"), ("data_prep", "data_prep"),
            ("validation", "validation"), ("analysis", "analysis"),
            ("insight", "insight"), ("knowledge", "knowledge"))
# note: reporting is NOT pre-seeded — every test below really executes it,
# so stages_succeeded is proven by a genuine run, not a fixture row


def _wf(db_session, status="QA") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status=status,
                  portfolio="General Insurance", created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _succeeded(db_session, wf, pairs=UPSTREAM):
    for stage, agent in pairs:
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    db_session.commit()


def _metric(db_session, wf, key, dims, value, prev=None, expected=None,
            delta=None, unit="ratio"):
    m = Metric(workflow_id=wf.id, metric_key=key, dimensions=dims,
               period="2026-09", value=value, prev_value=prev,
               expected_value=expected, delta_pp=delta, unit=unit,
               formula=f"{key} = f(inputs)")
    db_session.add(m)
    db_session.commit()
    return m


def _seed_metrics(db_session, wf):
    _metric(db_session, wf, "loss_ratio", {}, 0.673, 0.631, 0.628, 4.2)
    _metric(db_session, wf, "claim_frequency", {}, 0.047, 0.046, None, 2.4,
            unit="per_unit")
    _metric(db_session, wf, "claim_severity", {}, 292387, 258519, None,
            13.1, unit="inr")
    _metric(db_session, wf, "ave_variance", {}, 4.5, None, 0.628, None,
            unit="pp")
    _metric(db_session, wf, "loss_ratio", {"product": "Commercial"},
            0.71, 0.66, None, 5.0)
    _metric(db_session, wf, "deterioration_contribution",
            {"product": "Commercial", "segment": "Construction",
             "region": "South"}, 60.8, None, None, None, unit="pct")


def _seed_finding(db_session, wf, with_evidence=True):
    f = Finding(workflow_id=wf.id, agent="insight", title="Construction "
                "South drives deterioration",
                narrative="Evidence: LR up.\nConclusion: driver.",
                severity="high", confidence=0.9,
                possible_drivers=["large claims"], status="draft",
                decision_question="Review or monitor?")
    db_session.add(f)
    db_session.commit()
    if with_evidence:
        m = db_session.execute(select(Metric).where(
            Metric.workflow_id == wf.id,
            Metric.metric_key == "loss_ratio")).scalars().first()
        db_session.add(Evidence(
            workflow_id=wf.id, finding_id=f.id, evidence_type="metric",
            ref_id=str(m.id), snapshot={"metric_key": "loss_ratio"},
            description="loss ratio evidence"))
        db_session.commit()
    return f


def _seed_validation_decision(db_session, wf, actor):
    db_session.add(ValidationResult(
        workflow_id=wf.id, check_id="recon_premium", check_name="Premium recon",
        category="reconciliation", severity="BLOCKER",
        status="ACCEPTED_EXCEPTION", message="Premium -2.1% vs SoR",
        details={},
        resolution={"rationale": "Known endorsement processing lag."}))
    db_session.add(ValidationResult(
        workflow_id=wf.id, check_id="behav_outlier_claims",
        check_name="Outliers", category="behavioral", severity="WARNING",
        status="WARNING", message="1 unusual claim", details={}))
    cp = HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type="assumption_variance",
        severity="red", blocking=True, title="Assumption variance",
        context={}, options=[], status="resolved", resolved_by=actor.id)
    db_session.add(cp)
    db_session.commit()
    db_session.add(HumanDecision(
        workflow_id=wf.id, checkpoint_id=cp.id,
        decision="no_change_required", rationale="Monitor one more period.",
        payload={}, decided_by=actor.id))
    db_session.commit()


async def _seed_datasets(db_session, wf, storage):
    for kind, csv in (("claims", CLAIMS_CSV), ("premium", PREMIUM_CSV),
                      ("exposure", EXPOSURE_CSV)):
        path = f"workflows/{wf.id}/processed/{kind}.csv"
        await storage.upload_bytes(path, csv.encode())
        db_session.add(DatasetVersion(
            workflow_id=wf.id, kind=kind, source_file_ids=[],
            storage_path=path, row_count=2, column_map={},
            transform_log={}, checksum="x"))
    db_session.commit()


def _ctx(db_session, wf, storage):
    return WorkflowContext(session=db_session, workflow_id=wf.id,
                           workflow=wf, storage=storage)


async def _report_v1(db_session, wf, storage, prose=None):
    return await run_agent(
        _ctx(db_session, wf, storage), "reporting", "reporting",
        lambda c: run_reporting(
            c, llm_client=FakeLLMClient(
                script=[{"json": dict(prose or PROSE)}])))


def _tamper_summary(db_session, wf, text):
    rep = db_session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version.desc())).scalars().first()
    rep.sections = {**rep.sections, "executive_summary": text}
    db_session.commit()
    return rep


@pytest.mark.asyncio
async def test_all_pass_path(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    _succeeded(db_session, wf)
    await _seed_datasets(db_session, wf, storage)
    _seed_metrics(db_session, wf)
    _seed_finding(db_session, wf)
    _seed_validation_decision(db_session, wf, actor)
    r1 = await _report_v1(db_session, wf, storage)
    assert r1.status == "PASS"
    result = await run_agent(
        _ctx(db_session, wf, storage), "qa", "qa",
        lambda c: run_qa(c, llm_client=FakeLLMClient(script=[])))
    assert result.status == "PASS"
    assert result.outputs["qa_passed"] is True
    assert result.outputs["regenerated"] is False
    rep = db_session.query(Report).one()
    assert rep.status == "qa_passed"
    assert rep.qa_result["passed"] is True
    assert len(rep.qa_result["checks"]) == 8
    assert all(c["status"] == "PASS" for c in rep.qa_result["checks"])
    cp6 = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "final_approval")).scalar_one()
    assert cp6.severity == "red" and cp6.blocking is True
    assert cp6.status == "pending"
    assert all(v for v in cp6.context["checklist"].values())
    assert {o["decision"] for o in cp6.options} == {
        "approve", "request_revision", "reject"}
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"
    assert result.outputs["checkpoint_id"] == str(cp6.id)


@pytest.mark.asyncio
async def test_mismatch_regen_cycle(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    _succeeded(db_session, wf)
    await _seed_datasets(db_session, wf, storage)
    _seed_metrics(db_session, wf)
    _seed_finding(db_session, wf)
    _seed_validation_decision(db_session, wf, actor)
    await _report_v1(db_session, wf, storage)
    _tamper_summary(db_session, wf, "Portfolio loss ratio closed at 99.9%.")
    result = await run_agent(
        _ctx(db_session, wf, storage), "qa", "qa",
        lambda c: run_qa(c, llm_client=FakeLLMClient(
            script=[{"json": dict(PROSE)}])))
    assert result.status == "PASS"
    assert result.outputs["qa_passed"] is True
    assert result.outputs["regenerated"] is True
    rows = db_session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version)).scalars().all()
    assert [r.version for r in rows] == [1, 2]
    assert rows[0].status == "draft"  # v1 untouched by regen
    assert rows[1].status == "qa_passed"
    assert rows[1].qa_result["regenerated"] is True
    reporting_runs = db_session.query(AgentRun).filter(
        AgentRun.stage == "reporting").all()
    assert len(reporting_runs) == 2  # v1 attempt + regen attempt


@pytest.mark.asyncio
async def test_second_failure_cp7(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    _succeeded(db_session, wf)
    await _seed_datasets(db_session, wf, storage)
    _seed_metrics(db_session, wf)
    _seed_finding(db_session, wf)
    _seed_validation_decision(db_session, wf, actor)
    await _report_v1(db_session, wf, storage)
    _tamper_summary(db_session, wf, "Portfolio loss ratio closed at 99.9%.")
    result = await run_agent(
        _ctx(db_session, wf, storage), "qa", "qa",
        lambda c: run_qa(c, llm_client=FakeLLMClient(
            script=[{"json": dict(BAD_PROSE)}])))
    assert result.status == "PASS"  # QA work complete; report blocked
    assert result.outputs["qa_passed"] is False
    assert result.outputs["failed_checks"] == ["numbers_consistent"]
    rows = db_session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version)).scalars().all()
    assert [r.version for r in rows] == [1, 2]  # exactly one regen
    assert all(r.status == "draft" for r in rows)
    assert rows[1].qa_result["passed"] is False
    cp7 = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "qa_failure")).scalar_one()
    assert cp7.severity == "red" and cp7.blocking is True
    assert cp7.context["mismatches"][0]["prose"] == "99.9%"
    assert 'report "99.9%"' in cp7.context["mismatches"][0]["message"]
    assert {o["decision"] for o in cp7.options} == {
        "request_revision", "escalate"}
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"


@pytest.mark.asyncio
async def test_staleness_triggers_refresh(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    await _seed_datasets(db_session, wf, storage)
    db_session.add(ReferenceValue(
        period="2026-09", metric_key="expected_loss_ratio", dimensions={},
        value=0.70, source="methodology"))
    db_session.commit()
    ctx = _ctx(db_session, wf, storage)
    # real analysis computes LR 0.75 / freq 1.0 / severity 750
    from app.agents.analysis import run_analysis

    analyzed = await run_agent(ctx, "analysis", "analysis", run_analysis)
    assert analyzed.status in ("PASS", "WARNING")
    # backdate metrics: older than the dataset upload -> stale
    db_session.execute(update(Metric).where(
        Metric.workflow_id == wf.id).values(
            computed_at=datetime(2000, 1, 1, tzinfo=UTC)))
    db_session.commit()
    metrics = db_session.execute(
        select(Metric).where(Metric.workflow_id == wf.id)).scalars().all()
    port = next(m for m in metrics if m.metric_key == "loss_ratio"
                and not m.dimensions)
    assert float(port.value) == 0.75
    _seed_finding(db_session, wf)
    db_session.add(ValidationResult(
        workflow_id=wf.id, check_id="struct_schema", check_name="Schema",
        category="structural", severity="INFO", status="PASS",
        message="ok", details={}))
    for stage, agent in (("intake", "intake"), ("data_prep", "data_prep"),
                         ("validation", "validation"), ("insight", "insight"),
                         ("knowledge", "knowledge"),
                         ("reporting", "reporting")):
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    db_session.commit()
    prose = {"executive_summary": "Loss ratio 75.0%. Frequency 1.0 per unit. "
                                  "Severity 750.",
             "open_questions": ["Anything else?"]}
    await run_agent(ctx, "reporting", "reporting",
                    lambda c: run_reporting(
                        c, llm_client=FakeLLMClient(
                            script=[{"json": prose}])))
    result = await run_agent(ctx, "qa", "qa",
                             lambda c: run_qa(
                                 c, llm_client=FakeLLMClient(script=[])))
    assert result.status == "PASS"
    assert result.outputs["qa_passed"] is True
    assert db_session.query(AgentRun).filter(
        AgentRun.stage == "analysis").count() == 2  # seed run + refresh
    rep = db_session.query(Report).one()
    assert rep.qa_result["refreshed_metrics"] is True
    fresh = db_session.execute(
        select(Metric.computed_at).where(
            Metric.workflow_id == wf.id)).scalars().all()
    assert all(ts.year > 2020 for ts in fresh)


@pytest.mark.asyncio
async def test_unresolved_red_cp7_without_regen(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    _succeeded(db_session, wf)
    await _seed_datasets(db_session, wf, storage)
    _seed_metrics(db_session, wf)
    _seed_finding(db_session, wf)
    _seed_validation_decision(db_session, wf, actor)
    await _report_v1(db_session, wf, storage)
    # a red checkpoint left pending: structural failure, regen pointless
    db_session.add(HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type="validation_blocker",
        severity="red", blocking=True, title="Unresolved blocker",
        context={}, options=[]))
    db_session.commit()
    result = await run_agent(
        _ctx(db_session, wf, storage), "qa", "qa",
        lambda c: run_qa(c, llm_client=FakeLLMClient(script=[])))
    assert result.status == "PASS"
    assert result.outputs["qa_passed"] is False
    assert result.outputs["failed_checks"] == ["no_unresolved_red"]
    assert db_session.query(Report).count() == 1  # no regen attempted
    cp7 = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "qa_failure")).scalar_one()
    assert cp7.status == "pending"


@pytest.mark.asyncio
async def test_no_evidence_cp7(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    _succeeded(db_session, wf)
    await _seed_datasets(db_session, wf, storage)
    _seed_metrics(db_session, wf)
    _seed_finding(db_session, wf, with_evidence=False)
    _seed_validation_decision(db_session, wf, actor)
    await _report_v1(db_session, wf, storage)
    result = await run_agent(
        _ctx(db_session, wf, storage), "qa", "qa",
        lambda c: run_qa(c, llm_client=FakeLLMClient(script=[])))
    assert result.outputs["qa_passed"] is False
    assert "evidence_complete" in result.outputs["failed_checks"]


@pytest.mark.asyncio
async def test_no_report_failed(db_session):
    wf = _wf(db_session)
    result = await run_agent(
        _ctx(db_session, wf, MemoryStorage()), "qa", "qa",
        lambda c: run_qa(c, llm_client=FakeLLMClient(script=[])))
    assert result.status == "FAILED"
    assert "no draft report" in result.error

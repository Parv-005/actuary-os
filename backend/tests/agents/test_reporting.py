"""Reporting agent tests (§6.8): sections complete, revision versions,
missing-metric handling, LLM failure, chart specs."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.reporting import run_reporting
from app.llm.client import FakeLLMClient
from app.models import (
    DatasetVersion,
    Evidence,
    Finding,
    HumanCheckpoint,
    HumanDecision,
    KnowledgeDocument,
    Metric,
    Report,
    User,
    ValidationResult,
    Workflow,
)
from app.services import reports as rep
from app.storage.supabase import MemoryStorage

PROSE = {
    "executive_summary": "Portfolio loss ratio closed at 67.3%, up 4.2pp "
                         "on the prior 63.1% and 4.5pp above expected. "
                         "Construction South severity 292387 drove a 60.8% "
                         "contribution.",
    "open_questions": ["Is Construction South severity a sustained trend "
                       "or large-loss noise?"],
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


def _wf(db_session, status="REPORTING") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status=status,
                  portfolio="General Insurance", created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _metric(db_session, wf, key, dims, value, prev=None, expected=None,
            delta=None, unit="ratio"):
    m = Metric(workflow_id=wf.id, metric_key=key, dimensions=dims,
               period="2026-09", value=value, prev_value=prev,
               expected_value=expected, delta_pp=delta, unit=unit,
               formula=f"{key} = f(inputs)")
    db_session.add(m)
    db_session.commit()
    return m


def _seed_metrics(db_session, wf, with_severity=True):
    _metric(db_session, wf, "loss_ratio", {}, 0.673, 0.631, 0.628, 4.2)
    _metric(db_session, wf, "claim_frequency", {}, 0.047, 0.046, None, 2.4,
            unit="per_unit")
    if with_severity:
        _metric(db_session, wf, "claim_severity", {}, 292387, 258519, None,
                13.1, unit="inr")
        _metric(db_session, wf, "claim_severity",
                {"product": "Commercial", "segment": "Construction"},
                280000, 250000, None, 12.0, unit="inr")
        _metric(db_session, wf, "claim_frequency",
                {"product": "Commercial", "segment": "Construction"},
                0.05, 0.049, None, 2.0, unit="per_unit")
    _metric(db_session, wf, "ave_variance", {}, 4.5, None, 0.628, None,
            unit="pp")
    _metric(db_session, wf, "loss_ratio", {"product": "Commercial"},
            0.71, 0.66, None, 5.0)
    _metric(db_session, wf, "loss_ratio", {"product": "Motor"},
            0.60, 0.61, None, -1.0)
    _metric(db_session, wf, "deterioration_contribution",
            {"product": "Commercial", "segment": "Construction",
             "region": "South"}, 60.8, None, None, None, unit="pct")


def _seed_finding(db_session, wf, with_evidence=True):
    f = Finding(workflow_id=wf.id, agent="insight", title="Construction "
                "South drives deterioration",
                narrative="Evidence: LR up.\nConclusion: driver.",
                severity="high", confidence=0.9,
                possible_drivers=["large claims"],
                alternatives=["volatility"], status="draft",
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


def _seed_validation_and_decision(db_session, wf, actor):
    db_session.add(ValidationResult(
        workflow_id=wf.id, check_id="recon_premium", check_name="Premium recon",
        category="reconciliation", severity="BLOCKER",
        status="ACCEPTED_EXCEPTION", message="Premium -2.1% vs SoR",
        details={"diff_pct": -2.1},
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


def _seed_docs(db_session):
    db_session.add(KnowledgeDocument(
        title="Monthly Review 2026-08", doc_type="prior_report",
        version="1.0", effective_date=date(2026, 8, 31),
        content_text="Monitor construction severity.",
        tags=["prior_report"]))
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


def _seed_all(db_session, wf, storage, actor, with_severity=True):
    _seed_metrics(db_session, wf, with_severity)
    _seed_finding(db_session, wf)
    _seed_validation_and_decision(db_session, wf, actor)
    _seed_docs(db_session)
    return _seed_datasets(db_session, wf, storage)


def _ctx(db_session, wf, storage):
    return WorkflowContext(session=db_session, workflow_id=wf.id,
                           workflow=wf, storage=storage)


@pytest.mark.asyncio
async def test_sections_complete(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    await _seed_all(db_session, wf, storage, actor)
    fake = FakeLLMClient(script=[{"json": dict(PROSE)}])
    result = await run_agent(
        _ctx(db_session, wf, storage), "reporting", "reporting",
        lambda c: run_reporting(c, llm_client=fake))
    assert result.status == "PASS"
    assert result.outputs["version"] == 1 and not result.outputs["regen"]
    rep_row = db_session.query(Report).one()
    assert rep_row.status == "draft"
    s = rep_row.sections
    assert set(s) == {"executive_summary", "key_metrics", "findings",
                      "exceptions", "decisions", "open_questions", "charts",
                      "citations"}
    assert "67.3%" in s["executive_summary"]
    assert any(r["metric_key"] == "loss_ratio" and r["dimensions"] == {}
               for r in s["key_metrics"])
    assert s["findings"][0]["severity"] == "high"
    assert s["findings"][0]["evidence_count"] == 1
    assert any(e["check_id"] == "recon_premium"
               and e["status"] == "ACCEPTED_EXCEPTION"
               and "endorsement" in str(e["resolution"])
               for e in s["exceptions"])
    assert any(d["decision"] == "no_change_required" for d in s["decisions"])
    assert len(s["charts"]) == 4
    assert {c["chart"] for c in s["charts"]} <= {"line", "bar"}
    assert rep_row.body_markdown.startswith("# Monthly Portfolio Review")
    assert "67.3%" in rep_row.body_markdown


@pytest.mark.asyncio
async def test_revision_creates_v2_preserves_v1(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    await _seed_all(db_session, wf, storage, actor)
    ctx = _ctx(db_session, wf, storage)
    await run_agent(ctx, "reporting", "reporting",
                    lambda c: run_reporting(
                        c, llm_client=FakeLLMClient(
                            script=[{"json": dict(PROSE)}])))
    v1 = db_session.query(Report).one()
    v1_sections = dict(v1.sections)
    prose2 = dict(PROSE, executive_summary="Revised summary at 67.3%.")
    await run_agent(ctx, "reporting", "reporting",
                    lambda c: run_reporting(
                        c, llm_client=FakeLLMClient(
                            script=[{"json": prose2}])))
    rows = db_session.execute(
        select(Report).where(Report.workflow_id == wf.id)
        .order_by(Report.version)).scalars().all()
    assert [r.version for r in rows] == [1, 2]
    assert rows[0].sections == v1_sections  # v1 untouched
    assert rows[0].status == "draft"
    assert "Revised summary" in rows[1].sections["executive_summary"]


@pytest.mark.asyncio
async def test_missing_metric_omitted_plus_open_question(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    await _seed_all(db_session, wf, storage, actor, with_severity=False)
    fake = FakeLLMClient(script=[{"json": dict(PROSE)}])
    result = await run_agent(
        _ctx(db_session, wf, storage), "reporting", "reporting",
        lambda c: run_reporting(c, llm_client=fake))
    assert result.status == "PASS"
    s = db_session.query(Report).one().sections
    assert not any(r["metric_key"] == "claim_severity"
                   and r["dimensions"] == {} for r in s["key_metrics"])
    assert any("claim_severity" in q for q in s["open_questions"])


@pytest.mark.asyncio
async def test_llm_invalid_x3_failed(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    actor = db_session.query(User).first()
    await _seed_all(db_session, wf, storage, actor)
    fake = FakeLLMClient(script=[{"json": {"nope": 1}}] * 3)
    result = await run_agent(
        _ctx(db_session, wf, storage), "reporting", "reporting",
        lambda c: run_reporting(c, llm_client=fake))
    assert result.status == "FAILED"
    assert "x3" in result.error
    assert db_session.query(Report).count() == 0


@pytest.mark.asyncio
async def test_no_metrics_failed(db_session):
    wf = _wf(db_session)
    result = await run_agent(
        _ctx(db_session, wf, MemoryStorage()), "reporting", "reporting",
        lambda c: run_reporting(
            c, llm_client=FakeLLMClient(script=[{"json": dict(PROSE)}])))
    assert result.status == "FAILED"
    assert "no metrics" in result.error


def test_generate_chart_spec_rejects_unknown():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        rep.generate_chart_spec("pie", "t", [], "x", [])
    spec = rep.generate_chart_spec("line", "t", [{"a": 1}], "a",
                                   [{"key": "a", "name": "A"}])
    assert spec["chart"] == "line" and spec["data"] == [{"a": 1}]

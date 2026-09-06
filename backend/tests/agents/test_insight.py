"""Insight agent tests (§6.6): FakeLLM tool-loop happy path, schema-retry
exhaustion, bad-evidence rejection, multi-driver, low-confidence CP-5,
deterministic CP-4, replace-with-link on re-run."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.insight import run_insight
from app.llm.client import FakeLLMClient
from app.models import (
    Evidence,
    Finding,
    HumanCheckpoint,
    KnowledgeDocument,
    Metric,
    ReferenceValue,
    User,
    Workflow,
)
from app.storage.supabase import MemoryStorage


def _wf(db_session, status="INVESTIGATING") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status=status,
                  portfolio="General Insurance", created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _metric(db_session, wf, key, dims, value, prev=None, delta=None,
            unit="ratio"):
    m = Metric(workflow_id=wf.id, metric_key=key, dimensions=dims,
               period="2026-09", value=value, prev_value=prev,
               expected_value=None, delta_pp=delta, unit=unit,
               formula=f"{key} = f(inputs)")
    db_session.add(m)
    db_session.commit()
    return m


def _seed_metrics(db_session, wf):
    port = _metric(db_session, wf, "loss_ratio", {}, 0.673, 0.631, 4.2)
    cs = {"product": "Commercial", "segment": "Construction",
          "region": "South"}
    lr = _metric(db_session, wf, "loss_ratio", cs, 0.781, 0.629, 15.2)
    sev = _metric(db_session, wf, "claim_severity", cs, 292387, 258519,
                  13.1, unit="inr")
    freq = _metric(db_session, wf, "claim_frequency", cs, 0.0471, 0.046,
                   2.4, unit="per_unit")
    contrib = _metric(db_session, wf, "deterioration_contribution", cs,
                      60.8, None, None, unit="pct")
    return {"port": port, "lr": lr, "sev": sev, "freq": freq,
            "contrib": contrib}


def _ctx(db_session, wf):
    return WorkflowContext(session=db_session, workflow_id=wf.id,
                           workflow=wf, storage=MemoryStorage())


def _finding_json(ids, confidence=0.9, drivers=("large claims",)):
    return {
        "finding": "Commercial Construction (South) drives deterioration",
        "severity": "high", "confidence": confidence,
        "evidence_ids": ids,
        "narrative": {
            "evidence": "LR 62.9% -> 78.1% (+15.2pp)",
            "hypothesis": "severity-driven move coincides with large losses",
            "conclusion": "largest contributor to portfolio movement",
        },
        "possible_drivers": list(drivers),
        "alternatives": ["large-loss volatility", "reporting timing"],
        "correlation_caveat": "coincides with severe weather; "
                              "causation not established",
        "human_review_required": False,
        "decision_question": "Assumption review, pricing review, or monitoring?",
    }


@pytest.mark.asyncio
async def test_happy_path_cites_real_ids(db_session):
    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    ev = [str(ids["port"].id), str(ids["lr"].id), str(ids["sev"].id),
          str(ids["freq"].id), str(ids["contrib"].id)]
    fake = FakeLLMClient(script=[
        {"tool_calls": [{"name": "portfolio_summary", "args": {}}]},
        {"tool_calls": [{"name": "top_contributors", "args": {}}]},
        {"tool_calls": [{"name": "severity_vs_frequency",
                         "args": {"product": "Commercial",
                                  "segment": "Construction",
                                  "region": "South"}}]},
        {"json": _finding_json(ev)},
    ])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "PASS"
    assert result.outputs["tool_calls"] == 3
    assert result.outputs["cp4_checkpoint_id"] is None  # no trend refs
    assert result.outputs["cp5_checkpoint_id"] is None  # conf 0.9

    findings = db_session.query(Finding).all()
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "high" and f.status == "draft"
    assert f.human_review_required is False
    assert "Evidence:" in f.narrative and "Hypothesis:" in f.narrative
    assert f.data_quality.startswith("validation:")
    assert f.model and f.agent_version == "v1"
    rows = db_session.execute(
        select(Evidence).where(Evidence.finding_id == f.id)).scalars().all()
    assert len(rows) == 5
    snap = next(r.snapshot for r in rows
                if r.snapshot["metric_key"] == "loss_ratio"
                and r.snapshot["dimensions"].get("region") == "South")
    assert snap["value"] == pytest.approx(0.781)
    assert snap["formula"] == "loss_ratio = f(inputs)"
    assert [t["name"] for t in fake.executed_tools] == [
        "portfolio_summary", "top_contributors", "severity_vs_frequency"]


@pytest.mark.asyncio
async def test_invalid_json_retries_then_failed(db_session):
    wf = _wf(db_session)
    _seed_metrics(db_session, wf)
    bad = {"finding": "x"}  # schema-invalid (missing required fields)
    fake = FakeLLMClient(script=[{"json": bad}] * 3 + [{"json": bad}])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "FAILED"
    assert "attempts" in result.error and "fallback" in result.error
    assert db_session.query(Finding).count() == 0


@pytest.mark.asyncio
async def test_bad_evidence_rejected_then_failed(db_session):
    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    weird = _finding_json([str(ids["port"].id), "not-a-metric-id"])
    fake = FakeLLMClient(script=[{"json": weird}] * 3 + [{"json": weird}])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "FAILED"
    assert "evidence" in result.error
    assert db_session.query(Finding).count() == 0


@pytest.mark.asyncio
async def test_multi_driver_not_forced_single_cause(db_session):
    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    fake = FakeLLMClient(script=[{"json": _finding_json(
        [str(ids["port"].id), str(ids["contrib"].id)],
        drivers=("large claims in Construction South",
                 "regional concentration in South"))}])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "PASS"
    f = db_session.query(Finding).one()
    assert len(f.possible_drivers) == 2


@pytest.mark.asyncio
async def test_low_confidence_raises_cp5(db_session):
    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    fake = FakeLLMClient(script=[{"json": _finding_json(
        [str(ids["port"].id)], confidence=0.5)}])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "PASS"
    f = db_session.query(Finding).one()
    assert f.human_review_required is True
    cp5 = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "finding_review")).scalar_one()
    assert cp5.severity == "yellow" and cp5.blocking is False
    assert cp5.context["finding_id"] == str(f.id)
    assert result.outputs["cp5_checkpoint_id"] == str(cp5.id)


@pytest.mark.asyncio
async def test_no_metrics_is_failed(db_session):
    wf = _wf(db_session)
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=FakeLLMClient(script=[])))
    assert result.status == "FAILED"
    assert "no metrics" in result.error


@pytest.mark.asyncio
async def test_cp4_deterministic_variance(db_session):
    """Observed +13.1% vs configured +5.0% -> variance +8.1pp >= 5pp gate.
    Computed deterministically — the LLM output cannot suppress it."""
    from datetime import date

    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    db_session.add(ReferenceValue(
        period="2026-09", metric_key="expected_severity_trend",
        dimensions={"product": "Commercial", "segment": "Construction"},
        value=0.050, source="methodology"))
    db_session.add(KnowledgeDocument(
        title="Reserve Methodology", doc_type="methodology", version="v3.1",
        effective_date=date(2026, 4, 1),
        content_text="Expected severity trend for Commercial Construction: "
                     "+5.0% YoY.",
        tags=["methodology", "reserving", "construction", "assumptions"]))
    db_session.commit()
    # the canned LLM says nothing about assumptions — CP-4 still fires
    plain = _finding_json([str(ids["port"].id)])
    del plain["decision_question"]
    fake = FakeLLMClient(script=[{"json": plain}])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "PASS"
    cp4 = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "assumption_variance")).scalar_one()
    assert cp4.severity == "red" and cp4.blocking is True
    var = cp4.context["variances"][0]
    assert var["observed_delta_pct"] == pytest.approx(13.1)
    assert var["expected_trend_pct"] == pytest.approx(5.0)
    assert var["variance_pp"] == pytest.approx(8.1)
    assert cp4.context["methodology"]["version"] == "v3.1"
    assert "does NOT recommend an assumption change" in cp4.context["disclaimer"]
    assert {o["decision"] for o in cp4.options} == {
        "no_change_required", "investigate_further", "review_assumption",
        "escalate"}
    assert result.outputs["cp4_checkpoint_id"] == str(cp4.id)


@pytest.mark.asyncio
async def test_no_cp4_below_gate(db_session):
    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    db_session.add(ReferenceValue(
        period="2026-09", metric_key="expected_severity_trend",
        dimensions={"product": "Commercial", "segment": "Construction"},
        value=0.120, source="methodology"))  # +12% config: variance +1.1pp
    db_session.commit()
    fake = FakeLLMClient(
        script=[{"json": _finding_json([str(ids["port"].id)])}])
    result = await run_agent(
        _ctx(db_session, wf), "insight", "insight",
        lambda c: run_insight(c, llm_client=fake))
    assert result.status == "PASS"
    assert result.outputs["cp4_checkpoint_id"] is None
    assert db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "assumption_variance")
    ).scalars().all() == []


@pytest.mark.asyncio
async def test_replace_with_link_on_rerun(db_session):
    wf = _wf(db_session)
    ids = _seed_metrics(db_session, wf)
    ev = [str(ids["port"].id)]
    for _ in range(2):
        fake = FakeLLMClient(script=[{"json": _finding_json(ev)}])
        result = await run_agent(
            _ctx(db_session, wf), "insight", "insight",
            lambda c, _f=fake: run_insight(c, llm_client=_f))
        assert result.status == "PASS"
    findings = db_session.query(Finding).all()
    assert len(findings) == 1  # replaced, not duplicated
    assert len(findings[0].links) == 1

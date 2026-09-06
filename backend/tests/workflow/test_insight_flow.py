"""Investigation flow tests (§6.6/§6.7/§12): engine-driven insight +
knowledge with patched FakeLLM factories — no-CP4 rest, CP-4 park + decide,
insight-failure FAILED without analysis re-run, bounded investigate_further.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.agents.insight as insight_mod
import app.agents.knowledge as knowledge_mod
from app.llm.client import FakeLLMClient
from app.main import app
from app.models import (
    AgentRun,
    Finding,
    HumanCheckpoint,
    KnowledgeDocument,
    Metric,
    ReferenceValue,
    Workflow,
)
from app.orchestrator import engine

EARLY_STAGES = (("intake", "intake"), ("data_prep", "data_prep"),
                ("validation", "validation"), ("analysis", "analysis"))


def _wf(db_session, status="ANALYZED") -> Workflow:
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", portfolio="General Insurance",
                  status=status)
    db_session.add(wf)
    db_session.commit()
    return wf


def _seed_succeeded(db_session, wf, pairs=EARLY_STAGES):
    for stage, agent in pairs:
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    db_session.commit()


def _metric(db_session, wf, key, dims, value, prev=None, delta=None,
            unit="ratio"):
    m = Metric(workflow_id=wf.id, metric_key=key, dimensions=dims,
               period="2026-09", value=value, prev_value=prev,
               delta_pp=delta, unit=unit, formula=f"{key} = f(inputs)")
    db_session.add(m)
    db_session.commit()
    return m


def _seed_metrics(db_session, wf, material=True):
    delta = 4.2 if material else 1.0
    port = _metric(db_session, wf, "loss_ratio", {}, 0.673, 0.631, delta)
    cs = {"product": "Commercial", "segment": "Construction",
          "region": "South"}
    lr = _metric(db_session, wf, "loss_ratio", cs, 0.781, 0.629,
                 15.2 if material else 1.2)
    sev = _metric(db_session, wf, "claim_severity", cs, 292387, 258519,
                  13.1 if material else 0.8, unit="inr")
    contrib = _metric(db_session, wf, "deterioration_contribution", cs,
                      60.8 if material else 5.0, None, None, unit="pct")
    return {"port": port, "lr": lr, "sev": sev, "contrib": contrib}


def _finding_json(ids, confidence=0.9):
    return {
        "finding": "Commercial Construction (South) drives deterioration",
        "severity": "high", "confidence": confidence,
        "evidence_ids": ids,
        "narrative": {
            "evidence": "LR 62.9% -> 78.1% (+15.2pp)",
            "hypothesis": "severity-driven move coincides with large losses",
            "conclusion": "largest contributor to portfolio movement",
        },
        "possible_drivers": ["large claims in Construction South"],
        "alternatives": ["large-loss volatility"],
        "human_review_required": False,
        "decision_question": "Assumption review or monitoring?",
    }


def _patch_llms(monkeypatch, insight_script, knowledge_script=None):
    monkeypatch.setattr(
        insight_mod, "get_llm_client",
        lambda: FakeLLMClient(script=list(insight_script)))
    monkeypatch.setattr(
        knowledge_mod, "get_llm_client",
        lambda: FakeLLMClient(
            script=list(knowledge_script or [{"json": {
                "summary": "docs framed", "citations": [],
                "no_relevant_document": False}}])))


@pytest.mark.asyncio
async def test_engine_investigation_no_cp4_rests(db_session, monkeypatch):
    # freeze before reporting: this flow stops at INSIGHTS_READY (reporting
    # and QA are covered by the report-flow and sample-run tests)
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [s for s in engine.STAGE_ORDER
                         if s.stage != "reporting" and s.stage != "qa"])
    wf = _wf(db_session)
    _seed_succeeded(db_session, wf)
    ids = _seed_metrics(db_session, wf, material=False)
    _patch_llms(monkeypatch, [
        {"tool_calls": [{"name": "portfolio_summary", "args": {}}]},
        {"json": _finding_json([str(ids["port"].id)])},
    ])
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "INSIGHTS_READY"  # no trend refs -> no CP-4, rest
    assert db_session.query(Finding).count() == 1
    states = {r.stage: r.status for r in db_session.query(AgentRun).all()
              if r.stage in ("insight", "knowledge")}
    assert states == {"insight": "succeeded", "knowledge": "succeeded"}


@pytest.mark.asyncio
async def test_engine_cp4_parks_and_decide(db_session, monkeypatch):
    wf = _wf(db_session)
    _seed_succeeded(db_session, wf)
    ids = _seed_metrics(db_session, wf, material=True)
    db_session.add(ReferenceValue(
        period="2026-09", metric_key="expected_severity_trend",
        dimensions={"product": "Commercial", "segment": "Construction"},
        value=0.050, source="methodology"))
    db_session.add(KnowledgeDocument(
        title="Reserve Methodology", doc_type="methodology", version="v3.1",
        effective_date=date(2026, 4, 1),
        content_text="Expected severity trend: +5.0%.",
        tags=["methodology", "assumptions"]))
    db_session.commit()
    script = [
        {"tool_calls": [{"name": "top_contributors", "args": {}}]},
        {"json": _finding_json([str(ids["port"].id),
                                str(ids["contrib"].id)])},
    ]
    _patch_llms(monkeypatch, script)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"
    cp = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.checkpoint_type == "assumption_variance")).scalar_one()
    assert cp.status == "pending" and cp.blocking is True

    with TestClient(app) as client:
        r = client.post(f"/workflows/{wf.id}/decisions", json={
            "checkpoint_id": str(cp.id), "decision": "no_change_required",
            "rationale": "", "payload": {}})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REPORTING"
    db_session.refresh(wf)
    assert wf.status == "REPORTING"


@pytest.mark.asyncio
async def test_insight_failure_failed_without_analysis_rerun(db_session,
                                                             monkeypatch):
    monkeypatch.setattr(engine, "BACKOFF_S", [0.01, 0.01, 0.01])
    wf = _wf(db_session)
    _seed_succeeded(db_session, wf)
    _seed_metrics(db_session, wf, material=True)
    bad = {"finding": "x"}  # schema-invalid every time, incl. fallback
    monkeypatch.setattr(insight_mod, "get_llm_client",
                        lambda: FakeLLMClient(script=[{"json": bad}] * 4))
    monkeypatch.setattr(knowledge_mod, "get_llm_client",
                        lambda: FakeLLMClient(script=[]))
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "FAILED"
    assert "insight" in (wf.error or {}).get("message", "")
    insight_rows = db_session.query(AgentRun).filter(
        AgentRun.stage == "insight").all()
    assert len(insight_rows) == 3  # 3 attempts, then FAILED (§6.6)
    assert all(r.status == "failed" for r in insight_rows)
    assert db_session.query(AgentRun).filter(
        AgentRun.stage == "analysis").count() == 1  # never re-executed
    assert db_session.query(Finding).count() == 0


@pytest.mark.asyncio
async def test_investigate_further_bounded(db_session, monkeypatch):
    wf = _wf(db_session)
    _seed_succeeded(db_session, wf)
    ids = _seed_metrics(db_session, wf, material=True)
    db_session.add(ReferenceValue(
        period="2026-09", metric_key="expected_severity_trend",
        dimensions={"product": "Commercial", "segment": "Construction"},
        value=0.050, source="methodology"))
    db_session.commit()

    def _script():
        return [
            {"tool_calls": [{"name": "portfolio_summary", "args": {}}]},
            {"json": _finding_json([str(ids["port"].id)])},
        ]

    _patch_llms(monkeypatch, _script())
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"
    first_id = str(db_session.query(Finding).one().id)

    with TestClient(app) as client:
        cp = db_session.execute(
            select(HumanCheckpoint).where(
                HumanCheckpoint.checkpoint_type == "assumption_variance")).scalar_one()
        r = client.post(f"/workflows/{wf.id}/decisions", json={
            "checkpoint_id": str(cp.id), "decision": "investigate_further",
            "rationale": "", "payload": {"focus": "frequency"}})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "INVESTIGATING"
    db_session.refresh(wf)
    assert wf.config["investigation_rounds"] == 1
    assert db_session.query(AgentRun).filter(
        AgentRun.stage.in_(("insight", "knowledge"))).count() == 0

    _patch_llms(monkeypatch, _script())  # fresh scripts for the re-run
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"  # CP-4 raised again
    findings = db_session.query(Finding).all()
    assert len(findings) == 1 and findings[0].links == [first_id]

    with TestClient(app) as client:
        cp2 = db_session.execute(
            select(HumanCheckpoint).where(
                HumanCheckpoint.checkpoint_type == "assumption_variance",
                HumanCheckpoint.status == "pending")).scalar_one()
        r = client.post(f"/workflows/{wf.id}/decisions", json={
            "checkpoint_id": str(cp2.id), "decision": "investigate_further",
            "rationale": "", "payload": {}})
        assert r.status_code == 409  # bounded 1x

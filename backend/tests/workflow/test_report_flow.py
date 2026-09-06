"""Report flow tests (§6.8/§6.9/§10): engine-driven reporting + QA with a
patched reporting factory — full path to CP-6, plus the reports API
(versioning, 404s)."""
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
    DatasetVersion,
    Evidence,
    Finding,
    Metric,
    Report,
    ValidationResult,
    Workflow,
)
from app.orchestrator import engine
from app.storage.supabase import get_storage

EARLY = (("intake", "intake"), ("data_prep", "data_prep"),
         ("validation", "validation"), ("analysis", "analysis"),
         ("insight", "insight"), ("knowledge", "knowledge"))

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

PROSE = {
    "executive_summary": "Portfolio loss ratio closed at 75.0%.",
    "open_questions": ["Anything else?"],
}


async def _wait_for_status(client: TestClient, wid: str, want: str,
                           timeout_s: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict | None = None
    while time.monotonic() < deadline:
        await engine.run_workflow(wid)
        last = client.get(f"/workflows/{wid}/status").json()
        if last["status"] == want:
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out waiting for {want}, last={last}")


def _seed(db_session, storage, status="REPORTING"):
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", portfolio="General Insurance",
                  status=status)
    db_session.add(wf)
    db_session.commit()
    return wf


async def _seed_all(db_session, wf, storage):
    # datasets FIRST: metrics seeded after the dataset upload read fresh,
    # otherwise QA's staleness guard fires and recomputes them
    for kind, csv in (("claims", CLAIMS_CSV), ("premium", PREMIUM_CSV),
                      ("exposure", EXPOSURE_CSV)):
        path = f"workflows/{wf.id}/processed/{kind}.csv"
        await storage.upload_bytes(path, csv.encode())
        db_session.add(DatasetVersion(
            workflow_id=wf.id, kind=kind, source_file_ids=[],
            storage_path=path, row_count=1, column_map={},
            transform_log={}, checksum="x"))
    db_session.commit()
    for stage, agent in EARLY:
        db_session.add(AgentRun(workflow_id=wf.id, agent=agent, stage=stage,
                                attempt=1, status="succeeded"))
    for key, dims, value in (
            ("loss_ratio", {}, 0.75), ("claim_frequency", {}, 1.0),
            ("claim_severity", {}, 1000.0), ("ave_variance", {}, 5.0)):
        db_session.add(Metric(
            workflow_id=wf.id, metric_key=key, dimensions=dims,
            period="2026-09", value=value, formula=f"{key} = f(inputs)"))
    f = Finding(workflow_id=wf.id, agent="insight", title="Drift up",
                narrative="Evidence: up.\nConclusion: watch.", severity="high",
                confidence=0.9, status="draft")
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
    return wf


@pytest.mark.asyncio
async def test_engine_reporting_qa_to_cp6(db_session, monkeypatch):
    # engine-driven stages read through the storage singleton
    storage = get_storage()
    wf = _seed(db_session, storage)
    await _seed_all(db_session, wf, storage)
    monkeypatch.setattr(reporting_mod, "get_llm_client",
                        lambda: FakeLLMClient(script=[{"json": dict(PROSE)}]))
    with TestClient(app) as client:
        st = await _wait_for_status(client, str(wf.id), "WAITING_FOR_HUMAN")
        states = {s["stage"]: s["state"] for s in st["stage_statuses"]}
        assert states["reporting"] == "succeeded"
        assert states["qa"] == "succeeded"
        cp6 = next(c for c in st["pending_checkpoints"]
                   if c["type"] == "final_approval")
        assert cp6["severity"] == "red" and cp6["blocking"] is True

        body = client.get(f"/workflows/{wf.id}/report").json()
        assert body["report"]["version"] == 1
        assert body["report"]["status"] == "qa_passed"
        assert body["report"]["qa_result"]["passed"] is True
        assert "75.0%" in body["report"]["sections"]["executive_summary"]
        assert [(h["version"], h["status"]) for h in body["history"]] == [
            (1, "qa_passed")]
        assert client.get(
            f"/workflows/{wf.id}/report",
            params={"version": 2}).status_code == 404
        assert client.get(
            "/workflows/00000000-0000-0000-0000-000000000000/report"
        ).status_code == 404


@pytest.mark.asyncio
async def test_report_api_no_report_404(db_session):
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status="ANALYZED")
    db_session.add(wf)
    db_session.commit()
    with TestClient(app) as client:
        r = client.get(f"/workflows/{wf.id}/report")
        assert r.status_code == 404
        assert db_session.query(Report).count() == 0

"""Validation agent tests (§6.4): L1-L4 checks, recon CP-2 blocker flow,
stale-file coverage blocker, key-collision blocker, exact-match full pass,
and the CP-2 decisions API (accept / re-run / reject)."""
from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.data_prep import run_data_prep
from app.agents.validation import run_validation
from app.main import app
from app.models import (
    File,
    HumanCheckpoint,
    ReferenceValue,
    User,
    ValidationResult,
    Workflow,
)
from app.orchestrator import engine
from app.storage.supabase import MemoryStorage


def _premium_rows(n: int, earned: int) -> str:
    lines = ["policy_id,product,segment,region,period,written_premium,earned_premium"]
    lines += [f"P{i},Commercial,Construction,South,2026-09,{earned},{earned}"
              for i in range(1, n + 1)]
    return "\n".join(lines) + "\n"


def _exposure_rows(n: int) -> str:
    lines = ["policy_id,product,segment,region,period,active_policies,"
             "earned_exposure_units"]
    lines += [f"P{i},Commercial,Construction,South,2026-09,1,1.0"
              for i in range(1, n + 1)]
    return "\n".join(lines) + "\n"


def _claims_rows(n: int, amount: int, event: str = "2026-09-05",
                 report: str = "2026-09-06", regions=("South",)) -> str:
    lines = ["claim_id,policy_id,product,segment,region,claim_type,event_date,"
             "report_date,status,incurred_amount"]
    lines += [f"C{i},P{i},Commercial,Construction,{regions[(i - 1) % len(regions)]},"
              f"Fire,{event},{report},Open,{amount}"
              for i in range(1, n + 1)]
    return "\n".join(lines) + "\n"


PREMIUM_SMALL = _premium_rows(20, 100)        # earned sum 2,000 vs ref 120M
EXPOSURE_SMALL = _exposure_rows(20)
CLAIMS_SMALL = _claims_rows(20, 1000)         # incurred ~20k vs ref 80M
CLAIMS_STALE = _claims_rows(20, 1000, event="2026-08-05", report="2026-08-06")
CLAIMS_COLLISION = CLAIMS_SMALL + \
    "C1,P1,Commercial,Construction,South,Fire,2026-09-05,2026-09-06,Open,9999\n"
# exact system-of-record match -> full PASS (regions split to dodge concentration)
PREMIUM_EXACT = _premium_rows(20, 6_000_000)  # 120,000,000
EXPOSURE_EXACT = _exposure_rows(20)
CLAIMS_EXACT = _claims_rows(20, 4_000_000, regions=("North", "South", "East"))


def _wf(db_session, status="INGESTING") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status=status,
                  stage="data_prep", config={}, created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _add_refs(db_session, period="2026-09", premium=120_000_000,
              claims=80_000_000) -> None:
    """Mirror the migration-002 system-of-record totals (conftest truncates
    static seeds, so tests insert the refs they need)."""
    db_session.add_all([
        ReferenceValue(period=period, metric_key="recon_premium_total",
                       dimensions={}, value=premium,
                       source="system_of_record"),
        ReferenceValue(period=period, metric_key="recon_claims_total",
                       dimensions={}, value=claims,
                       source="system_of_record"),
    ])
    db_session.commit()


async def _prep_and_validate(db_session, wf, storage, claims_csv: str,
                             premium_csv: str = PREMIUM_SMALL,
                             exposure_csv: str = EXPOSURE_SMALL):
    for name, data, kind in (
        ("premium_2026_09.csv", premium_csv.encode(), "premium"),
        ("exposure_2026_09.csv", exposure_csv.encode(), "exposure"),
        ("claims_2026_09.csv", claims_csv.encode(), "claims"),
    ):
        f = File(workflow_id=wf.id, kind=kind, filename=name,
                 storage_path=f"workflows/{wf.id}/raw/{name}",
                 size_bytes=len(data), checksum="x", role="primary",
                 uploaded_at=datetime.now(UTC))
        db_session.add(f)
        db_session.commit()
        await storage.upload_bytes(f.storage_path, data)
    ctx = WorkflowContext(session=db_session, workflow_id=wf.id, workflow=wf,
                          storage=storage)
    prep = await run_agent(ctx, "data_prep", "data_prep", run_data_prep)
    assert prep.status in ("PASS", "WARNING"), prep.error
    return await run_agent(ctx, "validation", "validation", run_validation)


def _results(db_session, wf_id) -> dict[str, ValidationResult]:
    rows = db_session.execute(
        select(ValidationResult).where(ValidationResult.workflow_id == wf_id)
    ).scalars().all()
    return {r.check_id: r for r in rows}


@pytest.mark.asyncio
async def test_exact_match_full_pass_no_checkpoint(db_session):
    wf = _wf(db_session)
    _add_refs(db_session)
    result = await _prep_and_validate(db_session, wf, MemoryStorage(),
                                      CLAIMS_EXACT, PREMIUM_EXACT, EXPOSURE_EXACT)
    assert result.status == "PASS", result.outputs
    res = _results(db_session, wf.id)
    assert len(res) == 21  # 6 L1 + 7 L2 + 2 L3 + 6 L4
    assert all(r.status == "PASS" for r in res.values())
    assert res["recon_premium"].details["diff_pct"] == 0.0
    assert res["recon_claims"].details["diff_pct"] == 0.0
    cps = db_session.execute(
        select(HumanCheckpoint).where(HumanCheckpoint.workflow_id == wf.id)
    ).scalars().all()
    assert cps == []
    # validation endpoint serves the full matrix
    with TestClient(app) as client:
        body = client.get(f"/workflows/{wf.id}/validation").json()
        assert len(body["results"]) == 21
        by_id = {r["check_id"]: r for r in body["results"]}
        assert by_id["recon_premium"]["status"] == "PASS"


@pytest.mark.asyncio
async def test_recon_blocker_raises_cp2(db_session):
    wf = _wf(db_session)
    _add_refs(db_session)
    result = await _prep_and_validate(db_session, wf, MemoryStorage(),
                                      CLAIMS_SMALL)
    assert result.status == "BLOCKER"
    res = _results(db_session, wf.id)
    assert res["recon_premium"].status == "BLOCKER"
    assert res["recon_premium"].severity == "BLOCKER"
    assert res["recon_premium"].details["source"] == 120_000_000
    assert res["recon_claims"].status == "BLOCKER"
    # coverage / collisions clean on this fixture
    assert res["cover_claims_events"].status == "PASS"
    assert res["record_key_collisions"].status == "PASS"
    cps = db_session.execute(
        select(HumanCheckpoint).where(
            HumanCheckpoint.workflow_id == wf.id,
            HumanCheckpoint.checkpoint_type == "validation_blocker")
    ).scalars().all()
    assert len(cps) == 1
    cp = cps[0]
    assert cp.severity == "red" and cp.blocking is True
    assert cp.context["stage"] == "validation"
    assert {b["check_id"] for b in cp.context["blockers"]} == {
        "recon_premium", "recon_claims"}
    assert len(cp.context["likely_causes"]) == 5
    assert {o["decision"] for o in cp.options} == {
        "accept_exception", "request_rerun", "reject_data"}


@pytest.mark.asyncio
async def test_missing_reference_cannot_verify(db_session):
    """No system-of-record totals -> WARNING 'cannot verify', never BLOCKER."""
    wf = _wf(db_session)
    result = await _prep_and_validate(db_session, wf, MemoryStorage(),
                                      CLAIMS_SMALL)
    assert result.status == "WARNING"
    res = _results(db_session, wf.id)
    assert res["recon_premium"].status == "WARNING"
    assert "cannot verify" in res["recon_premium"].message
    assert res["recon_claims"].status == "WARNING"
    cps = db_session.execute(
        select(HumanCheckpoint).where(HumanCheckpoint.workflow_id == wf.id)
    ).scalars().all()
    assert cps == []


@pytest.mark.asyncio
async def test_stale_file_coverage_blocker(db_session):
    wf = _wf(db_session)
    _add_refs(db_session)
    result = await _prep_and_validate(db_session, wf, MemoryStorage(),
                                      CLAIMS_STALE)
    assert result.status == "BLOCKER"
    res = _results(db_session, wf.id)
    cov = res["cover_claims_events"]
    assert cov.status == "BLOCKER"
    assert "wrong file likely" in cov.message
    assert cov.details["pct"] == 0.0


@pytest.mark.asyncio
async def test_key_collision_blocker(db_session):
    wf = _wf(db_session)
    _add_refs(db_session)
    result = await _prep_and_validate(db_session, wf, MemoryStorage(),
                                      CLAIMS_COLLISION)
    assert result.status == "BLOCKER"
    res = _results(db_session, wf.id)
    coll = res["record_key_collisions"]
    assert coll.status == "BLOCKER"
    assert "never auto-merged" in coll.message


def _upload_three(client: TestClient, wid: str) -> None:
    r = client.post(f"/workflows/{wid}/upload", files=[
        ("files", ("claims_2026_09.csv", io.BytesIO(CLAIMS_SMALL.encode()),
                   "text/csv")),
        ("files", ("premium_2026_09.csv", io.BytesIO(PREMIUM_SMALL.encode()),
                   "text/csv")),
        ("files", ("exposure_2026_09.csv", io.BytesIO(EXPOSURE_SMALL.encode()),
                   "text/csv")),
    ])
    assert r.status_code == 202 and r.json()["auto_started"] is True


async def _run_to_cp2(client: TestClient, wid: str) -> dict:
    await engine.run_workflow(wid)
    st = client.get(f"/workflows/{wid}/status").json()
    assert st["status"] == "BLOCKED"
    cps = [c for c in st["pending_checkpoints"]
           if c["type"] == "validation_blocker"]
    assert len(cps) == 1
    return cps[0]


@pytest.mark.asyncio
async def test_cp2_accept_exception_api(db_session):
    _add_refs(db_session)
    with TestClient(app) as client:
        wid = client.post(
            "/workflows", json={"reporting_period": "2026-09"}).json()["id"]
        _upload_three(client, wid)
        cp = await _run_to_cp2(client, wid)
        # rationale required for accept_exception
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cp["id"], "decision": "accept_exception",
            "rationale": "too short"})
        assert r.status_code == 422
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cp["id"], "decision": "accept_exception",
            "rationale": "Known endorsement processing lag — documented "
                         "with the source team."})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "VALIDATED"
        res = _results(db_session, uuid.UUID(wid))
        assert res["recon_premium"].status == "ACCEPTED_EXCEPTION"
        assert res["recon_premium"].resolution["rationale"].startswith(
            "Known endorsement")
        assert res["recon_premium"].resolved_by is not None
        body = client.get(f"/workflows/{wid}/validation").json()
        by_id = {c["check_id"]: c for c in body["results"]}
        assert by_id["recon_premium"]["status"] == "ACCEPTED_EXCEPTION"
        assert by_id["recon_claims"]["status"] == "ACCEPTED_EXCEPTION"


@pytest.mark.asyncio
async def test_cp2_request_rerun_revalidates(db_session):
    _add_refs(db_session)
    with TestClient(app) as client:
        wid = client.post(
            "/workflows", json={"reporting_period": "2026-09"}).json()["id"]
        _upload_three(client, wid)
        cp = await _run_to_cp2(client, wid)
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cp["id"], "decision": "request_rerun"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "VALIDATING"
        await engine.run_workflow(wid)
        st = client.get(f"/workflows/{wid}/status").json()
        assert st["status"] == "BLOCKED"  # same data still blocks
        # upsert idempotency: still exactly 21 rows, fresh pending CP-2
        res = _results(db_session, uuid.UUID(wid))
        assert len(res) == 21
        assert res["recon_premium"].status == "BLOCKER"
        assert len(st["pending_checkpoints"]) == 1


@pytest.mark.asyncio
async def test_cp2_reject_data_api(db_session):
    _add_refs(db_session)
    with TestClient(app) as client:
        wid = client.post(
            "/workflows", json={"reporting_period": "2026-09"}).json()["id"]
        _upload_three(client, wid)
        cp = await _run_to_cp2(client, wid)
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cp["id"], "decision": "reject_data"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REJECTED"

"""Intake agent unit tests (§6.2): duplicates, quarantine, missing kind, stale."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.intake import classify_kind, infer_period, parse_csv, run_intake
from app.models import File, User, Workflow
from app.orchestrator import states
from app.storage.supabase import MemoryStorage

CLAIMS = (
    b"claim_id,policy_id,product,segment,region,claim_type,event_date,report_date,"
    b"status,incurred_amount\n" + b"C1,P1,Commercial,Construction,South,Fire,"
    b"2026-09-05,2026-09-06,Open,1000\n" * 3
)
CLAIMS_STALE = CLAIMS.replace(b"2026-09-05", b"2026-08-05")
PREMIUM = (b"policy_id,product,segment,region,period,written_premium,earned_premium\n"
           + b"P1,Commercial,Construction,South,2026-09,100,100\n" * 3)
EXPOSURE = (b"policy_id,product,segment,region,period,active_policies,"
            b"earned_exposure_units\n" + b"P1,Commercial,Construction,South,2026-09,1,1\n" * 3)


def _wf(db_session) -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status="INGESTING",
                  stage="intake", created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _add_file(db_session, wf, name: str, data: bytes, kind="claims") -> File:
    f = File(workflow_id=wf.id, kind=kind, filename=name,
             storage_path=f"workflows/{wf.id}/raw/{name}", size_bytes=len(data),
             checksum="x", mime="text/csv", uploaded_at=datetime.now(UTC))
    db_session.add(f)
    db_session.commit()
    return f


def _ctx(db_session, wf, storage) -> WorkflowContext:
    return WorkflowContext(session=db_session, workflow_id=wf.id, workflow=wf,
                           storage=storage)


@pytest.mark.asyncio
async def test_parse_and_classify():
    header, n = parse_csv(CLAIMS)
    assert n == 3 and header[0] == "claim_id"
    assert classify_kind("claims_2026_09.csv", header) == "claims"
    assert classify_kind("misc.csv", ["policy_id", "earned_premium"]) == "premium"
    assert classify_kind("misc.csv", ["policy_id", "active_policies"]) == "exposure"
    assert classify_kind("misc.csv", ["a", "b"]) == "unknown"
    header_full = [c.strip() for c in CLAIMS.decode().strip().split("\n")[0].split(",")]
    assert infer_period(header_full, CLAIMS, "claims") == "2026-09"
    with pytest.raises(ValueError):
        parse_csv(b"onlyonecol\n")
    with pytest.raises(ValueError):
        parse_csv(b"")


@pytest.mark.asyncio
async def test_duplicate_submission_raises_cp1(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    v1 = _add_file(db_session, wf, "claims_2026_09.csv", CLAIMS_STALE)
    v2 = _add_file(db_session, wf, "claims_2026_09_v2.csv", CLAIMS)
    _add_file(db_session, wf, "premium_2026_09.csv", PREMIUM, "premium")
    _add_file(db_session, wf, "exposure_2026_09.csv", EXPOSURE, "exposure")
    # upload raw bytes so intake can read them
    await storage.upload_bytes(v1.storage_path, CLAIMS_STALE)
    await storage.upload_bytes(v2.storage_path, CLAIMS)
    for f in db_session.query(File).filter(File.kind == "premium"):
        await storage.upload_bytes(f.storage_path, PREMIUM)
    for f in db_session.query(File).filter(File.kind == "exposure"):
        await storage.upload_bytes(f.storage_path, EXPOSURE)

    result = await run_agent(_ctx(db_session, wf, storage), "intake", "intake", run_intake)
    assert result.status == "BLOCKER"
    assert wf.status == states.BLOCKED
    db_session.refresh(v1)
    db_session.refresh(v2)
    assert v1.role == "duplicate" and v2.role == "duplicate"
    from app.models import HumanCheckpoint

    row = db_session.get(HumanCheckpoint, result.checkpoint_id)
    assert row.title.startswith("Two claims files")
    assert len(row.options) == 3  # select v1 / select v2 / reject both
    profiles = row.context["profiles"]
    assert profiles[0]["stale"] is True  # v1 holds Aug data


@pytest.mark.asyncio
async def test_quarantine_and_missing_kind(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    bad = _add_file(db_session, wf, "claims_bad.csv", b"\x00\x01binary", "claims")
    p = _add_file(db_session, wf, "premium_2026_09.csv", PREMIUM, "premium")
    e = _add_file(db_session, wf, "exposure_2026_09.csv", EXPOSURE, "exposure")
    await storage.upload_bytes(p.storage_path, PREMIUM)
    await storage.upload_bytes(e.storage_path, EXPOSURE)
    # note: bad file bytes NOT uploaded -> download fails too

    result = await run_agent(_ctx(db_session, wf, storage), "intake", "intake", run_intake)
    assert result.status == "BLOCKER"
    db_session.refresh(bad)
    assert bad.role == "quarantined"
    from app.models import HumanCheckpoint

    row = db_session.get(HumanCheckpoint, result.checkpoint_id)
    assert "claims" in row.title  # missing claims kind


@pytest.mark.asyncio
async def test_clean_pass(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    f = _add_file(db_session, wf, "claims_2026_09.csv", CLAIMS)
    p = _add_file(db_session, wf, "premium_2026_09.csv", PREMIUM, "premium")
    e = _add_file(db_session, wf, "exposure_2026_09.csv", EXPOSURE, "exposure")
    await storage.upload_bytes(f.storage_path, CLAIMS)
    await storage.upload_bytes(p.storage_path, PREMIUM)
    await storage.upload_bytes(e.storage_path, EXPOSURE)

    result = await run_agent(_ctx(db_session, wf, storage), "intake", "intake", run_intake)
    assert result.status == "PASS"
    assert wf.status == states.INGESTING  # stays until data_prep advances it
    db_session.refresh(f)
    assert f.role == "primary" and f.row_count == 3
    assert f.period_inferred == "2026-09"

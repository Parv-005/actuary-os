"""Data-prep agent tests (§6.3): canonical output, transform log, CP-3,
blocker on missing required column, region fix, collisions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.data_prep import run_data_prep
from app.models import DatasetVersion, File, HumanCheckpoint, User, Workflow
from app.storage.supabase import MemoryStorage

PREMIUM = (
    "policyno,product,segment,region,period,premium,earned_premium\n"
    "P1,commercial,Construction,South,2026-09,\"₹1,20,000\",1000\n"
    "P2,commercial,SME,North,2026-09,800,800\n"
    "P1,commercial,Construction,South,2026-09,\"₹1,20,000\",1000\n"  # exact dupe
)
EXPOSURE = (
    "policyno,product,segment,region,period,active_policies,exposure\n"
    "P1,commercial,Construction,South,2026-09,1,1.5\n"
    "P2,commercial,SME,North,2026-09,1,1.0\n"
)
# claims: claim_amt alias, blank region (fix via join), dupes, collision, N/A reject
CLAIMS = (
    "claim_id,policy_id,product,segment,region,claim_type,event_date,report_date,"
    "status,claim_amt\n"
    "C1,P1,commercial,Construction,,Fire,2026-09-05,2026-09-06,Open,1000\n"
    "C2,P2,commercial,SME,North,Theft,2026-09-06,2026-09-07,Closed,500\n"
    "C3,P2,commercial,SME,North,Theft,2026-09-06,2026-09-07,Closed,N/A\n"
    "C1,P1,commercial,Construction,,Fire,2026-09-05,2026-09-06,Open,1000\n"  # exact dupe
    "C4,P2,commercial,SME,North,Theft,2026-09-08,2026-09-09,Open,100\n"
    "C4,P2,commercial,SME,North,Theft,2026-09-08,2026-09-09,Open,999\n"  # key collision
    "C5,P1,commercial,Construction,South,Flood,31-09-2026,2026-09-10,Closed,200\n"  # bad date
)
CLAIMS_NO_INCURRED = CLAIMS.replace("claim_amt", "zzz_unrelated")
CLAIMS_BIZCLASS = CLAIMS.replace("status,claim_amt",
                                 "status,claim_amt,advisor_name").replace(
    ",Open,1000", ",Open,1000,Smith").replace(
    ",Closed,500", ",Closed,500,Jones").replace(
    ",Closed,N/A", ",Closed,N/A,Jones").replace(
    ",Open,100\n", ",Open,100,Jones\n").replace(
    ",Open,999", ",Open,999,Jones").replace(
    ",Closed,200", ",Closed,200,Smith")


def _wf(db_session, config=None) -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status="INGESTING",
                  stage="data_prep", config=config or {}, created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _add(db_session, wf, name, data, kind):
    f = File(workflow_id=wf.id, kind=kind, filename=name,
             storage_path=f"workflows/{wf.id}/raw/{name}",
             size_bytes=len(data), checksum="x", role="primary",
             uploaded_at=datetime.now(UTC))
    db_session.add(f)
    db_session.commit()
    return f


async def _seed_and_run(db_session, wf, storage, claims_csv: str):
    files = [
        ("premium_2026_09.csv", PREMIUM.encode(), "premium"),
        ("exposure_2026_09.csv", EXPOSURE.encode(), "exposure"),
        ("claims_2026_09.csv", claims_csv.encode(), "claims"),
    ]
    for name, data, kind in files:
        f = _add(db_session, wf, name, data, kind)
        await storage.upload_bytes(f.storage_path, data)
    return await run_agent(
        WorkflowContext(session=db_session, workflow_id=wf.id, workflow=wf,
                        storage=storage),
        "data_prep", "data_prep", run_data_prep,
    )


def _dv_by_kind(db_session, wf_id) -> dict[str, DatasetVersion]:
    rows = db_session.query(DatasetVersion).filter(
        DatasetVersion.workflow_id == wf_id).all()
    return {dv.kind: dv for dv in rows}


@pytest.mark.asyncio
async def test_full_pipeline(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    result = await _seed_and_run(db_session, wf, storage, CLAIMS)

    # tiny fixture: 1/6 incurred values rejected (N/A) -> >1% -> WARNING (correct)
    assert result.status == "WARNING"
    assert any("incurred_amount" in w for w in result.outputs["warnings"])
    dvs = _dv_by_kind(db_session, wf.id)
    assert set(dvs) == {"claims", "premium", "exposure"}

    prem_log = dvs["premium"].transform_log
    # rupee parsed + exact dupe removed + alias premium->written_premium
    assert prem_log["exact_duplicates_removed"]["count"] == 1
    assert prem_log["column_map"]["premium"] == "written_premium"
    wc = next(t for t in prem_log["type_conversions"] if t["column"] == "written_premium")
    assert wc["converted"] == 3 and wc["rejected"] == 0  # conversion precedes dedup

    cl_log = dvs["claims"].transform_log
    assert cl_log["exact_duplicates_removed"]["count"] == 1
    assert cl_log["column_map"]["claim_amt"] == "incurred_amount"
    # ambiguous N/A rejected individually
    inc = next(t for t in cl_log["type_conversions"] if t["column"] == "incurred_amount")
    assert inc["rejected"] == 1 and "N/A" in str(inc["rejected_samples"])
    # invalid date flagged
    evd = next(t for t in cl_log["type_conversions"] if t["column"] == "event_date")
    assert evd["rejected"] == 1
    # region auto-fix via policy join (C1 blank -> South from P1)
    assert cl_log["region_fix"]["filled"] == 1
    # key collision flagged (C4 has 100 vs 999)
    assert cl_log["key_collisions"]["count"] >= 2
    # categorical standardization: commercial -> Commercial
    assert any(c["column"] == "product" for c in cl_log["categorical_standardized"])

    # processed CSV round-trips through storage with canonical columns
    data = await storage.download_bytes(dvs["claims"].storage_path)
    header = data.decode().splitlines()[0]
    assert header.startswith("claim_id,policy_id,product,segment,region,claim_type,"
                             "event_date,report_date,status,incurred_amount")
    assert dvs["claims"].row_count == 6  # 7 raw - 1 exact dupe


@pytest.mark.asyncio
async def test_missing_incurred_blocker(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    result = await _seed_and_run(db_session, wf, storage, CLAIMS_NO_INCURRED)
    assert result.status == "BLOCKER"
    assert wf.status == "BLOCKED"
    cp = db_session.get(HumanCheckpoint, result.checkpoint_id)
    assert "incurred_amount" in cp.title
    assert "loss_ratio" in " ".join(cp.context["affected_metrics"])
    assert "claim_severity" in " ".join(cp.context["affected_metrics"])


@pytest.mark.asyncio
async def test_unmapped_column_raises_cp3(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    result = await _seed_and_run(db_session, wf, storage, CLAIMS_BIZCLASS)
    assert result.status == "WARNING"  # rejection warning; yellow CP-3 never pauses
    cps = db_session.query(HumanCheckpoint).filter(
        HumanCheckpoint.workflow_id == wf.id,
        HumanCheckpoint.checkpoint_type == "schema_mapping").all()
    assert len(cps) == 1
    assert cps[0].severity == "yellow" and cps[0].blocking is False
    unmapped = cps[0].context["unmapped"]
    assert unmapped[0]["column"] == "advisor_name"
    assert cps[0].options[-1]["decision"] == "ignore_column"
    assert any(o["decision"] == "confirm_mapping" for o in cps[0].options)

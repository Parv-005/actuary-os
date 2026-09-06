"""Phase 7+8 acceptance: the real seeded sample data (§15) through the engine.

Verifies the demo storyline numbers in transform_log (12 exact dupes removed,
15 regions auto-fixed) and the seeded -2.10% premium reconciliation blocker:
CP-1 -> select v2 -> prep -> validation BLOCKED (CP-2) -> accept with
rationale -> VALIDATED with the exception carried on the check row.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import DatasetVersion, ReferenceValue
from app.orchestrator import engine
from app.storage.supabase import get_storage

SAMPLE = Path(__file__).resolve().parent.parent.parent.parent / "sample_data"


@pytest.mark.asyncio
async def test_sample_data_through_intake_and_prep(db_session):
    # seed the shared Storage singleton demo/ exactly like scripts/seed.py
    storage = get_storage()
    for name in ("claims_2026_09.csv", "claims_2026_09_v2.csv",
                 "premium_2026_09.csv", "exposure_2026_09.csv"):
        await storage.upload_bytes(f"demo/{name}", (SAMPLE / name).read_bytes())
    # system-of-record totals (migration 002 seeds; conftest truncates statics)
    db_session.add_all([
        ReferenceValue(period="2026-09", metric_key="recon_premium_total",
                       dimensions={}, value=120_000_000,
                       source="system_of_record"),
        ReferenceValue(period="2026-09", metric_key="recon_claims_total",
                       dimensions={}, value=80_000_000,
                       source="system_of_record"),
    ])
    db_session.commit()

    with TestClient(app) as client:
        r = client.post("/workflows", json={"reporting_period": "2026-09", "demo": True})
        wid = r.json()["id"]
        # run 1: intake -> CP-1 (stale v1 + v2)
        await engine.run_workflow(wid)
        st = client.get(f"/workflows/{wid}/status").json()
        assert st["status"] == "BLOCKED"
        cps = st["pending_checkpoints"]
        assert cps and cps[0]["type"] == "input_exception"
        v2 = next(o for o in cps[0]["options"] if "v2" in o["label"])

        # select v2 (decisions API)
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cps[0]["id"], "decision": "select_file",
            "payload": {"file_id": v2["payload"]["file_id"]}})
        assert r.status_code == 202

        # run 2: intake passes -> data_prep runs -> validation BLOCKS on the
        # seeded -2.10% premium reconciliation mismatch (CP-2 red)
        await engine.run_workflow(wid)
        st2 = client.get(f"/workflows/{wid}/status").json()
        assert st2["status"] == "BLOCKED"
        cps = [c for c in st2["pending_checkpoints"]
               if c["type"] == "validation_blocker"]
        assert len(cps) == 1
        assert {b["check_id"] for b in cps[0]["context"]["blockers"]} == {
            "recon_premium"}
        assert "likely_causes" in cps[0]["context"]

        val = client.get(f"/workflows/{wid}/validation").json()["results"]
        by_check = {r["check_id"]: r for r in val}
        assert len(val) == 21
        assert by_check["recon_premium"]["status"] == "BLOCKER"
        assert by_check["recon_premium"]["details"]["diff_pct"] == pytest.approx(
            -2.10, abs=0.01)
        # claims recon only warns (-1.17%); outlier + small-sample warn too
        assert by_check["recon_claims"]["status"] == "WARNING"
        assert by_check["behav_outlier_claims"]["status"] == "WARNING"
        assert "unusual but not proven invalid" in \
            by_check["behav_outlier_claims"]["message"]
        assert by_check["behav_small_sample"]["status"] == "WARNING"
        assert "Marine Cargo" in by_check["behav_small_sample"]["message"]
        assert by_check["cover_claims_events"]["status"] == "PASS"
        assert by_check["record_key_collisions"]["status"] == "PASS"

        # CP-2 accept with rationale -> VALIDATED, exception carried on the row
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cps[0]["id"], "decision": "accept_exception",
            "rationale": "Known endorsement processing lag — documented "
                         "with the source team."})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "VALIDATED"
        st3 = client.get(f"/workflows/{wid}/status").json()
        assert st3["status"] == "VALIDATED"
        assert not st3["pending_checkpoints"]
        val2 = client.get(f"/workflows/{wid}/validation").json()["results"]
        by_check2 = {r2["check_id"]: r2 for r2 in val2}
        assert by_check2["recon_premium"]["status"] == "ACCEPTED_EXCEPTION"
        assert by_check2["recon_premium"]["resolution"]["rationale"].startswith(
            "Known endorsement")

        dvs = db_session.execute(
            select(DatasetVersion).where(DatasetVersion.workflow_id == wid)
        ).scalars().all()
        by_kind = {dv.kind: dv for dv in dvs}
        assert set(by_kind) == {"claims", "premium", "exposure"}

        cl = by_kind["claims"].transform_log
        assert cl["exact_duplicates_removed"]["count"] == 12
        assert cl["region_fix"]["filled"] == 15
        assert by_kind["premium"].row_count == 6000
        assert by_kind["exposure"].row_count == 6000
        assert by_kind["claims"].row_count == 280

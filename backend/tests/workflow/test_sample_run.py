"""Phase 7 acceptance: the real seeded sample data (§15) through the engine.

Verifies the demo storyline numbers in transform_log: 12 exact dupes removed,
15 regions auto-fixed, -2.1% premium is left to validation (Phase 8), CP-3
not raised (all demo columns map), processed datasets produced.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import DatasetVersion
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

        # run 2: intake passes -> data_prep runs -> VALIDATING
        await engine.run_workflow(wid)
        st2 = client.get(f"/workflows/{wid}/status").json()
        assert st2["status"] == "VALIDATING"
        assert not st2["pending_checkpoints"]  # no CP-3: all demo columns map

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

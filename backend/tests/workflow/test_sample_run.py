"""Phase 7+8+9 acceptance: the real seeded sample data (§15) through the engine.

Verifies the demo storyline end to end: transform_log (12 exact dupes
removed, 15 regions auto-fixed), the seeded -2.10% premium reconciliation
blocker (CP-2 -> accept -> VALIDATED), then analysis (Phase 9): portfolio
LR 63.1%->67.3%, AvE +4.5pp, Construction/South 62.9%->78.1% (+15.2pp),
severity +13.1% / frequency +2.4%, ~61% contribution, Marine Cargo
small-sample flag, Health flat.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import DatasetVersion, ReferenceValue, User, Workflow
from app.orchestrator import engine
from app.storage.supabase import get_storage

SAMPLE = Path(__file__).resolve().parent.parent.parent.parent / "sample_data"
HISTORY_LR = [("2026-04", 0.615), ("2026-05", 0.622), ("2026-06", 0.618),
              ("2026-07", 0.625), ("2026-08", 0.631)]


async def _wait_for_status(client: TestClient, wid: str, want: str,
                           timeout_s: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict | None = None
    while time.monotonic() < deadline:
        await engine.run_workflow(wid)
        last = client.get(f"/workflows/{wid}/status").json()
        if last["status"] == want:
            return last
    raise AssertionError(f"timed out waiting for {want}, last={last}")


@pytest.mark.asyncio
async def test_sample_data_through_intake_and_prep(db_session):
    # seed the shared Storage singleton demo/ exactly like scripts/seed.py
    storage = get_storage()
    for name in ("claims_2026_09.csv", "claims_2026_09_v2.csv",
                 "premium_2026_09.csv", "exposure_2026_09.csv"):
        await storage.upload_bytes(f"demo/{name}", (SAMPLE / name).read_bytes())
    # system-of-record totals + expected LR + history series
    # (migration 002 seeds; conftest truncates statics)
    db_session.add_all([
        ReferenceValue(period="2026-09", metric_key="recon_premium_total",
                       dimensions={}, value=120_000_000,
                       source="system_of_record"),
        ReferenceValue(period="2026-09", metric_key="recon_claims_total",
                       dimensions={}, value=80_000_000,
                       source="system_of_record"),
        ReferenceValue(period="2026-09", metric_key="expected_loss_ratio",
                       dimensions={}, value=0.628, source="methodology"),
    ] + [ReferenceValue(period=p, metric_key="historical_loss_ratio",
                        dimensions={}, value=v, source="system_of_record")
         for p, v in HISTORY_LR])
    # seeded August prior workflow with history datasets (mirrors seed.py):
    # the source of prev_value + contribution weights
    actor = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T",
                 role="actuary")
    db_session.add(actor)
    db_session.flush()
    prior = Workflow(human_ref="MPR-2026-08-001", portfolio="General Insurance",
                     reporting_period="2026-08", status="COMPLETED",
                     stage="COMPLETED", config={"seeded": True},
                     created_by=actor.id)
    db_session.add(prior)
    db_session.flush()
    for name, kind in (("claims_2026_08.csv", "claims"),
                       ("premium_2026_08.csv", "premium"),
                       ("exposure_2026_08.csv", "exposure")):
        data = (SAMPLE / "history" / name).read_bytes()
        path = f"workflows/{prior.id}/processed/{kind}.csv"
        await storage.upload_bytes(path, data)
        db_session.add(DatasetVersion(
            workflow_id=prior.id, kind=kind, source_file_ids=[],
            storage_path=path, row_count=0, column_map={},
            transform_log={"seeded": True},
            checksum=hashlib.sha256(data).hexdigest()))
    db_session.commit()

    with TestClient(app) as client:
        r = client.post("/workflows", json={"reporting_period": "2026-09", "demo": True})
        wid = r.json()["id"]
        # run 1: intake -> CP-1 (stale v1 + v2)
        st = await _wait_for_status(client, wid, "BLOCKED")
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
        st2 = await _wait_for_status(client, wid, "BLOCKED")
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
        val2 = client.get(f"/workflows/{wid}/validation").json()["results"]
        by_check2 = {r2["check_id"]: r2 for r2 in val2}
        assert by_check2["recon_premium"]["status"] == "ACCEPTED_EXCEPTION"
        assert by_check2["recon_premium"]["resolution"]["rationale"].startswith(
            "Known endorsement")

        # Phase 7 outputs intact
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

        # run 3: analysis -> ANALYZED, full §15 storyline in metrics
        st3 = await _wait_for_status(client, wid, "ANALYZED")
        assert not st3["pending_checkpoints"]
        states = {s["stage"]: s["state"] for s in st3["stage_statuses"]}
        assert states["analysis"] == "succeeded"

        body = client.get(f"/workflows/{wid}/metrics").json()
        assert body["undefined"] == []
        by_metric = {(m["metric_key"], tuple(sorted(m["dimensions"].items()))): m
                     for m in body["metrics"]}

        port = by_metric[("loss_ratio", ())]
        assert port["value"] == pytest.approx(0.673, abs=0.001)
        assert port["prev_value"] == pytest.approx(0.631, abs=0.001)
        assert port["expected_value"] == pytest.approx(0.628)
        assert port["delta_pp"] == pytest.approx(4.2, abs=0.05)
        ave = by_metric[("ave_variance", ())]
        assert ave["value"] == pytest.approx(4.5, abs=0.05)

        cs = (("product", "Commercial"), ("region", "South"),
              ("segment", "Construction"))
        cs_lr = by_metric[("loss_ratio", cs)]
        assert cs_lr["value"] == pytest.approx(0.781, abs=0.001)
        assert cs_lr["prev_value"] == pytest.approx(0.629, abs=0.001)
        assert cs_lr["delta_pp"] == pytest.approx(15.2, abs=0.05)
        cs_sev = by_metric[("claim_severity", cs)]
        assert cs_sev["delta_pp"] == pytest.approx(13.1, abs=0.05)
        cs_freq = by_metric[("claim_frequency", cs)]
        assert cs_freq["delta_pp"] == pytest.approx(2.4, abs=0.1)

        contribs = [m for m in body["metrics"]
                    if m["metric_key"] == "deterioration_contribution"
                    and m["value"] is not None]
        top = max(contribs, key=lambda m: m["value"])
        assert top["dimensions"] == {"product": "Commercial",
                                     "segment": "Construction",
                                     "region": "South"}
        assert top["value"] == pytest.approx(60.8, abs=1.0)
        assert top["inputs"]["prior_ref"] == "MPR-2026-08-001"

        marine = by_metric[("loss_ratio", (("product", "Commercial"),
                                           ("segment", "Marine Cargo")))]
        assert marine["flags"]["small_sample"] is True
        health = by_metric[("loss_ratio", (("product", "Health"),))]
        assert health["value"] == pytest.approx(0.58, abs=0.005)
        assert health["prev_value"] == pytest.approx(0.58, abs=0.005)

        series = client.get(f"/workflows/{wid}/metrics",
                            params={"series": "loss_ratio"}).json()
        assert [p["period"] for p in series["series"]] == [
            "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
        assert series["series"][-1]["value"] == pytest.approx(
            0.673, abs=0.001)
        assert series["series"][-1]["source"] == "computed"

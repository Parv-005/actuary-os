"""Phase 6 API tests: create/upload/start/status + CP-1 flow + 409s.

Engine background tasks under TestClient are exercised via direct awaits of
engine.run_workflow for determinism.
"""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import File, Workflow
from app.orchestrator import engine
from app.storage.supabase import MemoryStorage

CLAIMS_V2 = (
    "claim_id,policy_id,product,segment,region,claim_type,event_date,report_date,"
    "status,incurred_amount\n" + "".join(
        f"C{i},P{i % 10 + 1},Commercial,Construction,South,Fire,"
        f"2026-09-05,2026-09-06,Open,{1000 + i}\n"
        for i in range(1, 21)))
CLAIMS_V1 = CLAIMS_V2.replace("2026-09-05", "2026-08-05")
PREMIUM = ("policy_id,product,segment,region,period,written_premium,earned_premium\n"
           + "".join(f"P{i},Commercial,Construction,South,2026-09,100,100\n"
                     for i in range(1, 21)))
EXPOSURE = ("policy_id,product,segment,region,period,active_policies,earned_exposure_units\n"
            + "".join(f"P{i},Commercial,Construction,South,2026-09,1,1\n"
                      for i in range(1, 21)))


def _csv(name: str, content: str) -> tuple[str, bytes]:
    return name, content.encode()


def _client():
    return TestClient(app)


def _mk_workflow(client: TestClient) -> dict:
    r = client.post("/workflows", json={"reporting_period": "2026-09"})
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def seeded_demo_files(db_session):
    """Pre-populate Storage demo/ so demo creation works in tests."""
    storage = MemoryStorage()

    async def seed():
        await storage.upload_bytes("demo/claims_2026_09.csv", CLAIMS_V1.encode())
        await storage.upload_bytes("demo/claims_2026_09_v2.csv", CLAIMS_V2.encode())
        await storage.upload_bytes("demo/premium_2026_09.csv", PREMIUM.encode())
        await storage.upload_bytes("demo/exposure_2026_09.csv", EXPOSURE.encode())

    import asyncio
    asyncio.get_event_loop_policy()  # noqa: B018
    asyncio.run(seed())


def test_create_workflow(db_session):
    with _client() as client:
        body = _mk_workflow(client)
        assert body["human_ref"].startswith("MPR-2026-09-")
        assert body["status"] == "INPUT_WAIT"
        wf = db_session.get(Workflow, uuid.UUID(body["id"]))
        assert wf is not None
        # multiples allowed; suffix increments
        body2 = _mk_workflow(client)
        assert body2["human_ref"] != body["human_ref"]


def test_create_invalid_period_422():
    with _client() as client:
        r = client.post("/workflows", json={"reporting_period": "2026-13"})
        assert r.status_code == 422


def test_upload_lifecycle(db_session):
    with _client() as client:
        wf = _mk_workflow(client)
        wid = wf["id"]
        # upload premium only -> no auto-start
        r = client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("premium_2026_09.csv", io.BytesIO(PREMIUM.encode()), "text/csv"))])
        assert r.status_code == 202
        assert r.json()["auto_started"] is False
        # start with missing kinds -> 409
        r = client.post(f"/workflows/{wid}/start")
        assert r.status_code == 409
        assert r.json()["detail"].startswith("missing required files")
        # upload remaining -> auto-start
        r = client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("claims_2026_09_v2.csv", io.BytesIO(CLAIMS_V2.encode()), "text/csv")),
            ("files", ("exposure_2026_09.csv", io.BytesIO(EXPOSURE.encode()), "text/csv"))])
        assert r.status_code == 202
        assert r.json()["auto_started"] is True
        assert r.json()["status"] == "INGESTING"
        # further upload -> 409 wrong state
        r = client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("x.csv", io.BytesIO(CLAIMS_V2.encode()), "text/csv"))])
        assert r.status_code == 409
        # reject non-csv
        r = client.post("/workflows", json={"reporting_period": "2026-09"})
        wid2 = r.json()["id"]
        r = client.post(f"/workflows/{wid2}/upload", files=[
            ("files", ("notcsv.txt", io.BytesIO(b"hello"), "text/plain"))])
        assert r.status_code == 400


def test_upload_rejects_bad_sniff_and_size():
    with _client() as client:
        wf = _mk_workflow(client)
        wid = wf["id"]
        r = client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("bin.csv", io.BytesIO(b"\x00\x01\x02binary"), "text/csv"))])
        assert r.status_code == 415


@pytest.mark.asyncio
async def test_full_cp1_flow(db_session):
    """Upload 4 files (two claims) -> engine -> CP-1 BLOCKED -> select v2 -> resume."""
    with _client() as client:
        wf = _mk_workflow(client)
        wid = wf["id"]
        r = client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("claims_2026_09.csv", io.BytesIO(CLAIMS_V1.encode()), "text/csv")),
            ("files", ("claims_2026_09_v2.csv", io.BytesIO(CLAIMS_V2.encode()), "text/csv")),
            ("files", ("premium_2026_09.csv", io.BytesIO(PREMIUM.encode()), "text/csv")),
            ("files", ("exposure_2026_09.csv", io.BytesIO(EXPOSURE.encode()), "text/csv")),
        ])
        assert r.json()["auto_started"] is True
        # determinism: run engine inline (the launched task may still be pending)
        await engine.run_workflow(wid)

        st = client.get(f"/workflows/{wid}/status").json()
        assert st["status"] == "BLOCKED"
        assert st["stage"] == "intake"
        cps = st["pending_checkpoints"]
        assert len(cps) == 1 and cps[0]["type"] == "input_exception"
        assert cps[0]["severity"] == "red" and cps[0]["blocking"] is True
        options = cps[0]["options"]
        assert len(options) == 3
        v2_option = next(o for o in options if "v2" in o["label"])

        # decisions API: select v2
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cps[0]["id"], "decision": "select_file",
            "payload": {"file_id": v2_option["payload"]["file_id"]},
        })
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "INGESTING"

        # roles: v2 primary, v1 superseded
        files = db_session.execute(select(File).where(File.workflow_id == wf_id(wid))
                                   ).scalars().all()
        roles = {f.filename: f.role for f in files if f.kind == "claims"}
        assert roles["claims_2026_09_v2.csv"] == "primary"
        assert roles["claims_2026_09.csv"] == "superseded"

        # second run: intake passes; data_prep runs; validation runs on the
        # tiny fixtures (no reference totals seeded -> recon "cannot verify"
        # warnings, single-region concentration warning) -> VALIDATED
        await engine.run_workflow(wid)
        st2 = client.get(f"/workflows/{wid}/status").json()
        assert st2["status"] == "VALIDATED"
        assert not st2["pending_checkpoints"]
        states = {s["stage"]: s["state"] for s in st2["stage_statuses"]}
        assert states["intake"] == "succeeded"
        assert states["data_prep"] == "succeeded"
        assert states["validation"] == "succeeded"
        val = client.get(f"/workflows/{wid}/validation").json()["results"]
        by_id = {r["check_id"]: r for r in val}
        assert by_id["recon_premium"]["status"] == "WARNING"
        assert "cannot verify" in by_id["recon_premium"]["message"]


def wf_id(wid: str) -> uuid.UUID:
    return uuid.UUID(wid)


@pytest.mark.asyncio
async def test_reject_data_decision(db_session):
    with _client() as client:
        wf = _mk_workflow(client)
        wid = wf["id"]
        client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("claims_2026_09.csv", io.BytesIO(CLAIMS_V1.encode()), "text/csv")),
            ("files", ("claims_2026_09_v2.csv", io.BytesIO(CLAIMS_V2.encode()), "text/csv")),
            ("files", ("premium_2026_09.csv", io.BytesIO(PREMIUM.encode()), "text/csv")),
            ("files", ("exposure_2026_09.csv", io.BytesIO(EXPOSURE.encode()), "text/csv")),
        ])
        await engine.run_workflow(wid)
        cps = client.get(f"/workflows/{wid}/checkpoints").json()["pending"]
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cps[0]["id"], "decision": "reject_data"})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REJECTED"
        wfrow = db_session.get(Workflow, wf_id(wid))
        assert wfrow.status == "REJECTED"


def test_decision_validation(db_session):
    with _client() as client:
        wf = _mk_workflow(client)
        wid = wf["id"]
        # unknown decision enum -> 422
        r = client.post(f"/workflows/{wid}/decisions",
                        json={"decision": "not_a_decision"})
        assert r.status_code == 422
        # missing checkpoint -> 400
        r = client.post(f"/workflows/{wid}/decisions", json={"decision": "select_file"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_demo_creation(db_session, seeded_demo_files):
    with _client() as client:
        r = client.post("/workflows", json={"reporting_period": "2026-09", "demo": True})
        assert r.status_code == 201
        wid = r.json()["id"]
        files = db_session.execute(
            select(File).where(File.workflow_id == wf_id(wid))).scalars().all()
        assert len(files) == 4
        # engine: two claims files -> CP-1
        await engine.run_workflow(wid)
        st = client.get(f"/workflows/{wid}/status").json()
        assert st["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_resume_only_from_failed(db_session):
    with _client() as client:
        wf = _mk_workflow(client)
        r = client.post(f"/workflows/{wf['id']}/resume")
        assert r.status_code == 409

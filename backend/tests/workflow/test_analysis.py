"""Analysis agent tests (§6.5): metric math on fixtures, undefined handling,
prior/delta/contribution wiring, small-sample flags, and the metrics API
(filters + ?series=)."""
from __future__ import annotations

import asyncio
import hashlib
import io
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.analysis import run_analysis
from app.agents.base import run_agent
from app.agents.context import WorkflowContext
from app.agents.data_prep import run_data_prep
from app.main import app
from app.models import DatasetVersion, File, Metric, ReferenceValue, User, Workflow
from app.orchestrator import engine
from app.storage.supabase import MemoryStorage

PORTFOLIO = "General Insurance"


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


def _claims_rows(n: int, amount: int) -> str:
    lines = ["claim_id,policy_id,product,segment,region,claim_type,event_date,"
             "report_date,status,incurred_amount"]
    lines += [f"C{i},P{i},Commercial,Construction,South,Fire,2026-09-05,"
              f"2026-09-06,Open,{amount}" for i in range(1, n + 1)]
    return "\n".join(lines) + "\n"


PREMIUM = _premium_rows(20, 100)      # earned 2,000
EXPOSURE = _exposure_rows(20)
CLAIMS = _claims_rows(20, 1000)       # incurred 20,000 -> LR 10.0
PREMIUM_ZERO = _premium_rows(20, 0)
PRIOR_PREMIUM = _premium_rows(20, 100)
PRIOR_CLAIMS = _claims_rows(20, 600)  # prior LR 6.0


def _wf(db_session, period="2026-09", status="INGESTING") -> Workflow:
    user = User(email=f"{uuid.uuid4().hex[:8]}@t.app", name="T", role="actuary")
    db_session.add(user)
    wf = Workflow(human_ref=f"MPR-{period}-{uuid.uuid4().hex[:6].upper()}",
                  portfolio=PORTFOLIO, reporting_period=period, status=status,
                  stage="data_prep", config={}, created_by=user.id)
    db_session.add(wf)
    db_session.commit()
    return wf


def _add_refs(db_session, expected_lr: float | None = 0.628) -> None:
    db_session.add(ReferenceValue(period="2026-09",
                                  metric_key="recon_premium_total",
                                  dimensions={}, value=120_000_000,
                                  source="system_of_record"))
    db_session.add(ReferenceValue(period="2026-09",
                                  metric_key="recon_claims_total",
                                  dimensions={}, value=80_000_000,
                                  source="system_of_record"))
    if expected_lr is not None:
        db_session.add(ReferenceValue(period="2026-09",
                                      metric_key="expected_loss_ratio",
                                      dimensions={}, value=expected_lr,
                                      source="methodology"))
    db_session.commit()


async def _make_prior(db_session, storage, premium_csv: str,
                      claims_csv: str) -> Workflow:
    """Prior Aug workflow with processed datasets loadable from storage."""
    pw = _wf(db_session, period="2026-08", status="COMPLETED")
    for kind, data in (("premium", premium_csv.encode()),
                       ("exposure", _exposure_rows(20).encode()),
                       ("claims", claims_csv.encode())):
        path = f"workflows/{pw.id}/processed/{kind}.csv"
        await storage.upload_bytes(path, data)
        db_session.add(DatasetVersion(
            workflow_id=pw.id, kind=kind, source_file_ids=[],
            storage_path=path, row_count=20, column_map={}, transform_log={},
            checksum=hashlib.sha256(data).hexdigest()))
    db_session.commit()
    return pw


async def _prep_and_analyze(db_session, wf, storage, claims_csv: str,
                            premium_csv: str = PREMIUM):
    for name, data, kind in (
        ("premium_2026_09.csv", premium_csv.encode(), "premium"),
        ("exposure_2026_09.csv", EXPOSURE.encode(), "exposure"),
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
    return await run_agent(ctx, "analysis", "analysis", run_analysis)


def _metrics(db_session, wf_id) -> list[Metric]:
    return db_session.execute(
        select(Metric).where(Metric.workflow_id == wf_id)).scalars().all()


def _one(rows: list[Metric], key: str, dims: dict) -> Metric:
    return next(m for m in rows
                if m.metric_key == key and (m.dimensions or {}) == dims)


async def _wait_for_status(client: TestClient, wid: str, want: str,
                           timeout_s: float = 120.0) -> dict:
    """Converge to `want`: the decisions-triggered background task and this
    direct await race (two loops, one DB lease) — both converge to the same
    final state, so keep driving until the poll shows it."""
    deadline = time.monotonic() + timeout_s
    last: dict | None = None
    while time.monotonic() < deadline:
        await engine.run_workflow(wid)
        last = client.get(f"/workflows/{wid}/status").json()
        if last["status"] == want:
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out waiting for {want}, last={last}")


@pytest.mark.asyncio
async def test_portfolio_math_no_baselines(db_session):
    wf = _wf(db_session)
    result = await _prep_and_analyze(db_session, wf, MemoryStorage(), CLAIMS)
    # no expected LR, no prior -> ave + contribution unavailable -> WARNING
    assert result.status == "WARNING"
    assert {u["metric_key"] for u in result.outputs["unavailable"]} == {
        "ave_variance", "deterioration_contribution"}
    rows = _metrics(db_session, wf.id)
    port = _one(rows, "loss_ratio", {})
    assert float(port.value) == 20_000 / 2_000
    assert port.prev_value is None and port.delta_pp is None
    assert port.unit == "ratio"
    assert port.formula == "loss_ratio = Σincurred/Σearned_premium"
    assert port.inputs["dataset_version_ids"] and port.module_version == "m1"
    freq = _one(rows, "claim_frequency", {})
    assert float(freq.value) == 20 / 20.0  # exposure units, not premium
    sev = _one(rows, "claim_severity", {})
    assert float(sev.value) == 20_000 / 20


@pytest.mark.asyncio
async def test_zero_premium_undefined_not_infinite(db_session):
    wf = _wf(db_session)
    result = await _prep_and_analyze(db_session, wf, MemoryStorage(), CLAIMS,
                                     PREMIUM_ZERO)
    rows = _metrics(db_session, wf.id)
    port = _one(rows, "loss_ratio", {})
    assert port.value is None
    assert port.undefined_reason == "premium base is zero"
    assert any(u["metric_key"] == "loss_ratio"
               for u in result.outputs["unavailable"])


@pytest.mark.asyncio
async def test_ave_with_expected_and_prior_deltas(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    _add_refs(db_session, expected_lr=0.628)
    await _make_prior(db_session, storage, PRIOR_PREMIUM, PRIOR_CLAIMS)
    result = await _prep_and_analyze(db_session, wf, storage, CLAIMS)
    assert result.status == "PASS", result.outputs
    rows = _metrics(db_session, wf.id)
    port = _one(rows, "loss_ratio", {})
    assert float(port.value) == 10.0
    assert float(port.prev_value) == 6.0
    assert float(port.delta_pp) == (10.0 - 6.0) * 100
    assert float(port.expected_value) == 0.628
    ave = _one(rows, "ave_variance", {})
    assert float(ave.value) == (10.0 - 0.628) * 100
    contribs = [m for m in rows if m.metric_key == "deterioration_contribution"
                and m.value is not None]
    assert len(contribs) == 1  # single finest cell
    assert float(contribs[0].value) == pytest.approx(100.0, abs=0.01)
    assert contribs[0].inputs["prior_ref"] is not None
    # rerun is idempotent: same row count, no duplicates
    ctx = WorkflowContext(session=db_session, workflow_id=wf.id, workflow=wf,
                          storage=storage)
    again = await run_agent(ctx, "analysis", "analysis", run_analysis)
    assert again.status == "PASS"
    assert len(_metrics(db_session, wf.id)) == len(rows)


@pytest.mark.asyncio
async def test_small_sample_flagged(db_session):
    wf = _wf(db_session)
    storage = MemoryStorage()
    _add_refs(db_session)
    await _make_prior(db_session, storage, PRIOR_PREMIUM, PRIOR_CLAIMS)
    result = await _prep_and_analyze(db_session, wf, storage, CLAIMS)
    assert result.status == "PASS"
    rows = _metrics(db_session, wf.id)
    cell = _one(rows, "loss_ratio",
                {"product": "Commercial", "segment": "Construction",
                 "region": "South"})
    # 20 claims is not < 20, but 20 policies... exactly 20 -> not small either
    assert cell.flags["small_sample"] is False
    assert cell.flags["claim_count"] == 20


@pytest.mark.asyncio
async def test_analysis_api_end_to_end(db_session, monkeypatch):
    # freeze the deterministic prefix: this flow stops at ANALYZED, before
    # the LLM investigation stages (covered by the sample-run test)
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [s for s in engine.STAGE_ORDER
                         if s.stage in ("intake", "data_prep", "validation",
                                        "analysis")])
    _add_refs(db_session)
    db_session.add(ReferenceValue(period="2026-04",
                                  metric_key="historical_loss_ratio",
                                  dimensions={}, value=0.615,
                                  source="system_of_record"))
    db_session.add(ReferenceValue(period="2026-08",
                                  metric_key="historical_loss_ratio",
                                  dimensions={}, value=0.631,
                                  source="system_of_record"))
    db_session.commit()
    with TestClient(app) as client:
        wid = client.post(
            "/workflows", json={"reporting_period": "2026-09"}).json()["id"]
        r = client.post(f"/workflows/{wid}/upload", files=[
            ("files", ("claims_2026_09.csv", io.BytesIO(CLAIMS.encode()),
                       "text/csv")),
            ("files", ("premium_2026_09.csv", io.BytesIO(PREMIUM.encode()),
                       "text/csv")),
            ("files", ("exposure_2026_09.csv", io.BytesIO(EXPOSURE.encode()),
                       "text/csv")),
        ])
        assert r.json()["auto_started"] is True
        await engine.run_workflow(wid)
        st = client.get(f"/workflows/{wid}/status").json()
        # tiny fixture vs system-of-record totals -> CP-2, accept, continue
        assert st["status"] == "BLOCKED"
        cp = next(c for c in st["pending_checkpoints"]
                  if c["type"] == "validation_blocker")
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cp["id"], "decision": "accept_exception",
            "rationale": "Test fixture totals are synthetic and tiny; "
                         "proceed to analysis."})
        assert r.status_code == 202
        st = await _wait_for_status(client, wid, "ANALYZED")
        states = {s["stage"]: s["state"] for s in st["stage_statuses"]}
        assert states["analysis"] == "succeeded"

        body = client.get(f"/workflows/{wid}/metrics").json()
        assert body["undefined"]  # ave + contribution unavailable, no prior
        port = next(m for m in body["metrics"]
                    if m["metric_key"] == "loss_ratio" and m["dimensions"] == {})
        assert port["value"] == 10.0
        assert port["prev_value"] is None

        filt = client.get(f"/workflows/{wid}/metrics",
                          params={"metric_key": "loss_ratio",
                                  "group_by": "product,segment"}).json()
        assert filt["metrics"] and all(
            set(m["dimensions"]) == {"product", "segment"}
            for m in filt["metrics"])
        port_only = client.get(f"/workflows/{wid}/metrics",
                               params={"group_by": "portfolio"}).json()
        assert all(m["dimensions"] == {} for m in port_only["metrics"])
        bad = client.get(f"/workflows/{wid}/metrics",
                         params={"group_by": "planet"})
        assert bad.status_code == 400
        missing = client.get("/workflows/00000000-0000-0000-0000-000000000000"
                             "/metrics")
        assert missing.status_code == 404

        series = client.get(f"/workflows/{wid}/metrics",
                            params={"series": "loss_ratio"}).json()
        assert [p["period"] for p in series["series"]] == [
            "2026-04", "2026-08", "2026-09"]
        assert series["series"][-1] == {"period": "2026-09", "value": 10.0,
                                        "source": "computed"}
        assert series["series"][0]["source"] == "system_of_record"
        bad_series = client.get(f"/workflows/{wid}/metrics",
                                params={"series": "shenanigans"})
        assert bad_series.status_code == 400

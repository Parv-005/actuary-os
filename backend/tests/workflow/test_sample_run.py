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
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.agents.insight as insight_mod
import app.agents.knowledge as knowledge_mod
from app.llm.client import FakeLLMClient
from app.main import app
from app.models import (
    AgentRun,
    DatasetVersion,
    Finding,
    KnowledgeDocument,
    Metric,
    ReferenceValue,
    User,
    Workflow,
)
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
async def test_sample_data_through_intake_and_prep(db_session, monkeypatch):
    # seed the shared Storage singleton demo/ exactly like scripts/seed.py
    storage = get_storage()
    for name in ("claims_2026_09.csv", "claims_2026_09_v2.csv",
                 "premium_2026_09.csv", "exposure_2026_09.csv"):
        await storage.upload_bytes(f"demo/{name}", (SAMPLE / name).read_bytes())
    # system-of-record totals + expected LR + severity-trend assumption +
    # history series (migration 002 seeds; conftest truncates statics)
    db_session.add_all([
        ReferenceValue(period="2026-09", metric_key="recon_premium_total",
                       dimensions={}, value=120_000_000,
                       source="system_of_record"),
        ReferenceValue(period="2026-09", metric_key="recon_claims_total",
                       dimensions={}, value=80_000_000,
                       source="system_of_record"),
        ReferenceValue(period="2026-09", metric_key="expected_loss_ratio",
                       dimensions={}, value=0.628, source="methodology"),
        ReferenceValue(period="2026-09", metric_key="expected_severity_trend",
                       dimensions={"product": "Commercial",
                                   "segment": "Construction"},
                       value=0.050, source="methodology"),
    ] + [ReferenceValue(period=p, metric_key="historical_loss_ratio",
                        dimensions={}, value=v, source="system_of_record")
         for p, v in HISTORY_LR])
    # knowledge base (mirrors migration 002 + seed.py)
    v30 = KnowledgeDocument(
        title="Reserve Methodology", doc_type="methodology", version="v3.0",
        effective_date=date(2025, 1, 15),
        content_text="Reserve methodology v3.0. Expected severity trend for "
                     "Commercial Construction: +4.0% YoY. Superseded by v3.1.",
        tags=["methodology", "reserving", "construction"])
    v31 = KnowledgeDocument(
        title="Reserve Methodology", doc_type="methodology", version="v3.1",
        effective_date=date(2026, 4, 1),
        content_text="Reserve methodology v3.1 (current). Expected severity "
                     "trend for Commercial Construction: +5.0% YoY. "
                     "Assumption variance gate: observed vs configured >= 5pp "
                     "requires actuary review. AI does not recommend "
                     "assumption changes.",
        tags=["methodology", "reserving", "construction", "assumptions"])
    db_session.add_all([v30, v31])
    db_session.flush()
    v30.superseded_by = v31.id
    db_session.add(KnowledgeDocument(
        title="Monthly Review 2026-08", doc_type="prior_report",
        version="1.0", effective_date=date(2026, 8, 31),
        content_text="Monthly Portfolio Review — August 2026 (Meridian "
                     "General Insurance). Portfolio loss ratio 63.1% vs "
                     "expected 62.8% (+0.3pp). Commercial Construction "
                     "(South) severity rising faster than the +5.0% "
                     "methodology trend. Decision: monitor construction "
                     "severity next periods (recorded by Demo Actuary). "
                     "No assumption change.",
        tags=["prior_report", "construction", "monitoring"]))
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
    # seeded August monitoring finding for the knowledge prior-link
    aug_finding = Finding(
        workflow_id=prior.id, agent="insight", agent_version="seed-v1",
        model="scripted",
        title="Construction severity trending up — monitor into September",
        narrative="Evidence: Construction (South) severity rising faster "
                  "than trend.\nConclusion: watch item for September.",
        severity="medium", confidence=0.65, status="monitoring")
    db_session.add(aug_finding)
    db_session.commit()

    with TestClient(app) as client:
        r = client.post("/workflows", json={"reporting_period": "2026-09", "demo": True})
        wid = r.json()["id"]

        # Patch LLM factories BEFORE any engine activity can reach
        # investigation: the CP-2 accept below launches a background task
        # that may run insight+knowledge on its own thread. Scripts are
        # built at call time (metrics are committed by then); tool calls
        # execute genuinely against the real metrics in every racer.
        cs_dims = {"product": "Commercial", "segment": "Construction",
                   "region": "South"}

        def _insight_factory():
            rows = db_session.execute(
                select(Metric).where(
                    Metric.workflow_id == uuid.UUID(wid))).scalars().all()
            by_key = {(m.metric_key,
                       tuple(sorted((m.dimensions or {}).items()))): str(m.id)
                      for m in rows}
            cs_t = tuple(sorted(cs_dims.items()))
            script = [
                {"tool_calls": [{"name": "portfolio_summary", "args": {}}]},
                {"tool_calls": [{"name": "top_contributors",
                                 "args": {"limit": 3}}]},
                {"tool_calls": [{"name": "severity_vs_frequency",
                                 "args": dict(cs_dims)}]},
                {"json": {
                    "finding": "Commercial Construction (South) is the "
                               "largest contributor to portfolio deterioration",
                    "severity": "high", "confidence": 0.9,
                    "evidence_ids": [
                        by_key[("loss_ratio", ())], by_key[("loss_ratio", cs_t)],
                        by_key[("claim_severity", cs_t)],
                        by_key[("claim_frequency", cs_t)],
                        by_key[("deterioration_contribution", cs_t)]],
                    "narrative": {
                        "evidence": "Segment LR 62.9%->78.1% (+15.2pp); "
                                    "portfolio 63.1%->67.3%; severity +13.1% "
                                    "vs frequency +2.4%; 60.8% of movement.",
                        "hypothesis": "Severity-driven deterioration "
                                      "coincides with large-claim activity "
                                      "in Construction South.",
                        "conclusion": "Largest contributor; concentrated in "
                                      "South region."},
                    "possible_drivers": ["large claims in Construction South",
                                         "regional concentration in South"],
                    "alternatives": ["large-loss volatility",
                                     "reporting delay"],
                    "correlation_caveat": "Coincides with severe weather "
                                          "period; causation not established.",
                    "human_review_required": False,
                    "decision_question": "Does this warrant assumption "
                                         "review, pricing review, or "
                                         "continued monitoring?",
                }},
            ]
            return FakeLLMClient(script=script)

        knowledge_script = [{"json": {
            "summary": "August set a monitor on construction severity; "
                       "methodology v3.1 holds the +5.0% trend.",
            "citations": [
                {"title": "Monthly Review 2026-08", "version": "1.0",
                 "effective_date": "2026-08-31"},
                {"title": "Reserve Methodology", "version": "v3.1",
                 "effective_date": "2026-04-01"}],
            "no_relevant_document": False,
        }}]
        monkeypatch.setattr(insight_mod, "get_llm_client", _insight_factory)
        monkeypatch.setattr(knowledge_mod, "get_llm_client",
                            lambda: FakeLLMClient(script=list(knowledge_script)))

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

        # run 3+4: analysis -> investigation (insight + knowledge run with
        # the scripted factories above, whichever racer holds the lease) ->
        # CP-4 parks the workflow for the actuary
        st4 = await _wait_for_status(client, wid, "WAITING_FOR_HUMAN")
        states4 = {s["stage"]: s["state"] for s in st4["stage_statuses"]}
        assert states4["analysis"] == "succeeded"
        assert states4["insight"] == "succeeded"
        assert states4["knowledge"] == "succeeded"

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

        # investigation ran with the scripted factories (tool calls genuine)
        st4 = await _wait_for_status(client, wid, "WAITING_FOR_HUMAN")

        # the §15 storyline finding, evidence-backed by real metric ids
        flist = client.get(f"/workflows/{wid}/findings").json()["findings"]
        assert len(flist) == 1
        assert flist[0]["severity"] == "high"
        assert flist[0]["confidence"] == pytest.approx(0.9)
        assert flist[0]["evidence_count"] == 8  # 5 metric + 3 knowledge
        assert flist[0]["human_review_required"] is False
        assert "Construction" in flist[0]["title"] and "South" in flist[0]["title"]
        fid = flist[0]["id"]

        detail = client.get(f"/workflows/{wid}/findings/{fid}").json()
        assert detail["finding"]["decision_question"].startswith(
            "Does this warrant")
        assert set(detail["finding"]["alternatives"]) == {
            "large-loss volatility", "reporting delay"}
        assert "causation not established" in \
            detail["finding"]["correlation_caveat"]
        metric_ev = [e for e in detail["evidence"] if e["type"] == "metric"]
        assert len(metric_ev) == 5
        chain = next(e["chain"] for e in metric_ev
                     if e["chain"]["metric"]["metric_key"] == "loss_ratio"
                     and e["chain"]["metric"]["dimensions"].get("region") == "South")
        assert chain["metric"]["value"] == pytest.approx(0.781, abs=0.001)
        assert chain["metric"]["formula"].startswith("loss_ratio =")
        assert chain["datasets"] and chain["files"]
        assert any("claims_2026_09_v2.csv" in f["filename"]
                   for f in chain["files"])
        know_ev = [e for e in detail["evidence"] if e["type"] == "knowledge"]
        assert any(e["document"]["title"] == "Monthly Review 2026-08"
                   for e in know_ev)
        assert any("repeat monitoring item" in e["description"]
                   for e in know_ev)
        assert any(p["title"].startswith("Construction severity trending")
                   for p in detail["prior_findings"])
        frow = db_session.execute(
            select(Finding).where(Finding.workflow_id == uuid.UUID(wid))
        ).scalars().all()
        assert len(frow) == 1 and str(aug_finding.id) in (frow[0].links or [])

        # CP-4 assumption alert: +13.1% observed vs +5.0% configured
        cp4 = next(c for c in st4["pending_checkpoints"]
                   if c["type"] == "assumption_variance")
        assert cp4["severity"] == "red" and cp4["blocking"] is True
        var = cp4["context"]["variances"][0]
        assert var["observed_delta_pct"] == pytest.approx(13.1, abs=0.05)
        assert var["expected_trend_pct"] == pytest.approx(5.0)
        assert var["variance_pp"] == pytest.approx(8.1, abs=0.05)
        assert cp4["context"]["methodology"]["version"] == "v3.1"
        assert "does NOT recommend an assumption change" in \
            cp4["context"]["disclaimer"]
        assert {o["decision"] for o in cp4["options"]} == {
            "no_change_required", "investigate_further", "review_assumption",
            "escalate"}

        # LLM usage accounted on the agent runs
        runs = {r.stage: r for r in db_session.execute(
            select(AgentRun).where(
                AgentRun.workflow_id == uuid.UUID(wid),
                AgentRun.stage.in_(("insight", "knowledge")))
        ).scalars().all()}
        assert runs["insight"].llm_calls >= 4  # 3 tool rounds + final
        assert runs["insight"].prompt_hash
        assert runs["knowledge"].llm_calls >= 1

        # CP-4 decision: record "No change required — monitor", continue
        r = client.post(f"/workflows/{wid}/decisions", json={
            "checkpoint_id": cp4["id"], "decision": "no_change_required",
            "rationale": "", "payload": {"comment": "Monitor one more period."}})
        assert r.status_code == 202
        assert r.json()["workflow_status"] == "REPORTING"
        st5 = client.get(f"/workflows/{wid}/status").json()
        assert st5["status"] == "REPORTING"
        assert not st5["pending_checkpoints"]

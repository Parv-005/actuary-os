"""Activity + demo router tests (§10): audit-log (limit/after/404), agent-runs,
demo instructions (static §25 script)."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models import AgentRun, Workflow
from app.services.audit import record_event


def _wf(db_session) -> Workflow:
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status="ANALYZED")
    db_session.add(wf)
    db_session.commit()
    return wf


def test_audit_log_limit_after_and_404(db_session):
    wf = _wf(db_session)
    for i in range(3):
        record_event(db_session, workflow_id=wf.id, actor_type="system",
                     actor="t", action=f"step_{i}", summary=f"s{i}")
    db_session.commit()
    with TestClient(app) as client:
        body = client.get(f"/workflows/{wf.id}/audit-log").json()
        assert {e["action"] for e in body["events"]} == {
            "step_0", "step_1", "step_2"}
        assert set(body["events"][0]) >= {
            "id", "ts", "actor_type", "actor", "action", "from_status",
            "to_status", "entity_type", "entity_id", "summary"}
        body = client.get(f"/workflows/{wf.id}/audit-log",
                          params={"limit": 2}).json()
        assert len(body["events"]) == 2
        # cursor pages in the endpoint's stable timeline order
        full = client.get(f"/workflows/{wf.id}/audit-log").json()["events"]
        cursor = full[1]["id"]
        body = client.get(f"/workflows/{wf.id}/audit-log",
                          params={"after": cursor}).json()
        assert [e["id"] for e in body["events"]] == [
            e["id"] for e in full[2:]]
        assert client.get(
            f"/workflows/{wf.id}/audit-log",
            params={"after": "not-a-time"}).status_code == 400
        assert client.get(
            "/workflows/00000000-0000-0000-0000-000000000000/"
            "audit-log").status_code == 404


def test_agent_runs_shape_and_404(db_session):
    wf = _wf(db_session)
    db_session.add(AgentRun(workflow_id=wf.id, agent="intake", stage="intake",
                            attempt=1, status="succeeded", llm_calls=0,
                            tokens_in=0, tokens_out=0, cost_usd=0))
    db_session.commit()
    with TestClient(app) as client:
        body = client.get(f"/workflows/{wf.id}/agent-runs").json()
        assert len(body["runs"]) == 1
        run = body["runs"][0]
        assert (run["agent"], run["stage"], run["status"]) == (
            "intake", "intake", "succeeded")
        assert set(run) >= {"attempt", "duration_ms", "llm_calls",
                            "tokens_in", "tokens_out", "cost_usd", "error",
                            "started_at", "finished_at"}
        assert run["cost_usd"] == 0.0
        assert client.get(
            "/workflows/00000000-0000-0000-0000-000000000000/"
            "agent-runs").status_code == 404


def test_demo_instructions_static():
    with TestClient(app) as client:
        body = client.get("/demo/instructions").json()
        assert body["period"] == "2026-09"
        assert len(body["files"]) == 4
        assert {f["filename"] for f in body["files"]} == {
            "claims_2026_09.csv", "claims_2026_09_v2.csv",
            "premium_2026_09.csv", "exposure_2026_09.csv"}
        assert [s["n"] for s in body["steps"]] == [1, 2, 3, 4, 5, 6, 7, 8]

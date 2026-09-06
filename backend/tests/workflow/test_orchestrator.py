"""Orchestrator tests (§6.1/§7.2/§13.8/§17): retries, loop/stuck guards,
gate checks, lease double-run prevention, kill-and-resume.

Deterministic stub stages stand in for real agents via monkeypatched
engine.STAGE_ORDER; speed via monkeypatched BACKOFF_S (dev backoff is
5s/15s/45s).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agents.base import StageResult
from app.models import AgentRun, HumanCheckpoint, Metric, Workflow
from app.orchestrator import engine
from app.orchestrator.stages import StageSpec


def _wf(db_session, status="ANALYZING") -> Workflow:
    wf = Workflow(human_ref=f"MPR-2026-09-{uuid.uuid4().hex[:6].upper()}",
                  reporting_period="2026-09", status=status)
    db_session.add(wf)
    db_session.commit()
    return wf


def _stub(stage="probe", agent="probe", enters="ANALYZING",
          on_pass="ANALYZED", fn=None, timeout_s=5.0) -> StageSpec:
    async def _ok(ctx):
        return StageResult(status="PASS", outputs={"ok": True})

    return StageSpec(stage=stage, agent=agent, enters=enters,
                     on_pass=on_pass, run=fn or _ok, timeout_s=timeout_s)


def _seed_failed_runs(db_session, wf, stage="probe", agent="probe", n=3,
                      minutes_ago=1):
    for i in range(1, n + 1):
        db_session.add(AgentRun(
            workflow_id=wf.id, agent=agent, stage=stage, attempt=i,
            status="failed", error="boom",
            started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))
    db_session.commit()


def _pending_blocker(db_session, wf) -> None:
    db_session.add(HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type="validation_blocker",
        severity="red", blocking=True, title="synthetic blocker",
        context={}, options=[]))
    db_session.commit()


@pytest.mark.asyncio
async def test_retry_counts_then_failed(db_session, monkeypatch):
    monkeypatch.setattr(engine, "BACKOFF_S", [0.01, 0.01, 0.01])
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [_stub(fn=_always_fail())])
    wf = _wf(db_session)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "FAILED"
    assert wf.error["stage"] == "probe"
    rows = db_session.query(AgentRun).all()
    assert len(rows) == 3 and all(r.status == "failed" for r in rows)


def _always_fail():
    async def _fail(ctx):
        return StageResult(status="FAILED", error="boom")

    return _fail


@pytest.mark.asyncio
async def test_transient_failure_recovers(db_session, monkeypatch):
    monkeypatch.setattr(engine, "BACKOFF_S", [0.01, 0.01, 0.01])
    calls = []

    async def _flaky(ctx):
        calls.append(1)
        if len(calls) < 3:
            return StageResult(status="FAILED", error="transient")
        return StageResult(status="PASS")

    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub(fn=_flaky)])
    wf = _wf(db_session)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"
    rows = db_session.query(AgentRun).order_by(AgentRun.attempt).all()
    assert [r.status for r in rows] == ["failed", "failed", "succeeded"]


@pytest.mark.asyncio
async def test_loop_guard_halts(db_session, monkeypatch):
    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub()])
    wf = _wf(db_session)
    _seed_failed_runs(db_session, wf)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "FAILED"
    assert "loop_detected" in wf.error["message"]
    assert db_session.query(AgentRun).count() == 3  # stage never re-ran


@pytest.mark.asyncio
async def test_loop_guard_allows_retry_with_new_artifacts(db_session,
                                                          monkeypatch):
    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub()])
    wf = _wf(db_session)
    _seed_failed_runs(db_session, wf, minutes_ago=5)
    db_session.add(Metric(workflow_id=wf.id, metric_key="loss_ratio",
                          period="2026-09", value=0.6))
    db_session.commit()
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"  # fresh output = progress: retry allowed
    assert db_session.query(AgentRun).count() == 4


@pytest.mark.asyncio
async def test_manual_retry_bypasses_loop_guard(db_session, monkeypatch):
    monkeypatch.setattr(engine, "BACKOFF_S", [0.01, 0.01, 0.01])
    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub()])
    wf = _wf(db_session, status="RETRYING")
    _seed_failed_runs(db_session, wf)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"  # explicit human retry is honored


@pytest.mark.asyncio
async def test_gate_blocks_downstream(db_session, monkeypatch):
    from app.models import AuditEvent

    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [_stub(stage="analysis", agent="analysis",
                               enters="ANALYZING", on_pass="ANALYZED")])
    wf = _wf(db_session, status="INGESTING")
    _pending_blocker(db_session, wf)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "BLOCKED"
    assert db_session.query(AgentRun).count() == 0  # analysis never ran
    assert db_session.query(AuditEvent).filter(
        AuditEvent.action == "gate_blocked").count() == 1


@pytest.mark.asyncio
async def test_gate_resting_state_breaks_without_stranding(db_session,
                                                           monkeypatch):
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [_stub(stage="analysis", agent="analysis",
                               enters="ANALYZING", on_pass="ANALYZED")])
    wf = _wf(db_session, status="VALIDATED")
    _pending_blocker(db_session, wf)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    # VALIDATED has no legal path to BLOCKED: hold position, run nothing
    assert wf.status == "VALIDATED"
    assert db_session.query(AgentRun).count() == 0


@pytest.mark.asyncio
async def test_lease_prevents_double_run(db_session, monkeypatch):
    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub()])
    wf = _wf(db_session)
    assert engine.acquire_lease(db_session, wf, ttl_s=300) is True
    await engine.run_workflow(wf.id)  # lease held -> immediate return
    assert db_session.query(AgentRun).count() == 0
    engine.release_lease(db_session, wf)
    await engine.run_workflow(wf.id)  # lease free -> stage runs once
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"
    assert db_session.query(AgentRun).count() == 1
    assert wf.locked_until is None  # lease released after run


@pytest.mark.asyncio
async def test_concurrent_runs_single_execution(db_session, monkeypatch):
    async def _slow(ctx):
        await asyncio.sleep(0.3)
        return StageResult(status="PASS")

    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub(fn=_slow)])
    wf = _wf(db_session)
    await asyncio.gather(engine.run_workflow(wf.id),
                         engine.run_workflow(wf.id))
    rows = db_session.query(AgentRun).all()
    assert len(rows) == 1 and rows[0].status == "succeeded"
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"


@pytest.mark.asyncio
async def test_kill_and_resume_skips_completed(db_session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(engine, "launch", lambda wid: calls.append(wid))
    monkeypatch.setattr(engine, "STAGE_ORDER", [
        _stub(stage="aaa", agent="aaa", enters="ANALYZING", on_pass=None),
        _stub(stage="bbb", agent="bbb", enters="ANALYZING", on_pass="ANALYZED"),
    ])
    wf = _wf(db_session)
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"
    assert db_session.query(AgentRun).count() == 2
    # simulate crash: lease free, heartbeat 100s stale
    wf.locked_until = datetime.now(UTC) - timedelta(seconds=10)
    wf.updated_at = datetime.now(UTC) - timedelta(seconds=100)
    db_session.commit()
    n = await engine.resume_stale_workflows()
    assert n == 1 and calls == [wf.id]
    db_session.refresh(wf)
    assert wf.resume_count == 1
    await engine.run_workflow(wf.id)
    assert db_session.query(AgentRun).count() == 2  # nothing re-executed
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"


@pytest.mark.asyncio
async def test_stuck_guard(db_session, monkeypatch):
    monkeypatch.setattr(engine, "STAGE_ORDER", [_stub()])
    wf = _wf(db_session)
    wf.resume_count = 6
    db_session.commit()
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "FAILED"
    assert wf.error["message"] == "stuck — surfaced on dashboard"
    assert db_session.query(AgentRun).count() == 0


@pytest.mark.asyncio
async def test_stuck_guard_resting_state_not_stranded(db_session, monkeypatch):
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [_stub(stage="analysis", agent="analysis",
                               enters="ANALYZING", on_pass="ANALYZED")])
    wf = _wf(db_session, status="VALIDATED")
    wf.resume_count = 6
    db_session.commit()
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "ANALYZED"  # no legal FAILED edge: proceed instead


@pytest.mark.asyncio
async def test_end_of_run_parks_for_blocking_checkpoint(db_session,
                                                        monkeypatch):
    # a late-raised decision gate (the CP-4 shape) flows through the stage,
    # then parks the workflow for the actuary instead of resting
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [_stub(stage="knowledge", agent="knowledge",
                               enters="INVESTIGATING",
                               on_pass="INSIGHTS_READY")])
    wf = _wf(db_session, status="INVESTIGATING")
    db_session.add(HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type="assumption_variance",
        severity="red", blocking=True, title="assumption variance",
        context={}, options=[]))
    db_session.commit()
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"

    wf2 = _wf(db_session, status="INVESTIGATING")
    await engine.run_workflow(wf2.id)
    db_session.refresh(wf2)
    assert wf2.status == "INSIGHTS_READY"  # no blocker: rest, don't park


@pytest.mark.asyncio
async def test_strict_gate_holds_reporting_for_cp4(db_session, monkeypatch):
    # reporting/qa gate on ANY unresolved blocker: a pending CP-4 holds the
    # workflow at INSIGHTS_READY (no legal BLOCKED edge — no stranding),
    # and the end-of-run check parks it for the actuary
    monkeypatch.setattr(engine, "STAGE_ORDER",
                        [_stub(stage="reporting", agent="reporting",
                               enters="REPORTING", on_pass="QA")])
    wf = _wf(db_session, status="INSIGHTS_READY")
    db_session.add(HumanCheckpoint(
        workflow_id=wf.id, checkpoint_type="assumption_variance",
        severity="red", blocking=True, title="assumption variance",
        context={}, options=[]))
    db_session.commit()
    await engine.run_workflow(wf.id)
    db_session.refresh(wf)
    assert wf.status == "WAITING_FOR_HUMAN"  # held, then parked
    assert db_session.query(AgentRun).filter(
        AgentRun.stage == "reporting").count() == 0  # never started


def test_launch_background_thread_runs_workflow(db_session, monkeypatch):
    # regression: on a live server sync endpoints have no running event
    # loop, so launch() silently skipped every run (workflows stranded
    # after decisions). With ENGINE_BACKGROUND=1 the run executes in a
    # daemon thread instead.
    import time

    from app.config import settings
    from app.orchestrator import engine as engine_mod

    monkeypatch.setattr(settings, "engine_background", True)
    monkeypatch.setattr(engine_mod, "BACKOFF_S", [0.01, 0.01, 0.01])
    wf = _wf(db_session, status="INGESTING")
    engine_mod.launch(wf.id)  # sync context: no running loop here
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        for t in list(engine_mod._threads):
            t.join(timeout=1.0)
        db_session.refresh(wf)
        if wf.status == "BLOCKED":
            break
    assert wf.status == "BLOCKED"  # intake: zero files -> CP-1 red
    assert db_session.query(HumanCheckpoint).filter(
        HumanCheckpoint.workflow_id == wf.id,
        HumanCheckpoint.checkpoint_type == "input_exception").count() == 1
    assert not engine_mod._threads


def test_launch_skipped_without_flag(db_session, monkeypatch):
    from app.config import settings
    from app.orchestrator import engine as engine_mod

    monkeypatch.setattr(settings, "engine_background", False)
    wf = _wf(db_session, status="INGESTING")
    engine_mod.launch(wf.id)  # sync context, flag off: inert (tests hermetic)
    assert not engine_mod._threads
    db_session.refresh(wf)
    assert wf.status == "INGESTING"

"""Orchestrator engine (§6.1/§7.2): run loop, lease+heartbeat, retries,
resume from last incomplete (stage, agent) pair."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.agents import base as agent_base
from app.agents.context import WorkflowContext
from app.config import settings
from app.models import AgentRun, Workflow
from app.orchestrator import states
from app.orchestrator.stages import STAGE_ORDER, StageSpec
from app.services.audit import record_event
from app.storage.supabase import get_storage
from app.utils.logging import json_log

LEASE_TTL_S = 120
BACKOFF_S = [5.0, 15.0, 45.0]  # §6.1 retry policy
MAX_ATTEMPTS = 3

_tasks: set[asyncio.Task] = set()


def acquire_lease(session, wf: Workflow, ttl_s: int = LEASE_TTL_S) -> bool:
    """Conditional UPDATE — duplicate execution is structurally impossible."""
    now = datetime.now(UTC)
    res = session.execute(
        update(Workflow)
        .where(
            Workflow.id == wf.id,
            (Workflow.locked_until.is_(None)) | (Workflow.locked_until < now),
        )
        .values(locked_until=now + timedelta(seconds=ttl_s), updated_at=now)
    )
    session.commit()
    return res.rowcount == 1


def renew_lease(session, wf: Workflow, ttl_s: int = LEASE_TTL_S) -> None:
    session.execute(
        update(Workflow)
        .where(Workflow.id == wf.id)
        .values(locked_until=datetime.now(UTC) + timedelta(seconds=ttl_s))
    )
    session.commit()


def release_lease(session, wf: Workflow) -> None:
    session.execute(
        update(Workflow).where(Workflow.id == wf.id).values(locked_until=None)
    )
    session.commit()


def _has_succeeded_run(session, workflow_id, spec: StageSpec) -> bool:
    row = session.execute(
        select(AgentRun).where(
            AgentRun.workflow_id == workflow_id,
            AgentRun.stage == spec.stage,
            AgentRun.agent == spec.agent,
            AgentRun.status == "succeeded",
        )
    ).scalar_one_or_none()
    return row is not None


async def _run_stage(session, wf: Workflow, spec: StageSpec) -> None:
    """Run one stage with retries (3 attempts, backoff), then FAILED."""
    ctx = WorkflowContext(session=session, workflow_id=wf.id, workflow=wf,
                          storage=get_storage(), config=wf.config or {})
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = await agent_base.run_agent(
            ctx, spec.stage, spec.agent,
            spec.run if spec.run else _unimplemented,  # type: ignore[arg-type]
            timeout_s=spec.timeout_s,
            heartbeat_interval_s=0.05 if settings.app_env == "test" else 30.0,
        )
        if result.status in ("PASS", "WARNING"):
            if spec.on_pass and wf.status != spec.on_pass:
                states.apply_transition(
                    session, wf, spec.on_pass, actor_type="agent",
                    actor=f"{spec.agent}_agent", reason=f"{spec.stage} complete",
                )
                session.commit()
            return
        if result.status == "BLOCKER":
            # Agents self-transition to BLOCKED when raising the checkpoint;
            # the engine only transitions if the agent did not already do so
            # (BLOCKED -> BLOCKED is illegal; previously crashed direct awaits
            # while background tasks swallowed it as an un-retrieved exception).
            if wf.status != states.BLOCKED:
                states.apply_transition(
                    session, wf, states.BLOCKED, actor_type="agent",
                    actor=f"{spec.agent}_agent",
                    reason=f"{spec.stage} raised a blocking checkpoint",
                )
                session.commit()
            return
        last_error = result.error or "unknown error"
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(0.01 if settings.app_env == "test" else BACKOFF_S[attempt - 1])
    wf.error = {"stage": spec.stage, "agent": spec.agent, "message": last_error[:1000]}
    states.apply_transition(session, wf, states.FAILED, actor_type="system",
                            actor="orchestrator",
                            reason=f"{spec.stage} failed after {MAX_ATTEMPTS} attempts")
    session.commit()


async def _unimplemented(ctx: WorkflowContext) -> agent_base.StageResult:  # pragma: no cover
    return agent_base.StageResult(status="FAILED", error="stage not implemented yet")


async def run_workflow(workflow_id) -> None:
    with _session() as session:
        wf = session.get(Workflow, workflow_id)
        if wf is None or wf.status not in states.RUNNABLE:
            return
        if not acquire_lease(session, wf):
            json_log("lease_busy", workflow_id=str(workflow_id))
            return
        try:
            for spec in STAGE_ORDER:
                if wf.status in states.PAUSED or wf.status in states.TERMINAL:
                    break
                if _has_succeeded_run(session, wf.id, spec):
                    # Resume: ensure the post-stage state is applied, then skip
                    if wf.status == spec.enters and spec.on_pass:
                        states.apply_transition(
                            session, wf, spec.on_pass, actor_type="system",
                            actor="orchestrator", reason=f"resume: {spec.stage} already done",
                        )
                        session.commit()
                    continue
                wf.stage = spec.stage
                session.commit()
                if wf.status != spec.enters or wf.status == "RETRYING":
                    states.apply_transition(session, wf, spec.enters,
                                            actor_type="system", actor="orchestrator",
                                            reason=f"entering {spec.stage}")
                    session.commit()
                await _run_stage(session, wf, spec)
        finally:
            release_lease(session, wf)
            session.commit()


def _session():
    from app.db import SessionLocal

    return SessionLocal()


def launch(workflow_id) -> None:
    """Fire-and-forget background run (endpoints). Restart-safe: the resume
    sweep re-launches on stale leases."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        json_log("launch_skipped", workflow_id=str(workflow_id), reason="no running loop")
        return
    task = loop.create_task(run_workflow(workflow_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def resume_stale_workflows() -> int:
    """Resume sweep (§7.2): stale = active ∧ lease free ∧ heartbeat >90s old."""
    now = datetime.now(UTC)
    n = 0
    with _session() as session:
        rows = session.execute(select(Workflow)).scalars().all()
        for wf in rows:
            if wf.status not in states.RUNNABLE:
                continue
            lease_free = wf.locked_until is None or wf.locked_until < now
            stale_heartbeat = (now - (wf.updated_at or wf.created_at)).total_seconds() > 90
            if lease_free and stale_heartbeat:
                n += 1
                record_event(session, workflow_id=wf.id, actor_type="system",
                             actor="orchestrator", action="resume_sweep_pickup",
                             summary="stale workflow picked up by resume sweep")
                session.commit()
                launch(wf.id)
    return n

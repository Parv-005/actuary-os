"""run_agent(): agent_runs lifecycle, heartbeat, lease renewal, timeout (§6)."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.agents.context import WorkflowContext
from app.models import AgentRun, Workflow
from app.utils.logging import json_log

HEARTBEAT_INTERVAL_S = 30.0
LEASE_TTL_S = 120


@dataclass
class StageResult:
    status: str = "PASS"  # PASS | WARNING | BLOCKER | FAILED
    outputs: dict = field(default_factory=dict)
    checkpoint_id: str | None = None
    error: str | None = None


def _count_attempts(session, workflow_id, stage: str, agent: str) -> int:
    rows = session.execute(
        select(AgentRun).where(
            AgentRun.workflow_id == workflow_id,
            AgentRun.stage == stage,
            AgentRun.agent == agent,
        )
    ).scalars().all()
    return len(rows)


async def run_agent(
    ctx: WorkflowContext,
    stage: str,
    agent: str,
    fn: Callable[[WorkflowContext], Awaitable[StageResult]],
    timeout_s: float = 120.0,
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
) -> StageResult:
    session = ctx.session
    attempt = _count_attempts(session, ctx.workflow_id, stage, agent) + 1
    run = AgentRun(workflow_id=ctx.workflow_id, agent=agent, stage=stage,
                   attempt=attempt, status="running")
    session.add(run)
    session.commit()
    started = time.monotonic()

    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_interval_s)
            except TimeoutError:
                pass
            if stop.is_set():
                break
            session.execute(
                update(Workflow)
                .where(Workflow.id == ctx.workflow_id)
                .values(
                    updated_at=datetime.now(UTC),
                    locked_until=datetime.now(UTC) + timedelta(seconds=LEASE_TTL_S),
                )
            )
            session.execute(
                update(AgentRun)
                .where(AgentRun.id == run.id)
                .values(updated_at=datetime.now(UTC))
            )
            session.commit()

    hb = asyncio.create_task(heartbeat())
    result: StageResult
    try:
        result = await asyncio.wait_for(fn(ctx), timeout=timeout_s)
    except TimeoutError:
        result = StageResult(status="FAILED", error=f"{agent} timed out after {timeout_s}s")
    except Exception as e:  # noqa: BLE001 — any crash becomes a stage failure
        result = StageResult(status="FAILED", error=f"{agent}: {type(e).__name__}: {str(e)[:400]}")
    finally:
        stop.set()
        hb.cancel()

    run.status = "succeeded" if result.status in ("PASS", "WARNING", "BLOCKER") else (
        "timeout" if "timed out" in (result.error or "") else "failed"
    )
    run.error = result.error
    run.output_ref = result.outputs
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.finished_at = datetime.now(UTC)
    session.commit()
    json_log("agent_run", workflow_id=str(ctx.workflow_id), agent=agent,
             stage=stage, attempt=attempt, status=run.status,
             duration_ms=run.duration_ms, error=result.error)
    return result

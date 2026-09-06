"""Shared helpers for LLM-backed agents (insight, knowledge, reporting)."""
from __future__ import annotations

from sqlalchemy import select

from app.llm.client import LLMUsage
from app.models import AgentRun


def record_llm_usage(session, workflow_id, stage: str, agent: str,
                     usage: LLMUsage, prompt_hash: str | None = None) -> None:
    """Accumulate token/cost usage onto the currently-running agent run row.

    Safe to call when no running row exists (e.g., unit tests calling the
    agent function directly without run_agent): it becomes a no-op.
    """
    run = session.execute(
        select(AgentRun)
        .where(AgentRun.workflow_id == workflow_id,
               AgentRun.stage == stage, AgentRun.agent == agent,
               AgentRun.status == "running")
        .order_by(AgentRun.attempt.desc())
    ).scalars().first()
    if run is None:
        return
    run.llm_calls = (run.llm_calls or 0) + usage.calls
    run.tokens_in = (run.tokens_in or 0) + usage.tokens_in
    run.tokens_out = (run.tokens_out or 0) + usage.tokens_out
    run.cost_usd = float(run.cost_usd or 0) + usage.cost_usd
    if prompt_hash:
        run.prompt_hash = prompt_hash
    session.flush()

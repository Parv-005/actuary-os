"""Stage registry (§5): (stage, agent) -> agent fn + state wiring.

Resume granularity is the (stage, agent) pair (§7.2). Phases append here:
P7 data_prep, P8 validation, P9 analysis, P11 insight/knowledge,
P12 reporting/qa.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.agents.analysis import run_analysis
from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.agents.data_prep import run_data_prep
from app.agents.insight import run_insight
from app.agents.intake import run_intake
from app.agents.knowledge import run_knowledge
from app.agents.qa import run_qa
from app.agents.reporting import run_reporting
from app.agents.validation import run_validation


@dataclass(frozen=True)
class StageSpec:
    stage: str
    agent: str
    enters: str  # workflow state entered while this stage runs
    on_pass: str | None  # state advanced to on PASS/WARNING (None = stay)
    on_blocker: str | None = None  # state on BLOCKER (default BLOCKED via engine)
    run: Callable[[WorkflowContext], Awaitable[StageResult]] | None = None
    timeout_s: float = 120.0


STAGE_ORDER: list[StageSpec] = [
    StageSpec(stage="intake", agent="intake", enters="INGESTING", on_pass=None,
              run=run_intake),
    StageSpec(stage="data_prep", agent="data_prep", enters="INGESTING",
              on_pass="VALIDATING", run=run_data_prep),
    StageSpec(stage="validation", agent="validation", enters="VALIDATING",
              on_pass="VALIDATED", run=run_validation),
    StageSpec(stage="analysis", agent="analysis", enters="ANALYZING",
              on_pass="ANALYZED", run=run_analysis),
    StageSpec(stage="insight", agent="insight", enters="INVESTIGATING",
              on_pass=None, run=run_insight, timeout_s=180.0),
    StageSpec(stage="knowledge", agent="knowledge", enters="INVESTIGATING",
              on_pass="INSIGHTS_READY", run=run_knowledge, timeout_s=180.0),
    StageSpec(stage="reporting", agent="reporting", enters="REPORTING",
              on_pass="QA", run=run_reporting, timeout_s=180.0),
    StageSpec(stage="qa", agent="qa", enters="QA",
              on_pass="WAITING_FOR_HUMAN", run=run_qa),
]

STAGES: dict[str, StageSpec] = {s.stage: s for s in STAGE_ORDER}

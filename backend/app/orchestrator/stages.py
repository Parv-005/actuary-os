"""Stage registry (§5): (stage, agent) -> agent fn + state wiring.

Resume granularity is the (stage, agent) pair (§7.2). Phases append here:
P7 data_prep, P8 validation, P9 analysis, P11 insight/knowledge,
P12 reporting/qa.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.agents.base import StageResult
from app.agents.context import WorkflowContext
from app.agents.data_prep import run_data_prep
from app.agents.intake import run_intake
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
]

STAGES: dict[str, StageSpec] = {s.stage: s for s in STAGE_ORDER}

"""WorkflowContext — everything an agent needs (§6 contract)."""
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import Workflow


@dataclass
class WorkflowContext:
    session: Session
    workflow_id: Any
    workflow: Workflow
    storage: Any
    config: dict = field(default_factory=dict)

    @property
    def period(self) -> str:
        return self.workflow.reporting_period

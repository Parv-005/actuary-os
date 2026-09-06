"""API request schemas (responses are plain JSON dicts per §10)."""
from pydantic import BaseModel, Field, field_validator

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class CreateWorkflow(BaseModel):
    reporting_period: str = Field(pattern=PERIOD_PATTERN)
    portfolio: str = "General Insurance"
    demo: bool = False


class DecisionRequest(BaseModel):
    checkpoint_id: str | None = None
    finding_id: str | None = None
    report_id: str | None = None
    decision: str
    rationale: str = ""
    payload: dict = {}

    @field_validator("decision")
    @classmethod
    def known_decision(cls, v: str) -> str:
        from app.services.checkpoints import DECISION_ENUMS

        if v not in DECISION_ENUMS:
            raise ValueError(f"unknown decision: {v}")
        return v

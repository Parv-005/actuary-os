"""Structured LLM outputs (§14) — pydantic-validated before anything hits the DB."""
from typing import Literal

from pydantic import BaseModel, Field


class FindingNarrative(BaseModel):
    evidence: str
    hypothesis: str
    conclusion: str


class InsightFindings(BaseModel):
    finding: str
    severity: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = []
    narrative: FindingNarrative
    possible_drivers: list[str] = []
    alternatives: list[str] = []
    correlation_caveat: str | None = None
    human_review_required: bool = False
    decision_question: str | None = None


class KnowledgeCitation(BaseModel):
    title: str
    version: str
    effective_date: str | None = None


class KnowledgeSummary(BaseModel):
    summary: str
    citations: list[KnowledgeCitation] = []
    no_relevant_document: bool = False


class ReportDraft(BaseModel):
    executive_summary: str
    open_questions: list[str] = []

"""SQLAlchemy models — one class per §8 table (consolidated in this module for P0)."""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[uuid.UUID] = _pk()
    human_ref: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    workflow_type: Mapped[str] = mapped_column(Text, server_default="monthly_portfolio_review")
    portfolio: Mapped[str] = mapped_column(Text, server_default="General Insurance")
    reporting_period: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="CREATED")
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resume_count: Mapped[int] = mapped_column(Integer, server_default="0")
    supersedes_workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class File(Base):
    __tablename__ = "files"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, server_default="unknown")
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    checksum: Mapped[str] = mapped_column(Text, server_default="")
    mime: Mapped[str] = mapped_column(Text, server_default="text/csv")
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(Text, server_default="primary")
    period_inferred: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = _now()


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_ids: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), server_default="{}")
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, server_default="0")
    column_map: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    transform_log: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    checksum: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = _now()


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    check_id: Mapped[str] = mapped_column(Text, nullable=False)
    check_name: Mapped[str] = mapped_column(Text, server_default="")
    category: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, server_default="")
    details: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    affected_row_count: Mapped[int] = mapped_column(Integer, server_default="0")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Metric(Base):
    __tablename__ = "metrics"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    period: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    prev_value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    delta_pp: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str] = mapped_column(Text, server_default="ratio")
    undefined_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    formula: Mapped[str] = mapped_column(Text, server_default="")
    inputs: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    dataset_version_ids: Mapped[list] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}"
    )
    module_version: Mapped[str] = mapped_column(Text, server_default="m1")
    computed_at: Mapped[datetime] = _now()


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    agent: Mapped[str] = mapped_column(Text, server_default="insight")
    agent_version: Mapped[str] = mapped_column(Text, server_default="v1")
    model: Mapped[str] = mapped_column(Text, server_default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, server_default="")
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), server_default="0.5")
    possible_drivers: Mapped[list] = mapped_column(JSONB, server_default="[]")
    alternatives: Mapped[list] = mapped_column(JSONB, server_default="[]")
    correlation_caveat: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_review_required: Mapped[bool] = mapped_column(Boolean, server_default="false")
    decision_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    links: Mapped[list] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=True
    )
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, server_default="")
    snapshot: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    description: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = _now()


class HumanCheckpoint(Base):
    __tablename__ = "human_checkpoints"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    checkpoint_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    blocking: Mapped[bool] = mapped_column(Boolean, server_default="true")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    options: Mapped[list] = mapped_column(JSONB, server_default="[]")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    raised_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class HumanDecision(Base):
    __tablename__ = "human_decisions"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_checkpoints.id"), nullable=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id"), nullable=True
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, server_default="")
    payload: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime] = _now()


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    sections: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    body_markdown: Mapped[str] = mapped_column(Text, server_default="")
    qa_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime] = _now()
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_ref: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    output_ref: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = _now()
    duration_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    llm_calls: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Numeric(8, 4), server_default="0")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE")
    )
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str] = mapped_column(Text, server_default="")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    summary: Mapped[str] = mapped_column(Text, server_default="")
    details: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = _now()


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, server_default="1.0")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_documents.id"), nullable=True
    )
    content_text: Mapped[str] = mapped_column(Text, server_default="")
    tags: Mapped[list] = mapped_column(ARRAY(Text), server_default="{}")
    created_at: Mapped[datetime] = _now()


class ReferenceValue(Base):
    __tablename__ = "reference_values"
    id: Mapped[uuid.UUID] = _pk()
    period: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    value: Mapped[float] = mapped_column(Numeric, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

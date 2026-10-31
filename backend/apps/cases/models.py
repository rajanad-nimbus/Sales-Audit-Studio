"""Persistence models owned by the case management feature."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class Exception(Base):
    """Detected anomaly or control failure"""
    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    exception_type: Mapped[str] = mapped_column(String(100), index=True)
    exception_family: Mapped[str] = mapped_column(String(100))
    source_system: Mapped[str] = mapped_column(String(50))
    detection_origin: Mapped[str] = mapped_column(String(50))
    detection_rule_id: Mapped[str] = mapped_column(String(100))
    exception_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    estimated_exposure: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[str] = mapped_column(String(20))
    close_impact: Mapped[str] = mapped_column(String(20))
    close_blocker_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="Open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Case(Base):
    """Groups related exceptions and orchestrates resolution"""
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="Open", index=True)
    case_type: Mapped[str] = mapped_column(String(50))
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    currency: Mapped[str | None] = mapped_column(String(3))
    amount_needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    total_exception_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    total_exposure: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    investigation_status: Mapped[str] = mapped_column(String(50), default="Pending")
    evidence_completeness: Mapped[int] = mapped_column(Integer, default=0)
    assigned_to: Mapped[str | None] = mapped_column(String(255), index=True)
    priority: Mapped[str] = mapped_column(String(20), default="Normal")
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_cases_status_exposure", "status", "total_exposure"),
        Index("ix_cases_status_sla", "status", "sla_due_at"),
        Index("ix_cases_type_store", "case_type", "store_id"),
    )


class EvidenceSnapshot(Base):
    """Evidence data collected during investigation"""
    __tablename__ = "evidence_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    exception_id: Mapped[str] = mapped_column(String(36), ForeignKey("exceptions.id"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(100))
    evidence_status: Mapped[str] = mapped_column(String(50))
    source_system: Mapped[str] = mapped_column(String(50))
    evidence_data: Mapped[dict] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    staleness_threshold: Mapped[int] = mapped_column(Integer, default=14400)  # 4 hours in seconds
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index(
        "ix_evidence_snapshots_case_retrieved_id", "case_id", "retrieved_at", "id"
    ),)


class CaseNote(Base):
    __tablename__ = "case_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    author: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceRequest(Base):
    __tablename__ = "evidence_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    requirement: Mapped[str] = mapped_column(String(100))
    requested_from: Mapped[str] = mapped_column(String(255))
    requested_by: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="Open")  # Open -> Fulfilled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)       # what the person supplied or confirmed
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # document, ticket or system reference
    fulfilled_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_financial_impact: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    override_requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    override_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    override_approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    override_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Finding(Base):
    """Supported conclusion backed by evidence"""
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    investigation_id: Mapped[str] = mapped_column(String(36), index=True)
    conclusion: Mapped[str] = mapped_column(Text)
    finding_type: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[str] = mapped_column(String(20))
    supporting_rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recommendation(Base):
    """Agent-proposed disposition with evidence basis"""
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    investigation_id: Mapped[str] = mapped_column(String(36), index=True)
    disposition_type: Mapped[str] = mapped_column(String(50))
    action_class: Mapped[str] = mapped_column(String(50))
    expected_workflow: Mapped[str] = mapped_column(Text)
    financial_impact: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    confidence: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(50), default="Proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_recommendations_case_created_id", "case_id", "created_at", "id"),
        Index(
            "uq_recommendations_one_proposed_per_case",
            "case_id",
            unique=True,
            postgresql_where=text("status = 'Proposed'"),
            sqlite_where=text("status = 'Proposed'"),
        ),
    )


class HumanDecision(Base):
    """Finance/IT authorization for a recommendation"""
    __tablename__ = "human_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), ForeignKey("recommendations.id"))
    decision_type: Mapped[str] = mapped_column(String(50))
    decision: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(255))
    override_reason: Mapped[str | None] = mapped_column(Text)
    authority_check: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workflow(Base):
    """Durable workflow orchestration for case resolution"""
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), ForeignKey("recommendations.id"))
    workflow_type: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(50), default="Pending")
    definition_version: Mapped[str] = mapped_column(String(100))
    current_step: Mapped[str] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ValidationObligation(Base):
    """Post-action validation requirement"""
    __tablename__ = "validation_obligations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflows.id"))
    expected_observation: Mapped[str] = mapped_column(Text)
    due_window_hours: Mapped[int] = mapped_column(Integer)
    verification_rule: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="Pending")
    verification_result: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyEvaluation(Base):
    """Deterministic policy gate result for a recommendation"""
    __tablename__ = "policy_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), ForeignKey("recommendations.id"))
    rule_version: Mapped[str] = mapped_column(String(50))
    outcome: Mapped[str] = mapped_column(String(50))
    reasons: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

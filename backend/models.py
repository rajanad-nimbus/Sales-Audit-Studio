from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    DateTime, ForeignKey, String, Text, Numeric, Integer, Boolean,
    LargeBinary, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceRecord(Base):
    """Preserve original payload before any transformation"""
    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(50), index=True)
    source_version: Mapped[str] = mapped_column(String(50))
    source_record_id: Mapped[str] = mapped_column(String(255), index=True)
    delivery_id: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), default="Received", index=True)
    quarantine_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CanonicalTransaction(Base):
    """Normalized business record with complete lineage"""
    __tablename__ = "canonical_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processing_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settlement_date: Mapped[str] = mapped_column(String(10))
    transaction_type: Mapped[str] = mapped_column(String(50), index=True)
    transaction_subtype: Mapped[str | None] = mapped_column(String(50))
    source_lineage: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    signed_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    quantity: Mapped[int | None] = mapped_column(Integer)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    register_id: Mapped[str | None] = mapped_column(String(50))
    channel_id: Mapped[str] = mapped_column(String(50))
    tender_type: Mapped[str | None] = mapped_column(String(50))
    payment_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    settlement_reference: Mapped[str | None] = mapped_column(String(255))
    reconciliation_status: Mapped[str] = mapped_column(String(50), default="Unmatched", index=True)
    source_record_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("source_records.id"), index=True)
    disposition: Mapped[str] = mapped_column(String(50), default="Open")
    # Returns point at the sale they reverse. The reference is what the feed sent; the id is set once that sale is found.
    original_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    original_transaction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("canonical_transactions.id"), index=True)
    return_reason: Mapped[str | None] = mapped_column(String(255))
    detail_status: Mapped[str] = mapped_column(String(20), default="No detail", index=True)   # No detail | Balanced | Unbalanced
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


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


class AuditEvent(Base):
    """Append-only audit trail of all actions"""
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(255))
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    action_description: Mapped[str] = mapped_column(Text)
    before_state: Mapped[dict | None] = mapped_column(JSON)
    after_state: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Populated by a database trigger (hash chain). Rows are append-only.
    seq: Mapped[int | None] = mapped_column(Integer, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))


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


class Connector(Base):
    """Integration connector health and automation mode"""
    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    state: Mapped[str] = mapped_column(String(50), default="Healthy")
    mode: Mapped[str] = mapped_column(String(50), default="Governed Automation")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceFeedProfile(Base):
    """Approved read-only source delivery contract and header mapping."""
    __tablename__ = "source_feed_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    source_system: Mapped[str] = mapped_column(String(32), index=True)
    schedule: Mapped[str | None] = mapped_column(String(128))
    column_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StoreDay(Base):
    """The controlled audit unit: one retail location and one business date."""
    __tablename__ = "store_days"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(32), default="Open", index=True)
    audit_version: Mapped[int] = mapped_column(Integer, default=0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(255))
    reopened_by: Mapped[str | None] = mapped_column(String(255))
    reopened_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("store_id", "business_date", name="uq_store_day"),)


class AuditTotal(Base):
    """Versioned declared/calculated total for a Store Day and balancing dimension."""
    __tablename__ = "audit_totals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_day_id: Mapped[str] = mapped_column(String(36), ForeignKey("store_days.id"), index=True)
    audit_version: Mapped[int] = mapped_column(Integer, index=True)
    total_name: Mapped[str] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(24), default="Store")
    dimension_key: Mapped[str | None] = mapped_column(String(100))
    calculated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    declared_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoreDayClosureRequest(Base):
    __tablename__ = "store_day_closure_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_day_id: Mapped[str] = mapped_column(String(36), ForeignKey("store_days.id"), index=True)
    requested_by: Mapped[str] = mapped_column(String(255))
    evidence_basis: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="Pending", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReconciliationPolicy(Base):
    """Fallback tolerance policy until an approved Ontology policy is available."""
    __tablename__ = "reconciliation_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    amount_tolerance: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    settlement_day_tolerance: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReconciliationMatch(Base):
    """A proposed or approved manual match; never alters source records."""
    __tablename__ = "reconciliation_matches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_ids: Mapped[list] = mapped_column(JSON)
    match_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(24), default="Proposed", index=True)
    rationale: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FoundationReference(Base):
    """Governed retail reference data used by audit controls and integrations."""
    __tablename__ = "foundation_references"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("category", "code", name="uq_foundation_reference_category_code"),)


class CashControl(Base):
    """Cash-office declaration for one store day, register/cashier and tender."""
    __tablename__ = "cash_controls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    register_id: Mapped[str | None] = mapped_column(String(50), index=True)
    cashier_id: Mapped[str | None] = mapped_column(String(100), index=True)
    tender_type: Mapped[str] = mapped_column(String(50), default="Cash")
    declared_cash: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    paid_out: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    safe_drop: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    status: Mapped[str] = mapped_column(String(24), default="Declared")
    declared_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CashDeposit(Base):
    """Banked cash deposit reconciled to declared cash controls."""
    __tablename__ = "cash_deposits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    deposit_reference: Mapped[str] = mapped_column(String(100), unique=True)
    bank_account: Mapped[str | None] = mapped_column(String(100))
    deposited_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    variance_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    status: Mapped[str] = mapped_column(String(24), default="Recorded")
    recorded_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TransactionAdjustment(Base):
    """Proposed correction to a source transaction, retained as a separate audit record."""
    __tablename__ = "transaction_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("canonical_transactions.id"), index=True)
    adjustment_type: Mapped[str] = mapped_column(String(50))
    proposed_values: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="Proposed", index=True)
    proposed_by: Mapped[str] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GLCrossReference(Base):
    __tablename__ = "gl_cross_references"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_type: Mapped[str] = mapped_column(String(50))
    tender_type: Mapped[str | None] = mapped_column(String(50))
    debit_account: Mapped[str] = mapped_column(String(100))
    credit_account: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("transaction_type", "tender_type", name="uq_gl_cross_reference"),)


class AuditTotalDefinition(Base):
    """Retailer-defined total evaluated against immutable canonical transactions."""
    __tablename__ = "audit_total_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    aggregation: Mapped[str] = mapped_column(String(16), default="Sum")
    source_system: Mapped[str | None] = mapped_column(String(50))
    transaction_types: Mapped[list] = mapped_column(JSON, default=list)
    tender_type: Mapped[str | None] = mapped_column(String(50))
    level: Mapped[str] = mapped_column(String(24), default="Store")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ConfiguredAuditRule(Base):
    """Safe, parameterized audit rule over a named calculated total."""
    __tablename__ = "configured_audit_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    total_name: Mapped[str] = mapped_column(String(150))
    threshold: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    severity: Mapped[str] = mapped_column(String(20), default="Medium")
    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), unique=True)
    retain_days: Mapped[int] = mapped_column(Integer)
    archive_before_purge: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RetentionRun(Base):
    """Immutable record of a retention archive or purge operation."""
    __tablename__ = "retention_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(50), index=True)
    retain_days: Mapped[int] = mapped_column(Integer)
    eligible_records: Mapped[int] = mapped_column(Integer, default=0)
    archived_records: Mapped[int] = mapped_column(Integer, default=0)
    purged_records: Mapped[int] = mapped_column(Integer, default=0)
    archive_location: Mapped[str | None] = mapped_column(Text)
    archive_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="Succeeded")
    error: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BatchRun(Base):
    """One execution of the daily transaction-log batch (ingest + reconcile)"""
    __tablename__ = "batch_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="Running")
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_counts: Mapped[dict | None] = mapped_column(JSON)
    transactions_loaded: Mapped[int] = mapped_column(Integer, default=0)
    matched_pairs: Mapped[int] = mapped_column(Integer, default=0)
    cases_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class ExportDestination(Base):
    """A downstream system that receives Nimbus outputs (ERP/GL, analytics, inventory)"""
    __tablename__ = "export_destinations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # webhook | file
    config: Mapped[dict] = mapped_column(JSON)
    format: Mapped[str] = mapped_column(String(10), default="json")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportBatch(Base):
    """One delivery to a destination, with acknowledgment tracking"""
    __tablename__ = "export_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("export_destinations.id"), index=True)
    dataset: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="Pending")  # Pending|Sent|Acknowledged|Failed
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    audit_head_hash: Mapped[str | None] = mapped_column(String(64))
    range_from: Mapped[int | None] = mapped_column(Integer)
    range_to: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_ref: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    backposted_count: Mapped[int] = mapped_column(Integer, default=0)  # records for an earlier or already-exported business day


class ExportItem(Base):
    """Records already delivered to a destination: guarantees no record is exported twice"""
    __tablename__ = "export_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("export_batches.id"), index=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("export_destinations.id"))
    dataset: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(36))
    object_version: Mapped[str] = mapped_column(String(40), default="v1")
    # Manifest of what each batch holds. A record is reserved for its batch the moment the batch is created, so it
    # can never be sent twice, and stays "Pending" until the batch is delivered.
    state: Mapped[str] = mapped_column(String(20), default="Pending")      # Pending | Delivered
    record_ref: Mapped[str | None] = mapped_column(String(120))              # case number or journal id
    store_id: Mapped[str | None] = mapped_column(String(50))
    business_date: Mapped[str | None] = mapped_column(String(10))
    backpost: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("destination_id", "dataset", "object_id", "object_version", name="uq_export_item"),)


class TransactionLine(Base):
    """One item sold or returned. Amounts are signed like the transaction (returns are negative)."""
    __tablename__ = "transaction_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("canonical_transactions.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    item_code: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    tax_code: Mapped[str | None] = mapped_column(String(30))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    return_reason: Mapped[str | None] = mapped_column(String(255))


class TransactionTax(Base):
    """Tax (VAT) summarized by code for a transaction."""
    __tablename__ = "transaction_taxes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("canonical_transactions.id"), index=True)
    tax_code: Mapped[str] = mapped_column(String(30))
    tax_name: Mapped[str] = mapped_column(String(100))
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)


class TransactionDiscount(Base):
    """A reduction applied to the sale: promotion, coupon, voucher or manual. line_no is null when it applies to the whole sale."""
    __tablename__ = "transaction_discounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("canonical_transactions.id"), index=True)
    line_no: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str | None] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))


class TransactionTender(Base):
    """How the sale was paid. Several tenders can split one transaction (for example card plus voucher)."""
    __tablename__ = "transaction_tenders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("canonical_transactions.id"), index=True)
    tender_type: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    reference: Mapped[str | None] = mapped_column(String(255))
    authorization: Mapped[str | None] = mapped_column(String(100))


class AgentSetting(Base):
    """Admin-controlled agent configuration. Overrides environment defaults; every change is audited."""
    __tablename__ = "agent_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


__all__ = [
    "ExportDestination", "ExportBatch", "ExportItem", "BatchRun", "PolicyEvaluation", "Connector",
    "Base", "SourceRecord", "CanonicalTransaction", "Exception",
    "Case", "EvidenceSnapshot", "Finding", "Recommendation",
    "HumanDecision", "Workflow", "ValidationObligation", "AuditEvent"
]

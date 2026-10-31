from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, String, Text, Numeric, Integer, Boolean,
    JSON, Index, UniqueConstraint, text
)
from sqlalchemy.orm import Mapped, mapped_column






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

    __table_args__ = (
        Index("ix_audit_events_case_seq", "case_id", "seq"),
        Index("ix_audit_events_object_seq", "object_type", "object_id", "seq"),
    )


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
    state: Mapped[str] = mapped_column(String(50), default="Unknown")
    mode: Mapped[str] = mapped_column(String(50), default="Not Configured")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))










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
    __table_args__ = (
        CheckConstraint(
            "status IN ('Proposed', 'Approved', 'Rejected')",
            name="ck_transaction_adjustments_status",
        ),
        Index("ix_transaction_adjustments_created_id", "created_at", "id"),
        Index("ix_transaction_adjustments_status_created_id", "status", "created_at", "id"),
    )


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
    __table_args__ = (
        UniqueConstraint("transaction_type", "tender_type", name="uq_gl_cross_reference"),
        Index(
            "uq_gl_cross_reference_default_by_type",
            "transaction_type",
            unique=True,
            postgresql_where=text("tender_type IS NULL"),
            sqlite_where=text("tender_type IS NULL"),
        ),
    )


class AuditTotalDefinition(Base):
    """Retailer-defined total evaluated against immutable canonical transactions."""
    __tablename__ = "audit_total_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(50), default="Miscellaneous", server_default="Miscellaneous")
    over_short_group: Mapped[str | None] = mapped_column(String(32))
    over_short_operator: Mapped[str | None] = mapped_column(String(8))
    calculation_basis: Mapped[str] = mapped_column(String(32), default="Raw", server_default="Raw")
    status: Mapped[str] = mapped_column(String(24), default="Worksheet", server_default="Worksheet")
    aggregation: Mapped[str] = mapped_column(String(16), default="Sum")
    measure: Mapped[str] = mapped_column(String(16), default="Amount", server_default="Amount")  # Amount (header amount) | Tax (tax recorded in the detail)
    source_system: Mapped[str | None] = mapped_column(String(50))
    transaction_types: Mapped[list] = mapped_column(JSON, default=list)
    tender_type: Mapped[str | None] = mapped_column(String(50))
    level: Mapped[str] = mapped_column(String(24), default="Store")
    corresponds_to_rtlog: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    system_calculated: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_with_wizard: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    required_by_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    display_group: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    realms: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    joins: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    parameters: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    rollups: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    restrictions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    combined_total_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    combined_operator: Mapped[str | None] = mapped_column(String(1))
    location_traits: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    usages: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    sql_statement: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_audit_total_definitions_name_revision", "name", "revision", unique=True),)


class SalesAuditErrorDefinition(Base):
    __tablename__ = "sales_audit_error_definitions"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    short_description: Mapped[str] = mapped_column(String(255))
    form: Mapped[str | None] = mapped_column(String(100))
    tab: Mapped[str | None] = mapped_column(String(100))
    recommended_solution: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class ConfiguredAuditRule(Base):
    """Safe, parameterized audit rule over a named calculated total."""
    __tablename__ = "configured_audit_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    total_name: Mapped[str] = mapped_column(String(150))
    threshold: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    threshold_currency: Mapped[str | None] = mapped_column(String(3).evaluates_none(), default="USD", server_default="USD")
    severity: Mapped[str] = mapped_column(String(20), default="Medium")
    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
















class AgentSetting(Base):
    """Admin-controlled agent configuration. Overrides environment defaults; every change is audited."""
    __tablename__ = "agent_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


__all__ = [
    "ExportDestination", "ExportBatch", "ExportItem", "TestSinkReceipt", "BatchRun",
    "RetentionPolicy", "RetentionRun", "PolicyEvaluation", "Connector",
    "Base", "SourceRecord", "CanonicalTransaction", "Exception", "AuditTotal", "StoreDay",
    "StoreDayClosureRequest", "ReconciliationMatch", "ReconciliationPolicy",
    "ShopifyReferenceStage", "ShopifyReferenceSyncState", "SourceFeedProfile", "SourceSetting",
    "Case", "EvidenceSnapshot", "Finding", "Recommendation",
    "HumanDecision", "Workflow", "ValidationObligation", "AuditEvent"
]


# Compatibility exports for existing integrations; feature code imports its own models.
from apps.store_days.models import AuditTotal, StoreDay, StoreDayClosureRequest  # noqa: E402, F401

from apps.exports.models import ExportBatch, ExportDestination, ExportItem, TestSinkReceipt  # noqa: E402, F401

from apps.cases.models import (  # noqa: E402, F401
    Case, EvidenceRequest, EvidenceSnapshot, Exception, CaseNote, Finding, Recommendation,
    HumanDecision, Workflow, ValidationObligation,
)

from apps.ingest.models import (  # noqa: E402, F401
    ReconciliationMatch, ReconciliationPolicy, ShopifyReferenceStage,
    ShopifyReferenceSyncState, SourceFeedProfile, SourceRecord, SourceSetting,
)

from apps.transactions.models import (  # noqa: E402, F401
    CanonicalTransaction, TransactionDiscount, TransactionLine, TransactionTax, TransactionTender,
)

from apps.operations.models import BatchRun, RetentionPolicy, RetentionRun  # noqa: E402, F401

from apps.cash.models import CashControl, CashDeposit  # noqa: E402, F401

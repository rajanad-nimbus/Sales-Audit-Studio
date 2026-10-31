from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import (
    DateTime, ForeignKey, String, Text, Numeric, Boolean,
    JSON, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column








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
    "AuditTotalDefinition", "SalesAuditErrorDefinition",
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

from apps.audit.models import AuditEvent  # noqa: E402, F401

from apps.gl.models import GLCrossReference  # noqa: E402, F401

from apps.corrections.models import TransactionAdjustment  # noqa: E402, F401

from apps.totals.models import AuditTotalDefinition, SalesAuditErrorDefinition  # noqa: E402, F401

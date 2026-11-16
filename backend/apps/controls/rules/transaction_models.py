"""Governed transaction field rules for the Sales Audit error workflow."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")  # matches migration 0105


class ConfiguredTransactionAuditRule(Base):
    """Approved, bounded conditions over canonical transaction attributes."""

    __tablename__ = "configured_transaction_audit_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    error_code: Mapped[str] = mapped_column(String(64))
    record_type: Mapped[str | None] = mapped_column(String(50))
    conditions: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, server_default="[]")
    match_mode: Mapped[str] = mapped_column(String(24), default="Match", server_default="Match")
    evaluation_scope: Mapped[str] = mapped_column(
        String(24), default="Transaction", server_default="Transaction"
    )
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    location_traits: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, server_default="[]")
    execution_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(24), default="Worksheet", server_default="Worksheet")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    submitted_by: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_configured_transaction_audit_rule_name"),
        Index(
            "ix_configured_transaction_audit_rules_status_order",
            "status",
            "enabled",
            "execution_order",
        ),
    )

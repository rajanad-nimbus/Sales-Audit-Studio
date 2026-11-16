"""Persistence model for configured Sales Audit rules."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column


class ConfiguredAuditRule(Base):
    """Safe, parameterized audit rule over a named calculated total."""

    __tablename__ = "configured_audit_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    total_name: Mapped[str] = mapped_column(String(150))
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    location_traits: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    execution_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    balance_level: Mapped[str] = mapped_column(String(24), default="Store", server_default="Store")
    comparison: Mapped[str] = mapped_column(String(16), default="ABS_GT", server_default="ABS_GT")
    threshold: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    threshold_currency: Mapped[str | None] = mapped_column(
        String(3).evaluates_none(), default="USD", server_default="USD"
    )
    severity: Mapped[str] = mapped_column(String(20), default="Medium")
    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="Active", server_default="Active")
    submitted_by: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

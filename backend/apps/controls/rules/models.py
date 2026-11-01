"""Persistence model for configured Sales Audit rules."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column


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

"""Persistence models owned by the store-day audit feature."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class StoreDay(Base):
    """The controlled audit unit: one retail location and one business date."""
    __tablename__ = "store_days"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(32), default="Open", index=True)
    audit_version: Mapped[int] = mapped_column(Integer, default=0)
    totals_stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
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
    definition_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("audit_total_definitions.id"))
    definition_revision: Mapped[int | None] = mapped_column(Integer)
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

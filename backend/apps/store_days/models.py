"""Persistence models owned by the store-day audit feature."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    __table_args__ = (UniqueConstraint("store_id", "business_date", name="uq_store_day"),)


class AuditTotal(Base):
    """Versioned declared/calculated total for a Store Day and balancing dimension."""

    __tablename__ = "audit_totals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_day_id: Mapped[str] = mapped_column(String(36), ForeignKey("store_days.id"), index=True)
    audit_version: Mapped[int] = mapped_column(Integer, index=True)
    total_name: Mapped[str] = mapped_column(String(100))
    definition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audit_total_definitions.id")
    )
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


class VarianceTolerance(Base):
    """How much variance a total may carry before it needs a reason, and before it needs a case.

    A rule applies to one total name, or every total when total_name is null, and to the
    stores with one location trait (the store group), or every store when the trait is null.
    """

    __tablename__ = "variance_tolerances"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    total_name: Mapped[str | None] = mapped_column(String(100))
    location_trait: Mapped[str | None] = mapped_column(String(100))
    # |variance| <= within_amount is within tolerance.
    within_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    # |variance| > case_amount needs a case or correction; in between needs a reason.
    case_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    __table_args__ = (
        CheckConstraint(
            "within_amount >= 0 AND case_amount >= within_amount",
            name="ck_variance_tolerances_amounts",
        ),
        Index("ix_variance_tolerances_scope", "total_name", "location_trait"),
    )


class VarianceExplanation(Base):
    """Why a store day's total carries a variance: a reason, and the case or correction behind it.

    It explains a variance by total, dimension and amount, so it still applies after a
    re-total that leaves that variance unchanged, and stops applying when the amount moves.
    """

    __tablename__ = "variance_explanations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_day_id: Mapped[str] = mapped_column(String(36), ForeignKey("store_days.id"), index=True)
    audit_total_id: Mapped[str] = mapped_column(String(36), ForeignKey("audit_totals.id"))
    audit_version: Mapped[int] = mapped_column(Integer)
    total_name: Mapped[str] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(24))
    dimension_key: Mapped[str | None] = mapped_column(String(100))
    variance_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    outcome: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    case_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cases.id"))
    correction_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("transaction_adjustments.id")
    )
    explained_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PeriodClose(Base):
    """Finance has closed the books through period_end; store days on or before it stay closed."""

    __tablename__ = "period_closes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    period_end: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(16), default="Closed", index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    closed_by: Mapped[str] = mapped_column(String(255))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reopened_by: Mapped[str | None] = mapped_column(String(255))
    reopened_reason: Mapped[str | None] = mapped_column(Text)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

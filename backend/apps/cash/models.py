"""Persistence models owned by cash-office controls."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import CheckConstraint, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_cash_controls_store_date_tender", "store_id", "business_date", "tender_type"),
    )


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

    __table_args__ = (Index("ix_cash_deposits_store_date", "store_id", "business_date"),)


class ACHAdjustment(Base):
    """Audited next-day ACH movement adjustment for a bank or store."""

    __tablename__ = "ach_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_type: Mapped[str] = mapped_column(String(10), index=True)
    store_id: Mapped[str | None] = mapped_column(String(50), index=True)
    bank_id: Mapped[str] = mapped_column(String(50), index=True)
    account_reference: Mapped[str | None] = mapped_column(String(100))
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    adjustment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    note: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(12), default="Active", server_default="Active", index=True
    )
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reversed_by: Mapped[str | None] = mapped_column(String(255))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ach_adjustments_bank_day", "bank_id", "business_date"),
        Index("ix_ach_adjustments_store_day", "store_id", "business_date"),
        CheckConstraint("record_type IN ('Bank', 'Store')", name="ck_ach_adjustment_type"),
        CheckConstraint("status IN ('Active', 'Reversed')", name="ck_ach_adjustment_status"),
        CheckConstraint(
            "(record_type = 'Bank' AND store_id IS NULL) OR (record_type = 'Store' AND store_id IS NOT NULL)",
            name="ck_ach_adjustment_scope",
        ),
    )

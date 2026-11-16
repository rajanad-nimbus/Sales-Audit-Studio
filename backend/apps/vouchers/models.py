"""Persistence for vouchers: stored value issued by a store, redeemed once, or escheated when unclaimed."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column


class Voucher(Base):
    """One voucher. Expired is derived from the expiry date, so only the final states are stored."""

    __tablename__ = "vouchers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    voucher_number: Mapped[str] = mapped_column(String(100), unique=True)
    issued_store_id: Mapped[str] = mapped_column(String(50), index=True)
    issued_on: Mapped[str] = mapped_column(String(10))
    expires_on: Mapped[str | None] = mapped_column(String(10), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    tender_type: Mapped[str] = mapped_column(String(100), default="Voucher", index=True)
    status: Mapped[str] = mapped_column(String(16), default="Issued", index=True)
    issued_by: Mapped[str] = mapped_column(String(255))
    redeemed_store_id: Mapped[str | None] = mapped_column(String(50))
    redeemed_on: Mapped[str | None] = mapped_column(String(10))
    redeemed_transaction_id: Mapped[str | None] = mapped_column(String(36))
    redeemed_by: Mapped[str | None] = mapped_column(String(255))
    escheated_on: Mapped[str | None] = mapped_column(String(10))
    escheated_by: Mapped[str | None] = mapped_column(String(255))
    escheat_note: Mapped[str | None] = mapped_column(Text)
    escheat_locations: Mapped[dict | None] = mapped_column(JSON)
    escheat_routing_snapshot: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_vouchers_amount_positive"),
        CheckConstraint("status IN ('Issued', 'Redeemed', 'Escheated')", name="ck_vouchers_status"),
        Index("ix_vouchers_status_expires", "status", "expires_on"),
        Index("ix_vouchers_tender_age", func.lower(tender_type), issued_on, id),
    )


class VoucherOption(Base):
    """Tender-specific escheat windows configured by the retailer."""

    __tablename__ = "voucher_options"
    tender_type: Mapped[str] = mapped_column(String(100), primary_key=True)
    escheat_days: Mapped[int] = mapped_column(Integer)
    purge_days: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "escheat_days BETWEEN 0 AND 36500", name="ck_voucher_options_escheat_days_nonnegative"
        ),
        CheckConstraint("length(trim(tender_type)) > 0", name="ck_voucher_options_tender_required"),
        CheckConstraint(
            "purge_days IS NULL OR purge_days BETWEEN 0 AND 36500",
            name="ck_voucher_options_purge_days",
        ),
        Index("uq_voucher_options_tender_lower", func.lower(tender_type), unique=True),
    )


class VoucherRoutingPolicy(Base):
    """One versioned retailer policy for Store, Issuer, Recipient and HQ routing."""

    __tablename__ = "voucher_routing_policy"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    hq_location: Mapped[dict | None] = mapped_column(JSON)
    income_partner: Mapped[str | None] = mapped_column(String(100))
    rules: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[str] = mapped_column(String(36))
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (CheckConstraint("id = 'default'", name="ck_voucher_routing_singleton"),)


class VoucherIdentity(Base):
    """Reserve issued numbers permanently and retain a small record after ledger purge."""

    __tablename__ = "voucher_identities"
    voucher_number: Mapped[str] = mapped_column(String(100), primary_key=True)
    voucher_id: Mapped[str] = mapped_column(String(36), unique=True)
    issued_store_id: Mapped[str] = mapped_column(String(50), index=True)
    tender_type: Mapped[str] = mapped_column(String(100))
    issued_on: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3))
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    final_status: Mapped[str | None] = mapped_column(String(16))
    finalized_on: Mapped[str | None] = mapped_column(String(10))
    retention_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("retention_runs.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_voucher_identity_amount_positive"),
        CheckConstraint(
            "(retired_at IS NULL AND final_status IS NULL AND finalized_on IS NULL AND retention_run_id IS NULL) OR "
            "(retired_at IS NOT NULL AND final_status IS NOT NULL AND final_status IN ('Redeemed', 'Escheated') "
            "AND finalized_on IS NOT NULL AND retention_run_id IS NOT NULL)",
            name="ck_voucher_identity_retirement",
        ),
    )

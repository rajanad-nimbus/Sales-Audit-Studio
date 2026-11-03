"""Persistence for vouchers: stored value issued by a store, redeemed once, or escheated when unclaimed."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import CheckConstraint, DateTime, Index, Numeric, String, Text
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
    status: Mapped[str] = mapped_column(String(16), default="Issued", index=True)
    issued_by: Mapped[str] = mapped_column(String(255))
    redeemed_store_id: Mapped[str | None] = mapped_column(String(50))
    redeemed_on: Mapped[str | None] = mapped_column(String(10))
    redeemed_transaction_id: Mapped[str | None] = mapped_column(String(36))
    redeemed_by: Mapped[str | None] = mapped_column(String(255))
    escheated_on: Mapped[str | None] = mapped_column(String(10))
    escheated_by: Mapped[str | None] = mapped_column(String(255))
    escheat_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_vouchers_amount_positive"),
        CheckConstraint("status IN ('Issued', 'Redeemed', 'Escheated')", name="ck_vouchers_status"),
        Index("ix_vouchers_status_expires", "status", "expires_on"),
    )

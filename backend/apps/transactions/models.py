"""Persistence models owned by canonical transaction processing."""

from datetime import datetime
from decimal import Decimal

from model_base import Base, utcnow
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column


class CanonicalTransaction(Base):
    """Normalized business record with complete lineage"""

    __tablename__ = "canonical_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processing_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settlement_date: Mapped[str] = mapped_column(String(10))
    transaction_type: Mapped[str] = mapped_column(String(50), index=True)
    transaction_subtype: Mapped[str | None] = mapped_column(String(50))
    source_lineage: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    signed_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    quantity: Mapped[int | None] = mapped_column(Integer)
    store_id: Mapped[str] = mapped_column(String(50), index=True)
    register_id: Mapped[str | None] = mapped_column(String(50))
    cashier_id: Mapped[str | None] = mapped_column(String(100))
    channel_id: Mapped[str] = mapped_column(String(50))
    tender_type: Mapped[str | None] = mapped_column(String(50), index=True)
    payment_reference: Mapped[str | None] = mapped_column(String(255), index=True)
    settlement_reference: Mapped[str | None] = mapped_column(String(255))
    reconciliation_status: Mapped[str] = mapped_column(String(50), default="Unmatched", index=True)
    source_record_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_records.id"), index=True
    )
    disposition: Mapped[str] = mapped_column(String(50), default="Open")
    # Returns point at the sale they reverse. The reference is what the feed sent; the id is set once that sale is found.
    original_reference: Mapped[str | None] = mapped_column(String(255))
    original_transaction_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("canonical_transactions.id")
    )
    return_reason: Mapped[str | None] = mapped_column(String(255))
    detail_status: Mapped[str] = mapped_column(
        String(20), default="No detail", server_default="No detail"
    )  # No detail | Balanced | Unbalanced
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_ct_store_business_date_type", "store_id", "business_date", "transaction_type"),
        Index("ix_ct_store_type_event_id", "store_id", "transaction_type", "event_timestamp", "id"),
        Index("ix_ct_original_reference", "original_reference"),
        Index("ix_ct_original_transaction_id", "original_transaction_id"),
        Index("ix_ct_detail_status", "detail_status"),
        Index(
            "ix_ct_pending_return_link",
            "store_id",
            "original_reference",
            "id",
            postgresql_where=text(
                "transaction_type = 'Return' AND original_transaction_id IS NULL"
            ),
            sqlite_where=text("transaction_type = 'Return' AND original_transaction_id IS NULL"),
        ),
    )


class TransactionLine(Base):
    """One item sold or returned. Amounts are signed like the transaction (returns are negative)."""

    __tablename__ = "transaction_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_transactions.id"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    item_code: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    tax_code: Mapped[str | None] = mapped_column(String(30))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0, server_default="0")
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0, server_default="0")
    return_reason: Mapped[str | None] = mapped_column(String(255))


class TransactionTax(Base):
    """Tax (VAT) summarized by code for a transaction."""

    __tablename__ = "transaction_taxes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_transactions.id"), index=True
    )
    tax_code: Mapped[str] = mapped_column(String(30))
    tax_name: Mapped[str] = mapped_column(String(100))
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=0, server_default="0")
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0, server_default="0")
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0, server_default="0")


class TransactionDiscount(Base):
    """A reduction applied to the sale: promotion, coupon, voucher or manual. line_no is null when it applies to the whole sale."""

    __tablename__ = "transaction_discounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_transactions.id"), index=True
    )
    line_no: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    code: Mapped[str | None] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))


class TransactionTender(Base):
    """How the sale was paid. Several tenders can split one transaction (for example card plus voucher)."""

    __tablename__ = "transaction_tenders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("canonical_transactions.id"), index=True
    )
    tender_type: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    reference: Mapped[str | None] = mapped_column(String(255))
    authorization: Mapped[str | None] = mapped_column(String(100))

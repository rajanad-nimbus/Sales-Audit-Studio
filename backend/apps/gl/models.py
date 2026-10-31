"""Persistence model for transaction-to-ledger account mappings."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class GLCrossReference(Base):
    __tablename__ = "gl_cross_references"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_type: Mapped[str] = mapped_column(String(50))
    tender_type: Mapped[str | None] = mapped_column(String(50))
    debit_account: Mapped[str] = mapped_column(String(100))
    credit_account: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("transaction_type", "tender_type", name="uq_gl_cross_reference"),
        Index(
            "uq_gl_cross_reference_default_by_type",
            "transaction_type",
            unique=True,
            postgresql_where=text("tender_type IS NULL"),
            sqlite_where=text("tender_type IS NULL"),
        ),
    )

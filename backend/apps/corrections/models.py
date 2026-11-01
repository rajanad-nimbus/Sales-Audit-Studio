"""Persistence model for approved transaction corrections."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class TransactionAdjustment(Base):
    """Proposed correction to a source transaction, retained as a separate audit record."""

    __tablename__ = "transaction_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("canonical_transactions.id"), index=True
    )
    adjustment_type: Mapped[str] = mapped_column(String(50))
    proposed_values: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="Proposed", index=True)
    proposed_by: Mapped[str] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint(
            "status IN ('Proposed', 'Approved', 'Rejected')",
            name="ck_transaction_adjustments_status",
        ),
        Index("ix_transaction_adjustments_created_id", "created_at", "id"),
        Index("ix_transaction_adjustments_status_created_id", "status", "created_at", "id"),
    )

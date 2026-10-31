"""Persistence model for governed retail foundation references."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String, UniqueConstraint

from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class FoundationReference(Base):
    """Governed retail reference data used by audit controls and integrations."""
    __tablename__ = "foundation_references"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("category", "code", name="uq_foundation_reference_category_code"),)

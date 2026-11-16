"""Persistence model for governed retail foundation references."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    __table_args__ = (
        UniqueConstraint("category", "code", name="uq_foundation_reference_category_code"),
        CheckConstraint(
            "length(trim(category)) > 0 AND length(trim(code)) > 0 AND length(trim(name)) > 0",
            name="ck_foundation_references_required_text",
        ),
    )


class StoreLocationTrait(Base):
    """Indexed live membership of a store in a reusable location trait."""

    __tablename__ = "store_location_traits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(100))
    location_trait: Mapped[str] = mapped_column(String(100))
    __table_args__ = (
        UniqueConstraint("store_id", "location_trait", name="uq_store_location_trait"),
        Index("ix_store_location_traits_trait", "location_trait", "store_id"),
    )


class FoundationImportRun(Base):
    """Durable process record for a reviewed foundation-data CSV upload."""

    __tablename__ = "foundation_import_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    process_name: Mapped[str] = mapped_column(String(100))
    file_name: Mapped[str] = mapped_column(String(255))
    categories: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    rows_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    errors: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_foundation_import_runs_started_at", "started_at"),)

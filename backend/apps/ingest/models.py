"""Persistence models owned by the source ingestion feature."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class SourceRecord(Base):
    """Preserve original payload before any transformation"""
    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(50), index=True)
    source_version: Mapped[str] = mapped_column(String(50))
    source_record_id: Mapped[str] = mapped_column(String(255), index=True)
    delivery_id: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), default="Received", index=True)
    quarantine_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        Index("ix_source_records_system_event_time", "source_system", "source_event_time"),
        Index("ix_source_records_system_status_id", "source_system", "status", "id"),
        Index("ix_source_records_system_record_id", "source_system", "source_record_id"),
        Index("ix_source_records_system_delivery", "source_system", "delivery_id"),
    )

class SourceSetting(Base):
    """Per-source switches: whether a source is accepted at all, joins the daily batch, and allows a manual sync or upload.
    A source with no row behaves as today: on, in the batch (Shopify only when its connector is configured), manual allowed."""
    __tablename__ = "source_settings"

    source_system: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    in_daily_batch: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    manual_sync: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Scheduled sync for sources Nimbus pulls itself: "daily" or "weekly" at sync_time (HH:MM) in sync_timezone,
    # on sync_weekday (0 = Monday) when weekly. None means not scheduled.
    sync_frequency: Mapped[str | None] = mapped_column(String(8))
    sync_time: Mapped[str | None] = mapped_column(String(5))
    sync_weekday: Mapped[int | None] = mapped_column(Integer)
    sync_timezone: Mapped[str | None] = mapped_column(String(64))
    last_scheduled_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scheduled_status: Mapped[str | None] = mapped_column(String(16))
    last_scheduled_message: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  server_default=text("now()"), onupdate=utcnow)

class SourceFeedProfile(Base):
    """Approved read-only source delivery contract and header mapping."""
    __tablename__ = "source_feed_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    source_system: Mapped[str] = mapped_column(String(32), index=True)
    schedule: Mapped[str | None] = mapped_column(String(128))
    column_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class ReconciliationPolicy(Base):
    """Fallback tolerance policy until an approved Ontology policy is available."""
    __tablename__ = "reconciliation_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    amount_tolerance: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    amount_tolerance_currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    settlement_day_tolerance: Mapped[int] = mapped_column(Integer, default=0)
    gap_min_count: Mapped[int] = mapped_column(Integer, default=10, server_default="10")  # a missing-transaction gap is "big" from this many...
    gap_share: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.1"), server_default="0.1")  # ...and this share of the declared count
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (Index(
        "uq_reconciliation_policies_one_active",
        "active",
        unique=True,
        postgresql_where=text("active IS TRUE"),
        sqlite_where=text("active = 1"),
    ),)

class ReconciliationMatch(Base):
    """A proposed or approved manual match; never alters source records."""
    __tablename__ = "reconciliation_matches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_ids: Mapped[list] = mapped_column(JSON)
    store_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    match_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(24), default="Proposed", index=True)
    rationale: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint(
        "status IN ('Proposed', 'Approved', 'Rejected')",
        name="ck_reconciliation_matches_status",
    ), Index("ix_reconciliation_matches_store_created_id", "store_id", "created_at", "id"))

class ShopifyReferenceStage(Base):
    """Durable, isolated snapshot rows awaiting an atomic Shopify reference sync."""
    __tablename__ = "shopify_reference_stage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sync_id: Mapped[str] = mapped_column(String(36), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(50))
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean)
    attributes: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("CURRENT_TIMESTAMP"))
    __table_args__ = (
        UniqueConstraint("sync_id", "category", "code", name="uq_shopify_reference_stage_sync_code"),
        Index("ix_shopify_reference_stage_sync_id_id", "sync_id", "id"),
        Index("ix_shopify_reference_stage_created_at", "created_at"),
    )

class ShopifyReferenceSyncState(Base):
    """Database-wide ordering for concurrent Shopify catalog snapshots."""
    __tablename__ = "shopify_reference_sync_state"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    applied_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

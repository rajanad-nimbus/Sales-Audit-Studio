"""Persistence models owned by the export delivery feature."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class ExportDestination(Base):
    """A downstream system that receives Nimbus outputs (ERP/GL, analytics, inventory)"""

    __tablename__ = "export_destinations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # webhook | file
    config: Mapped[dict] = mapped_column(JSON)
    format: Mapped[str] = mapped_column(String(10), default="json")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportBatch(Base):
    """One delivery to a destination, with acknowledgment tracking"""

    __tablename__ = "export_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    destination_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("export_destinations.id"), index=True
    )
    dataset: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(
        String(20), index=True, default="Pending"
    )  # Pending|Sent|Acknowledged|Failed
    delivery_attempt_id: Mapped[str | None] = mapped_column(String(36))
    attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    audit_head_hash: Mapped[str | None] = mapped_column(String(64))
    range_from: Mapped[int | None] = mapped_column(Integer)
    range_to: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_code: Mapped[int | None] = mapped_column(Integer)
    response_ref: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    backposted_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )  # records for an earlier or already-exported business day


class ExportItem(Base):
    """Records already delivered to a destination: guarantees no record is exported twice"""

    __tablename__ = "export_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("export_batches.id"), index=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("export_destinations.id"))
    dataset: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[str] = mapped_column(String(36))
    object_version: Mapped[str] = mapped_column(String(40), default="v1")
    # Manifest of what each batch holds. A record is reserved for its batch the moment the batch is created, so it
    # can never be sent twice, and stays "Pending" until the batch is delivered.
    state: Mapped[str] = mapped_column(
        String(20), default="Pending", server_default="Pending"
    )  # Pending | Delivered
    record_ref: Mapped[str | None] = mapped_column(String(120))  # case number or journal id
    store_id: Mapped[str | None] = mapped_column(String(50))
    business_date: Mapped[str | None] = mapped_column(String(10))
    backpost: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "destination_id", "dataset", "object_id", "object_version", name="uq_export_item"
        ),
    )


class TestSinkReceipt(Base):
    """Durable deduplication receipt for the built-in demonstration receiver."""

    __tablename__ = "test_sink_receipts"

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(30))
    payload_hash: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

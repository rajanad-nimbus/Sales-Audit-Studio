"""Persistence models owned by batch processing and data retention."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), unique=True)
    retain_days: Mapped[int] = mapped_column(Integer)
    archive_before_purge: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class RetentionRun(Base):
    """Immutable record of a retention archive or purge operation."""
    __tablename__ = "retention_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(50), index=True)
    retain_days: Mapped[int] = mapped_column(Integer)
    eligible_records: Mapped[int] = mapped_column(Integer, default=0)
    archived_records: Mapped[int] = mapped_column(Integer, default=0)
    purged_records: Mapped[int] = mapped_column(Integer, default=0)
    archive_location: Mapped[str | None] = mapped_column(Text)
    archive_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="Succeeded")
    error: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class BatchRun(Base):
    """One execution of the daily transaction-log batch (ingest + reconcile)"""
    __tablename__ = "batch_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="Running")
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_counts: Mapped[dict | None] = mapped_column(JSON)
    transactions_loaded: Mapped[int] = mapped_column(Integer, default=0)
    matched_pairs: Mapped[int] = mapped_column(Integer, default=0)
    cases_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "uq_batch_runs_one_running_per_date", "business_date", unique=True,
            postgresql_where=text("status = 'Running'"), sqlite_where=text("status = 'Running'"),
        ),
        Index("ix_batch_runs_status_finished_at", "status", "finished_at"),
    )

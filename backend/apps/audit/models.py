"""Persistence model for the append-only audit event stream."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class AuditEvent(Base):
    """Append-only audit trail of all actions"""
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(255))
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cases.id"), index=True)
    action_description: Mapped[str] = mapped_column(Text)
    before_state: Mapped[dict | None] = mapped_column(JSON)
    after_state: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Populated by a database trigger (hash chain). Rows are append-only.
    seq: Mapped[int | None] = mapped_column(Integer, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_audit_events_case_seq", "case_id", "seq"),
        Index("ix_audit_events_object_seq", "object_type", "object_id", "seq"),
    )

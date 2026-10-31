"""Persistence model for integration connector health."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text

from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base


class Connector(Base):
    """Integration connector health and automation mode"""
    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    state: Mapped[str] = mapped_column(String(50), default="Unknown")
    mode: Mapped[str] = mapped_column(String(50), default="Not Configured")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

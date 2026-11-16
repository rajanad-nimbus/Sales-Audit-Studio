"""Persistence model for integration connector health."""

from datetime import datetime

from model_base import Base
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class Connector(Base):
    """Integration connector health and automation mode"""

    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    state: Mapped[str] = mapped_column(String(50), default="Unknown")
    mode: Mapped[str] = mapped_column(String(50), default="Not Configured")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemOption(Base):
    """Audited retailer-wide setting for operational behavior."""

    __tablename__ = "system_options"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

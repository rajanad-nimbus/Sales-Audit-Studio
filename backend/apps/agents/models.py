"""Persistence model for admin-controlled agent settings."""

from datetime import datetime

from sqlalchemy import DateTime, JSON, String

from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class AgentSetting(Base):
    """Admin-controlled agent configuration. Overrides environment defaults; every change is audited."""
    __tablename__ = "agent_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

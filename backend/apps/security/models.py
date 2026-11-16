"""Persistence models for locally governed store access assignments."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


class StoreAssignment(Base):
    """Admin-managed store scope override for a verified identity."""

    __tablename__ = "store_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_identifier: Mapped[str] = mapped_column(String(255), unique=True)
    store_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    location_traits: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    updated_by: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerifiedStoreUser(Base):
    """Minimal directory cache of store-scoped identities verified by Ontology Studio."""

    __tablename__ = "verified_store_users"

    assignment_identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_identifier: Mapped[str] = mapped_column(String(255), index=True)
    identity_id: Mapped[str | None] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class UserPreference(Base):
    """Personal display preferences keyed to the verified Ontology identity."""

    __tablename__ = "user_preferences"

    user_identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    locale: Mapped[str] = mapped_column(String(20), default="en-US")
    time_zone: Mapped[str] = mapped_column(String(64), default="UTC")
    density: Mapped[str] = mapped_column(String(16), default="comfortable")
    contrast: Mapped[str] = mapped_column(String(16), default="standard")
    motion: Mapped[str] = mapped_column(String(16), default="system")
    theme: Mapped[str] = mapped_column(String(16), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSavedViews(Base):
    """Named worklist views owned by a verified user and a single workspace."""

    __tablename__ = "user_saved_views"

    user_identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    views: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

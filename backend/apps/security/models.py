"""Persistence models for locally governed role and store access assignments."""

from datetime import datetime

from model_base import Base, utcnow
from sqlalchemy import JSON, DateTime, String, Text, func
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


class UserRoleAssignment(Base):
    """The business role an administrator gives a verified identity.

    Ontology Studio owns who a person is and whether they are an administrator, but it can only express a
    coarse role. Nimbus owns which business role (Finance, IT, auditor, store manager...) that person holds
    here, keyed to Studio's stable identity so it survives renames. It never applies to Studio administrators.
    """

    __tablename__ = "user_role_assignments"

    assignment_identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    role: Mapped[str] = mapped_column(String(50))
    reason: Mapped[str] = mapped_column(Text)
    assigned_by: Mapped[str] = mapped_column(String(255))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class VerifiedStoreUser(Base):
    """Minimal directory cache of non-administrator identities verified by Ontology Studio."""

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
    locale: Mapped[str] = mapped_column(String(20), default="en-US", server_default="en-US")
    time_zone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    density: Mapped[str] = mapped_column(
        String(16), default="comfortable", server_default="comfortable"
    )
    contrast: Mapped[str] = mapped_column(String(16), default="standard", server_default="standard")
    motion: Mapped[str] = mapped_column(String(16), default="system", server_default="system")
    theme: Mapped[str] = mapped_column(String(16), default="system", server_default="system")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class UserSavedViews(Base):
    """Named worklist views owned by a verified user and a single workspace."""

    __tablename__ = "user_saved_views"

    user_identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    views: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

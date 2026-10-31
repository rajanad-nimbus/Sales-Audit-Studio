"""Persistence models for configured totals and sales-audit errors."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from model_base import Base, utcnow


class AuditTotalDefinition(Base):
    """Retailer-defined total evaluated against immutable canonical transactions."""
    __tablename__ = "audit_total_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(50), default="Miscellaneous", server_default="Miscellaneous")
    over_short_group: Mapped[str | None] = mapped_column(String(32))
    over_short_operator: Mapped[str | None] = mapped_column(String(8))
    calculation_basis: Mapped[str] = mapped_column(String(32), default="Raw", server_default="Raw")
    status: Mapped[str] = mapped_column(String(24), default="Worksheet", server_default="Worksheet")
    aggregation: Mapped[str] = mapped_column(String(16), default="Sum")
    measure: Mapped[str] = mapped_column(String(16), default="Amount", server_default="Amount")  # Amount (header amount) | Tax (tax recorded in the detail)
    source_system: Mapped[str | None] = mapped_column(String(50))
    transaction_types: Mapped[list] = mapped_column(JSON, default=list)
    tender_type: Mapped[str | None] = mapped_column(String(50))
    level: Mapped[str] = mapped_column(String(24), default="Store")
    corresponds_to_rtlog: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    system_calculated: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_with_wizard: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    required_by_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    display_group: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    realms: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    joins: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    parameters: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    rollups: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    restrictions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    combined_total_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    combined_operator: Mapped[str | None] = mapped_column(String(1))
    location_traits: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    usages: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    sql_statement: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_audit_total_definitions_name_revision", "name", "revision", unique=True),)

class SalesAuditErrorDefinition(Base):
    __tablename__ = "sales_audit_error_definitions"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    short_description: Mapped[str] = mapped_column(String(255))
    form: Mapped[str | None] = mapped_column(String(100))
    tab: Mapped[str | None] = mapped_column(String(100))
    recommended_solution: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

"""Startup provisioning for the built-in connector health records."""
import uuid

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.system.models import Connector

DEFAULT_CONNECTORS = ("POS Feed", "Payment Processor", "Bank Feed", "ERP / GL")
CONNECTOR_NAMESPACE = uuid.UUID("89f4960f-13e6-4f8c-9a0a-3c88ba8cb866")


async def ensure_default_connectors(db: AsyncSession) -> None:
    """Insert missing built-ins once, safely when several app replicas start together."""
    values = [{"id": str(uuid.uuid5(CONNECTOR_NAMESPACE, name)), "name": name,
               "state": "Unknown", "mode": "Not Configured", "last_checked_at": None}
              for name in DEFAULT_CONNECTORS]
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(Connector).values(values).on_conflict_do_nothing(index_elements=["name"])
    elif dialect == "sqlite":
        statement = sqlite_insert(Connector).values(values).on_conflict_do_nothing(index_elements=["name"])
    else:
        raise RuntimeError(f"Default connector provisioning is unsupported for database dialect {dialect}")
    await db.execute(statement)
    await db.commit()

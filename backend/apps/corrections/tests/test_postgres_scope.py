"""Run with ZETSA_TEST_POSTGRES_URL to verify JSON-based store scoping on PostgreSQL."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.corrections.routes import list_adjustments
from models import Base, TransactionAdjustment

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")


def test_missing_transaction_scope_on_postgres():
    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}})
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([TransactionAdjustment(id=store, adjustment_type="MissingTransaction", status="Proposed",
                    proposed_values={"store_id": store}, rationale="test", proposed_by="user") for store in ("S1", "S2")])
                await db.commit()
                user = {"store_scoped": True, "stores": ["S1"]}
                assert [row["id"] for row in (await list_adjustments(None, None, db, user))["items"]] == ["S1"]
                assert (await list_adjustments(None, None, db, {"store_scoped": True, "stores": []}))["items"] == []
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()
    asyncio.run(run())

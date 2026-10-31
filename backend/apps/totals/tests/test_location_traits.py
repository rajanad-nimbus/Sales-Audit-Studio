import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.store_days.routes import retotal_store_day
from apps.totals.routes import preview_definition
from models import AuditTotal, AuditTotalDefinition, Base, FoundationReference, StoreDay


def test_location_traits_limit_totals_and_overlap_does_not_duplicate():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([
                    FoundationReference(id="store", category="Store", code="S1", name="Store", active=True,
                                        attributes={"location_traits": ["North", "Mall"]}),
                    StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                    AuditTotalDefinition(id="match", name="Matching", location_traits=["North", "Mall"], active=True),
                    AuditTotalDefinition(id="other", name="Other", location_traits=["South"], active=True),
                    AuditTotalDefinition(id="all", name="All", location_traits=[], active=True),
                ])
                await ingest(db, "POS", "delivery", [{"source_record_id": "sale", "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z", "store_id": "S1", "amount": "10"}])
                await db.commit()
                assert (await preview_definition("match", "S1", "2026-10-01", db, {}))["value"] == 10
                with pytest.raises(HTTPException, match="does not apply"):
                    await preview_definition("other", "S1", "2026-10-01", db, {})
                await retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
                totals = (await db.execute(select(AuditTotal).where(AuditTotal.total_name.like("Configured:%")))).scalars().all()
                assert {item.total_name: item.calculated_amount for item in totals} == {
                    "Configured: Matching": 10, "Configured: All": 10}
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_trait_resolution_requires_valid_store_reference():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                            AuditTotalDefinition(id="total", name="T", location_traits=["North"], active=True)])
                await db.commit()
                with pytest.raises(HTTPException, match="active store reference"):
                    await retotal_store_day("S1", "2026-10-01", db, {"user": "finance"})
        finally:
            await engine.dispose()
    asyncio.run(run())

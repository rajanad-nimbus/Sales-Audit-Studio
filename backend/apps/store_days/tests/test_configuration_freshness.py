import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.controls.foundation.routes import ReferenceBody, update_reference
from apps.controls.rules.routes import RuleBody, create_rule
from apps.totals.routes import TotalDefinitionBody, create_definition, update_definition
from models import Base, ConfiguredAuditRule, FoundationReference, StoreDay, StoreDayClosureRequest


def test_definition_invalidation_follows_effective_dates_and_supersedes_requests():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([StoreDay(id="old", store_id="S1", business_date="2026-09-30", audit_version=1),
                            StoreDay(id="new", store_id="S1", business_date="2026-10-01", audit_version=1),
                            StoreDayClosureRequest(id="req", store_day_id="new", requested_by="reviewer", evidence_basis="reviewed")])
                await db.commit()
                await create_definition(TotalDefinitionBody(name="New", start_date="2026-10-01"), db, {"user": "finance"})
                db.expire_all()
                assert not (await db.get(StoreDay, "old")).totals_stale
                assert (await db.get(StoreDay, "new")).totals_stale
                assert (await db.get(StoreDayClosureRequest, "req")).status == "Superseded"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_rule_invalidation_rolls_back_on_duplicate_and_trait_updates_are_store_specific():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([StoreDay(id="a", store_id="S1", business_date="2026-10-01", audit_version=1),
                            StoreDay(id="b", store_id="S2", business_date="2026-10-01", audit_version=1),
                            ConfiguredAuditRule(id="r", name="Existing", total_name="POS Net", threshold=Decimal("1")),
                            FoundationReference(id="store", category="Store", code="S1", name="Store", active=True, attributes={})])
                await db.commit()
                with pytest.raises(HTTPException) as exc:
                    await create_rule(RuleBody(name="Existing", total_name="POS Net", threshold=Decimal("1")), db, {"user": "finance"})
                assert exc.value.status_code == 409
                db.expire_all()
                assert not (await db.get(StoreDay, "a")).totals_stale
                assert not (await db.get(StoreDay, "b")).totals_stale
                await update_reference("store", ReferenceBody(category="Store", code="S1", name="Store",
                                       attributes={"location_traits": ["North"]}), db, {"user": "finance"})
                db.expire_all()
                assert (await db.get(StoreDay, "a")).totals_stale
                assert not (await db.get(StoreDay, "b")).totals_stale
                await create_rule(RuleBody(name="New rule", total_name="POS Net", threshold=Decimal("1")), db, {"user": "finance"})
                db.expire_all()
                assert (await db.get(StoreDay, "b")).totals_stale
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_definition_revision_invalidates_days_from_old_and_new_date_ranges():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                created = await create_definition(
                    TotalDefinitionBody(name="Seasonal", start_date="2026-10-01", end_date="2026-10-31"),
                    db,
                    {"user": "finance"},
                )
                db.add_all([
                    StoreDay(id="before", store_id="S1", business_date="2026-09-30", audit_version=1),
                    StoreDay(id="old-range", store_id="S1", business_date="2026-10-15", audit_version=1),
                    StoreDay(id="new-range", store_id="S1", business_date="2026-11-15", audit_version=1),
                ])
                await db.commit()

                await update_definition(
                    created["id"],
                    TotalDefinitionBody(name="Seasonal", start_date="2026-11-01", end_date="2026-11-30"),
                    db,
                    {"user": "finance"},
                )
                db.expire_all()
                assert not (await db.get(StoreDay, "before")).totals_stale
                assert (await db.get(StoreDay, "old-range")).totals_stale
                assert (await db.get(StoreDay, "new-range")).totals_stale
        finally:
            await engine.dispose()
    asyncio.run(run())

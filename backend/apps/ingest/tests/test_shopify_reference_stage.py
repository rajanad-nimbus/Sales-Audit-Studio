"""Database tests for atomic, bounded Shopify reference snapshot reconciliation."""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.shopify import adapter as shopify_adapter
from apps.ingest.shopify import reference_sync
from models import Base, FoundationReference, ShopifyReferenceStage, ShopifyReferenceSyncState

_engines = []


def run(coro):
    async def with_cleanup():
        try:
            return await coro
        finally:
            while _engines:
                await _engines.pop().dispose()
    return asyncio.run(with_cleanup())


async def make_session(engine):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(ShopifyReferenceSyncState(key="Shopify", generation=0, applied_generation=0))
        await db.commit()
    return sessions


class CatalogAdapter:
    store_code = "SHOPIFY-MAIN"

    async def fetch_shop(self):
        return {"id": 1, "name": "Shop"}

    async def fetch_locations(self):
        yield {"id": 3, "name": "Store 3"}

    async def fetch_products(self):
        yield {"id": 10, "title": "New name", "status": "active", "variants": [
            {"id": 101, "sku": "SKU-1", "price": "12.00"},
            {"id": 102, "sku": "SKU-2", "price": "15.00"},
        ]}


def test_staged_snapshot_updates_and_deactivates_atomically():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add_all([
                FoundationReference(id="old", category="Item", code="SKU-1", name="Old name", active=True,
                                    attributes={"source": "Shopify"}),
                FoundationReference(id="gone", category="Item", code="GONE", name="Gone", active=True,
                                    attributes={"source": "Shopify"}),
                FoundationReference(id="manual", category="Item", code="SKU-2", name="Manual", active=True,
                                    attributes={"source": "operator"}),
            ])
            await db.commit()

            staged = await reference_sync.stage(CatalogAdapter(), db, "sync-1", "SHOPIFY-MAIN")
            assert {key: staged[key] for key in ("stores", "items", "duplicates")} == {
                "stores": 2, "items": 2, "duplicates": 0,
            }
            assert staged["generation"] == 1
            assert (await db.get(FoundationReference, "old")).name == "Old name"
            assert (await db.get(FoundationReference, "gone")).active is True

            counts = await reference_sync.apply(
                db, "sync-1", staged["generation"], "test-actor", staged["duplicates"]
            )
            await db.commit()
            assert counts == {"created": 2, "updated": 1, "deactivated": 1,
                              "skipped_manual": 1, "duplicates": 0, "superseded": False}
            assert (await db.get(FoundationReference, "old")).name == "New name"
            gone = await db.get(FoundationReference, "gone")
            await db.refresh(gone)
            assert gone.active is False
            assert (await db.get(FoundationReference, "manual")).name == "Manual"
            assert (await db.execute(select(ShopifyReferenceStage))).scalars().all() == []
    run(go())


def test_failed_fetch_discards_stage_without_changing_live_references(monkeypatch):
    class FailingCatalog(CatalogAdapter):
        async def fetch_products(self):
            yield {"id": 10, "title": "Partial", "status": "active", "variants": [
                {"id": 101, "sku": "SKU-1"},
            ]}
            raise shopify_adapter.ShopifyAPIError("page fetch failed")

    async def go():
        monkeypatch.setattr(reference_sync, "STAGE_BATCH_SIZE", 1)
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add(FoundationReference(id="old", category="Item", code="SKU-1", name="Original", active=True,
                                       attributes={"source": "Shopify"}))
            await db.commit()

            with pytest.raises(shopify_adapter.ShopifyAPIError, match="page fetch failed"):
                await reference_sync.stage(FailingCatalog(), db, "failed-sync", "SHOPIFY-MAIN")

            assert (await db.get(FoundationReference, "old")).name == "Original"
            assert (await db.execute(select(ShopifyReferenceStage))).scalars().all() == []
    run(go())


def test_stage_cleanup_error_does_not_mask_original_failure(monkeypatch):
    class BrokenCatalog(CatalogAdapter):
        async def fetch_shop(self):
            raise shopify_adapter.ShopifyAPIError("catalog unavailable")

    async def broken_discard(*_args, **_kwargs):
        raise RuntimeError("database unavailable during cleanup")

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            monkeypatch.setattr(reference_sync, "discard", broken_discard)
            with pytest.raises(shopify_adapter.ShopifyAPIError, match="catalog unavailable"):
                await reference_sync.stage(BrokenCatalog(), db, "broken-sync", "SHOPIFY-MAIN")
    run(go())


def test_duplicate_skus_keep_first_staged_variant(monkeypatch):
    class DuplicateCatalog(CatalogAdapter):
        async def fetch_products(self):
            yield {"id": 10, "title": "First", "status": "active", "variants": [{"id": 101, "sku": "DUP"}]}
            yield {"id": 11, "title": "Second", "status": "active", "variants": [{"id": 201, "sku": "DUP"}]}

    async def go():
        monkeypatch.setattr(reference_sync, "STAGE_BATCH_SIZE", 1)
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            staged = await reference_sync.stage(DuplicateCatalog(), db, "duplicate-sync", "SHOPIFY-MAIN")
            assert staged["duplicates"] == 1
            await reference_sync.apply(
                db, "duplicate-sync", staged["generation"], "test-actor", staged["duplicates"]
            )
            await db.commit()
            row = (await db.execute(select(FoundationReference).where(
                FoundationReference.code == "DUP",
            ))).scalar_one()
            assert row.name == "First"
    run(go())


def test_older_snapshot_cannot_overwrite_a_newer_applied_snapshot():
    class NamedCatalog(CatalogAdapter):
        def __init__(self, name):
            self.name = name

        async def fetch_products(self):
            yield {"id": 10, "title": self.name, "status": "active", "variants": [
                {"id": 101, "sku": "SKU-1"},
            ]}

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            older = await reference_sync.stage(NamedCatalog("Older"), db, "older-sync", "SHOPIFY-MAIN")
            newer = await reference_sync.stage(NamedCatalog("Newer"), db, "newer-sync", "SHOPIFY-MAIN")
            assert newer["generation"] > older["generation"]

            newest_counts = await reference_sync.apply(
                db, "newer-sync", newer["generation"], "test-actor", newer["duplicates"]
            )
            await db.commit()
            stale_counts = await reference_sync.apply(
                db, "older-sync", older["generation"], "test-actor", older["duplicates"]
            )
            await db.commit()

            row = (await db.execute(select(FoundationReference).where(
                FoundationReference.code == "SKU-1",
            ))).scalar_one()
            assert newest_counts["superseded"] is False
            assert stale_counts["superseded"] is True
            assert row.name == "Newer"
    run(go())

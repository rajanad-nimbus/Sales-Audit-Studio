"""Database tests for atomic, bounded Shopify reference snapshot reconciliation."""

import asyncio

import pytest
from apps.ingest.shopify import adapter as shopify_adapter
from apps.ingest.shopify import reference_sync
from apps.ingest.shopify import routes as shopify_routes
from models import (
    Base,
    FoundationReference,
    ShopifyReferenceStage,
    ShopifyReferenceSyncState,
    StoreDay,
    StoreDayClosureRequest,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
        yield {
            "id": 10,
            "title": "New name",
            "status": "active",
            "variants": [
                {"id": 101, "sku": "SKU-1", "price": "12.00"},
                {"id": 102, "sku": "SKU-2", "price": "15.00"},
            ],
        }


def test_staged_snapshot_updates_and_deactivates_atomically():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add_all(
                [
                    FoundationReference(
                        id="old",
                        category="Item",
                        code="SKU-1",
                        name="Old name",
                        active=True,
                        attributes={"source": "Shopify"},
                    ),
                    FoundationReference(
                        id="gone",
                        category="Item",
                        code="GONE",
                        name="Gone",
                        active=True,
                        attributes={"source": "Shopify"},
                    ),
                    FoundationReference(
                        id="manual",
                        category="Item",
                        code="SKU-2",
                        name="Manual",
                        active=True,
                        attributes={"source": "operator"},
                    ),
                ]
            )
            await db.commit()

            staged = await reference_sync.stage(CatalogAdapter(), db, "sync-1", "SHOPIFY-MAIN")
            assert {key: staged[key] for key in ("stores", "items", "duplicates")} == {
                "stores": 2,
                "items": 2,
                "duplicates": 0,
            }
            assert staged["generation"] == 1
            assert (await db.get(FoundationReference, "old")).name == "Old name"
            assert (await db.get(FoundationReference, "gone")).active is True

            counts = await reference_sync.apply(
                db, "sync-1", staged["generation"], "test-actor", staged["duplicates"]
            )
            await db.commit()
            assert counts == {
                "created": 2,
                "updated": 1,
                "deactivated": 1,
                "skipped_manual": 1,
                "duplicates": 0,
                "superseded": False,
            }
            assert (await db.get(FoundationReference, "old")).name == "New name"
            gone = await db.get(FoundationReference, "gone")
            await db.refresh(gone)
            assert gone.active is False
            assert (await db.get(FoundationReference, "manual")).name == "Manual"
            assert (await db.execute(select(ShopifyReferenceStage))).scalars().all() == []

    run(go())


def test_apply_invalidates_days_when_shopify_store_traits_change_or_store_deactivates():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add_all(
                [
                    FoundationReference(
                        id="shop",
                        category="Store",
                        code="SHOPIFY-MAIN",
                        name="Shop",
                        active=True,
                        attributes={"source": "Shopify", "location_traits": ["North"]},
                    ),
                    FoundationReference(
                        id="removed-shop",
                        category="Store",
                        code="OLD-LOCATION",
                        name="Old location",
                        active=True,
                        attributes={"source": "Shopify"},
                    ),
                    StoreDay(
                        id="trait-day",
                        store_id="SHOPIFY-MAIN",
                        business_date="2026-10-01",
                        audit_version=2,
                        totals_stale=False,
                    ),
                    StoreDay(
                        id="removed-day",
                        store_id="OLD-LOCATION",
                        business_date="2026-10-01",
                        audit_version=1,
                        totals_stale=False,
                    ),
                    StoreDayClosureRequest(
                        id="closure",
                        store_day_id="trait-day",
                        requested_by="finance",
                        evidence_basis="reviewed",
                        status="Pending",
                    ),
                ]
            )
            await db.commit()

            staged = await reference_sync.stage(
                CatalogAdapter(), db, "store-change", "SHOPIFY-MAIN"
            )
            counts = await reference_sync.apply(
                db, "store-change", staged["generation"], "test-actor", staged["duplicates"]
            )
            await db.commit()

            assert counts["deactivated"] == 1
            assert (await db.get(StoreDay, "trait-day")).totals_stale
            assert (await db.get(StoreDay, "removed-day")).totals_stale
            assert (await db.get(StoreDayClosureRequest, "closure")).status == "Superseded"

    run(go())


def test_failed_fetch_discards_stage_without_changing_live_references(monkeypatch):
    class FailingCatalog(CatalogAdapter):
        async def fetch_products(self):
            yield {
                "id": 10,
                "title": "Partial",
                "status": "active",
                "variants": [
                    {"id": 101, "sku": "SKU-1"},
                ],
            }
            raise shopify_adapter.ShopifyAPIError("page fetch failed")

    async def go():
        monkeypatch.setattr(reference_sync, "STAGE_BATCH_SIZE", 1)
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add(
                FoundationReference(
                    id="old",
                    category="Item",
                    code="SKU-1",
                    name="Original",
                    active=True,
                    attributes={"source": "Shopify"},
                )
            )
            await db.commit()

            with pytest.raises(shopify_adapter.ShopifyAPIError, match="page fetch failed"):
                await reference_sync.stage(FailingCatalog(), db, "failed-sync", "SHOPIFY-MAIN")

            assert (await db.get(FoundationReference, "old")).name == "Original"
            assert (await db.execute(select(ShopifyReferenceStage))).scalars().all() == []

    run(go())


def test_invalid_next_link_discards_partial_catalog_without_changing_live_references(monkeypatch):
    class MalformedCatalog(CatalogAdapter):
        async def fetch_products(self):
            adapter = shopify_adapter.ShopifyAdapter(require_credentials=False)

            async def request_full(*_args, **_kwargs):
                return (
                    {
                        "products": [
                            {
                                "id": 10,
                                "title": "Partial",
                                "variants": [{"id": 101, "sku": "SKU-1"}],
                            }
                        ]
                    },
                    {"link": '<https://shop.test/products.json?limit=250>; rel="next"'},
                )

            adapter._request_full = request_full
            async for product in adapter.fetch_products():
                yield product

    async def go():
        monkeypatch.setattr(reference_sync, "STAGE_BATCH_SIZE", 1)
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add(
                FoundationReference(
                    id="old",
                    category="Item",
                    code="SKU-1",
                    name="Original",
                    active=True,
                    attributes={"source": "Shopify"},
                )
            )
            await db.commit()
            with pytest.raises(shopify_adapter.ShopifyAPIError, match="invalid pagination link"):
                await reference_sync.stage(MalformedCatalog(), db, "invalid-sync", "SHOPIFY-MAIN")

            live = await db.get(FoundationReference, "old")
            assert live.name == "Original" and live.active
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


def test_reference_route_stages_in_an_isolated_session(monkeypatch):
    class RequestSession:
        commits = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            raise AssertionError("request transaction should not be rolled back")

    class StagingSession:
        pass

    class StagingContext:
        async def __aenter__(self):
            return staging_session

        async def __aexit__(self, *_args):
            return False

    class Adapter:
        async def close(self):
            pass

    request_session = RequestSession()
    staging_session = StagingSession()
    observed = {}

    async def require_allowed(*_args, **_kwargs):
        return None

    async def stage(_adapter, db, *_args):
        observed["stage_session"] = db
        return {"generation": 1, "duplicates": 0}

    async def apply(db, *_args):
        observed["apply_session"] = db
        return {"created": 0, "updated": 0, "deactivated": 0, "superseded": False}

    monkeypatch.setattr(shopify_routes, "async_session", StagingContext)
    monkeypatch.setattr(shopify_routes.shopify_adapter, "ShopifyAdapter", Adapter)
    monkeypatch.setattr(shopify_routes.source_settings, "require_allowed", require_allowed)
    monkeypatch.setattr(
        shopify_routes.source_settings,
        "lock_source_settings",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=None),
    )
    monkeypatch.setattr(shopify_routes.reference_sync, "stage", stage)
    monkeypatch.setattr(shopify_routes.reference_sync, "apply", apply)
    monkeypatch.setattr(shopify_routes, "audit", lambda *_args, **_kwargs: None)

    async def go():
        await shopify_routes.sync_reference(request_session, {"user": "test"})

    run(go())

    assert observed["stage_session"] is staging_session
    assert observed["apply_session"] is request_session
    assert request_session.commits == 1


def test_reference_route_rechecks_manual_permission_before_apply(monkeypatch):
    class RequestSession:
        rolled_back = False

        async def rollback(self):
            self.rolled_back = True

    class StageSession:
        pass

    class SessionContext:
        async def __aenter__(self):
            return stage_session

        async def __aexit__(self, *_args):
            return False

    class Adapter:
        async def close(self):
            pass

    request_session = RequestSession()
    stage_session = StageSession()
    applied = False
    permission_check = {}

    async def require_allowed(_db, _source, *, manual=False):
        permission_check["manual"] = manual

    async def stage(*_args):
        return {"generation": 1, "duplicates": 0}

    async def apply(*_args):
        nonlocal applied
        applied = True
        return {}

    async def discard(*_args):
        pass

    monkeypatch.setattr(shopify_routes, "async_session", SessionContext)
    monkeypatch.setattr(shopify_routes.shopify_adapter, "ShopifyAdapter", Adapter)
    monkeypatch.setattr(shopify_routes.source_settings, "require_allowed", require_allowed)
    monkeypatch.setattr(
        shopify_routes.source_settings,
        "lock_source_settings",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=object()),
    )
    monkeypatch.setattr(
        shopify_routes.source_settings,
        "effective",
        lambda *_args: {
            "enabled": True,
            "manual_sync": False,
        },
    )
    monkeypatch.setattr(shopify_routes.reference_sync, "stage", stage)
    monkeypatch.setattr(shopify_routes.reference_sync, "apply", apply)
    monkeypatch.setattr(shopify_routes.reference_sync, "discard", discard)

    with pytest.raises(shopify_routes.HTTPException) as error:
        run(shopify_routes.sync_reference(request_session, {"user": "test"}))

    assert permission_check["manual"] is True
    assert error.value.status_code == 409
    assert "Manual sync was switched off" in error.value.detail
    assert request_session.rolled_back
    assert not applied


def test_duplicate_skus_keep_first_staged_variant(monkeypatch):
    class DuplicateCatalog(CatalogAdapter):
        async def fetch_products(self):
            yield {
                "id": 10,
                "title": "First",
                "status": "active",
                "variants": [{"id": 101, "sku": "DUP"}],
            }
            yield {
                "id": 11,
                "title": "Second",
                "status": "active",
                "variants": [{"id": 201, "sku": "DUP"}],
            }

    async def go():
        monkeypatch.setattr(reference_sync, "STAGE_BATCH_SIZE", 1)
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            staged = await reference_sync.stage(
                DuplicateCatalog(), db, "duplicate-sync", "SHOPIFY-MAIN"
            )
            assert staged["duplicates"] == 1
            await reference_sync.apply(
                db, "duplicate-sync", staged["generation"], "test-actor", staged["duplicates"]
            )
            await db.commit()
            row = (
                await db.execute(
                    select(FoundationReference).where(
                        FoundationReference.code == "DUP",
                    )
                )
            ).scalar_one()
            assert row.name == "First"

    run(go())


def test_older_snapshot_cannot_overwrite_a_newer_applied_snapshot():
    class NamedCatalog(CatalogAdapter):
        def __init__(self, name):
            self.name = name

        async def fetch_products(self):
            yield {
                "id": 10,
                "title": self.name,
                "status": "active",
                "variants": [
                    {"id": 101, "sku": "SKU-1"},
                ],
            }

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            older = await reference_sync.stage(
                NamedCatalog("Older"), db, "older-sync", "SHOPIFY-MAIN"
            )
            newer = await reference_sync.stage(
                NamedCatalog("Newer"), db, "newer-sync", "SHOPIFY-MAIN"
            )
            assert newer["generation"] > older["generation"]

            newest_counts = await reference_sync.apply(
                db, "newer-sync", newer["generation"], "test-actor", newer["duplicates"]
            )
            await db.commit()
            stale_counts = await reference_sync.apply(
                db, "older-sync", older["generation"], "test-actor", older["duplicates"]
            )
            await db.commit()

            row = (
                await db.execute(
                    select(FoundationReference).where(
                        FoundationReference.code == "SKU-1",
                    )
                )
            ).scalar_one()
            assert newest_counts["superseded"] is False
            assert stale_counts["superseded"] is True
            assert row.name == "Newer"

    run(go())


def test_older_snapshot_is_not_applied_while_newer_generation_is_staging():
    class NamedCatalog(CatalogAdapter):
        def __init__(self, name):
            self.name = name

        async def fetch_products(self):
            yield {
                "id": 10,
                "title": self.name,
                "status": "active",
                "variants": [
                    {"id": 101, "sku": "SKU-1"},
                ],
            }

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            older = await reference_sync.stage(
                NamedCatalog("Older"), db, "older-inflight", "SHOPIFY-MAIN"
            )
            newer = await reference_sync.stage(
                NamedCatalog("Newer"), db, "newer-inflight", "SHOPIFY-MAIN"
            )

            stale_counts = await reference_sync.apply(
                db, "older-inflight", older["generation"], "test-actor", older["duplicates"]
            )
            await db.commit()

            row = (
                await db.execute(
                    select(FoundationReference).where(
                        FoundationReference.code == "SKU-1",
                    )
                )
            ).scalar_one_or_none()
            assert stale_counts["superseded"] is True
            assert row is None

            newest_counts = await reference_sync.apply(
                db, "newer-inflight", newer["generation"], "test-actor", newer["duplicates"]
            )
            await db.commit()
            row = (
                await db.execute(
                    select(FoundationReference).where(
                        FoundationReference.code == "SKU-1",
                    )
                )
            ).scalar_one()
            assert newest_counts["superseded"] is False
            assert row.name == "Newer"

    run(go())


def test_apply_rejects_missing_or_mismatched_stage_without_deactivating_references():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        _engines.append(engine)
        Session = await make_session(engine)
        async with Session() as db:
            db.add(
                FoundationReference(
                    id="keep",
                    category="Item",
                    code="KEEP",
                    name="Keep",
                    active=True,
                    attributes={"source": "Shopify"},
                )
            )
            state = (await db.execute(select(ShopifyReferenceSyncState))).scalar_one()
            state.generation = 1
            await db.commit()

            with pytest.raises(
                reference_sync.ShopifyReferenceStageError, match="snapshot is missing"
            ):
                await reference_sync.apply(db, "missing-stage", 1, "test-actor")
            await db.rollback()
            assert (await db.get(FoundationReference, "keep")).active is True

            staged = await reference_sync.stage(
                CatalogAdapter(), db, "mismatched-stage", "SHOPIFY-MAIN"
            )
            with pytest.raises(reference_sync.ShopifyReferenceStageError, match="does not match"):
                await reference_sync.apply(
                    db, "mismatched-stage", staged["generation"] + 1, "test-actor"
                )
            await db.rollback()
            row = await db.get(FoundationReference, "keep")
            await db.refresh(row)
            assert row.active is True

    run(go())

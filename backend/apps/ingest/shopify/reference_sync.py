"""Stage complete Shopify snapshots, then atomically reconcile retail references."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, tuple_, update, delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from model_base import utcnow
from apps.controls.foundation.models import FoundationReference
from apps.ingest.models import ShopifyReferenceStage, ShopifyReferenceSyncState

SOURCE = "Shopify"
STAGE_BATCH_SIZE = 500
ORPHAN_RETENTION = timedelta(days=1)
logger = logging.getLogger(__name__)


def _clip(value: str, size: int) -> str:
    return (value or "").strip()[:size]


def shop_to_store(shop: dict, store_code: str) -> dict:
    """The shop itself is the store that Shopify orders are booked to (SHOPIFY_STORE_ID)."""
    return {"category": "Store", "code": _clip(store_code, 100), "name": _clip(shop.get("name") or store_code, 255),
            "active": True, "attributes": {"source": SOURCE, "kind": "shop", "shopify_id": shop.get("id"),
                                           "currency": shop.get("currency"), "timezone": shop.get("iana_timezone")}}


def location_to_store(location: dict) -> dict:
    return {"category": "Store", "code": _clip(f"SHOPIFY-LOC-{location['id']}", 100),
            "name": _clip(location.get("name") or f"Location {location['id']}", 255),
            "active": bool(location.get("active", True)),
            "attributes": {"source": SOURCE, "kind": "location", "shopify_id": location["id"],
                           "city": location.get("city"), "province": location.get("province"),
                           "country": location.get("country_code") or location.get("country")}}


def product_to_items(product: dict) -> list[dict]:
    """One Item per variant, keyed by SKU (falling back to the variant id)."""
    items = []
    for variant in product.get("variants") or []:
        sku = (variant.get("sku") or "").strip()
        label = (variant.get("title") or "").strip()
        name = product.get("title") or f"Product {product['id']}"
        if label and label != "Default Title":
            name = f"{name} - {label}"
        items.append({"category": "Item", "code": _clip(sku or f"SHOPIFY-VAR-{variant['id']}", 100),
                      "name": _clip(name, 255), "active": product.get("status", "active") == "active",
                      "attributes": {"source": SOURCE, "shopify_product_id": product["id"],
                                     "shopify_variant_id": variant["id"], "price": variant.get("price"),
                                     "vendor": product.get("vendor"), "product_type": product.get("product_type")}})
    return items


async def _flush_stage(
    db: AsyncSession, sync_id: str, generation: int, pending: dict[tuple[str, str], dict]
) -> dict:
    """Persist one bounded stage batch and return counts for newly staged references."""
    if not pending:
        return {"stores": 0, "items": 0, "duplicates": 0}
    values = [
        {"id": str(uuid.uuid4()), "sync_id": sync_id, "generation": generation, **ref}
        for ref in pending.values()
    ]
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(ShopifyReferenceStage).values(values).on_conflict_do_nothing(
            constraint="uq_shopify_reference_stage_sync_code",
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(ShopifyReferenceStage).values(values).on_conflict_do_nothing(
            index_elements=["sync_id", "category", "code"],
        )
    else:
        raise RuntimeError(f"Unsupported database dialect for Shopify reference staging: {dialect}")
    inserted_categories = (await db.execute(
        statement.returning(ShopifyReferenceStage.category)
    )).scalars().all()
    await db.commit()
    counts = {
        "stores": sum(category == "Store" for category in inserted_categories),
        "items": sum(category == "Item" for category in inserted_categories),
        "duplicates": len(values) - len(inserted_categories),
    }
    pending.clear()
    return counts


async def discard(db: AsyncSession, sync_id: str) -> None:
    """Delete an incomplete or already-applied staging snapshot."""
    await db.execute(delete(ShopifyReferenceStage).where(ShopifyReferenceStage.sync_id == sync_id))
    await db.commit()


async def stage(adapter, db: AsyncSession, sync_id: str, store_code: str) -> dict:
    """Fetch and persist a complete Shopify catalog before any live reference is changed."""
    generation = (await db.execute(update(ShopifyReferenceSyncState).where(
        ShopifyReferenceSyncState.key == SOURCE,
    ).values(generation=ShopifyReferenceSyncState.generation + 1).returning(
        ShopifyReferenceSyncState.generation,
    ))).scalar_one()
    cutoff = datetime.now(timezone.utc) - ORPHAN_RETENTION
    await db.execute(delete(ShopifyReferenceStage).where(ShopifyReferenceStage.created_at < cutoff))
    await db.commit()
    pending: dict[tuple[str, str], dict] = {}
    counts = {"stores": 0, "items": 0, "duplicates": 0}

    async def add(ref: dict) -> None:
        key = (ref["category"], ref["code"])
        if key in pending:
            counts["duplicates"] += 1
            return
        pending[key] = ref
        if len(pending) >= STAGE_BATCH_SIZE:
            batch_counts = await _flush_stage(db, sync_id, generation, pending)
            for name, value in batch_counts.items():
                counts[name] += value

    try:
        await add(shop_to_store(await adapter.fetch_shop(), store_code))
        async for location in adapter.fetch_locations():
            await add(location_to_store(location))
        async for product in adapter.fetch_products():
            for item in product_to_items(product):
                await add(item)
        batch_counts = await _flush_stage(db, sync_id, generation, pending)
        for name, value in batch_counts.items():
            counts[name] += value
        return {**counts, "generation": generation}
    except Exception:
        await db.rollback()
        try:
            await discard(db, sync_id)
        except Exception:
            logger.exception("Could not discard failed Shopify reference stage %s", sync_id)
        raise


async def _apply_staged_rows(db: AsyncSession, sync_id: str, actor: str, counts: dict) -> None:
    """Apply staged references in bounded pages within the caller's transaction."""
    last_id = ""
    while True:
        rows = (await db.execute(select(ShopifyReferenceStage).where(
            ShopifyReferenceStage.sync_id == sync_id,
            ShopifyReferenceStage.id > last_id,
        ).order_by(ShopifyReferenceStage.id).limit(STAGE_BATCH_SIZE))).scalars().all()
        if not rows:
            return
        keys = [(row.category, row.code) for row in rows]
        existing = {(row.category, row.code): row for row in (await db.execute(select(FoundationReference).where(
            tuple_(FoundationReference.category, FoundationReference.code).in_(keys),
        ))).scalars().all()}
        for staged in rows:
            key = (staged.category, staged.code)
            row = existing.get(key)
            if row is None:
                db.add(FoundationReference(
                    id=str(uuid.uuid4()), category=staged.category, code=staged.code, name=staged.name,
                    active=staged.active, attributes=staged.attributes, updated_by=actor,
                ))
                counts["created"] += 1
            elif (row.attributes or {}).get("source") != SOURCE:
                counts["skipped_manual"] += 1
            elif (row.name, row.active, row.attributes) != (staged.name, staged.active, staged.attributes):
                row.name, row.active, row.attributes = staged.name, staged.active, staged.attributes
                row.updated_by, row.updated_at = actor, utcnow()
                counts["updated"] += 1
        last_id = rows[-1].id
        await db.flush()


async def apply(
    db: AsyncSession, sync_id: str, generation: int, actor: str, duplicate_count: int = 0
) -> dict:
    """Atomically upsert the complete staged snapshot and deactivate missing Shopify references."""
    counts = {"created": 0, "updated": 0, "deactivated": 0,
              "skipped_manual": 0, "duplicates": duplicate_count, "superseded": False}
    state = (await db.execute(select(ShopifyReferenceSyncState).where(
        ShopifyReferenceSyncState.key == SOURCE,
    ).with_for_update())).scalar_one()
    if generation <= state.applied_generation:
        counts["superseded"] = True
        await db.execute(delete(ShopifyReferenceStage).where(ShopifyReferenceStage.sync_id == sync_id))
        return counts

    await _apply_staged_rows(db, sync_id, actor, counts)

    staged_match = select(ShopifyReferenceStage.id).where(
        ShopifyReferenceStage.sync_id == sync_id,
        ShopifyReferenceStage.category == FoundationReference.category,
        ShopifyReferenceStage.code == FoundationReference.code,
    ).exists()
    result = await db.execute(update(FoundationReference).where(
        FoundationReference.category.in_(("Store", "Item")),
        FoundationReference.active.is_(True),
        FoundationReference.attributes["source"].as_string() == SOURCE,
        ~staged_match,
    ).values(active=False, updated_by=actor, updated_at=utcnow()).execution_options(synchronize_session=False))
    counts["deactivated"] = result.rowcount or 0
    await db.execute(delete(ShopifyReferenceStage).where(ShopifyReferenceStage.sync_id == sync_id))
    state.applied_generation = generation
    return counts

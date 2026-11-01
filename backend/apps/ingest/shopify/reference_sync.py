"""Stage complete Shopify snapshots, then atomically reconcile retail references."""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, tuple_, update, delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from model_base import utcnow
from apps.controls.foundation.models import FoundationReference
from apps.ingest.models import ShopifyReferenceStage, ShopifyReferenceSyncState
from apps.ingest.shopify.adapter import ShopifyAPIError
from apps.store_days.freshness import invalidate_configuration_locked, lock_configuration

SOURCE = "Shopify"
STAGE_BATCH_SIZE = 500
ORPHAN_RETENTION = timedelta(days=1)
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StageAccumulator:
    """Deduplicate one Shopify snapshot and flush references in bounded batches."""
    db: AsyncSession
    sync_id: str
    generation: int
    pending: dict[tuple[str, str], dict] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {
        "stores": 0, "items": 0, "duplicates": 0,
    })

    async def add(self, reference: dict) -> None:
        key = (reference["category"], reference["code"])
        if key in self.pending:
            self.counts["duplicates"] += 1
            return
        self.pending[key] = reference
        if len(self.pending) >= STAGE_BATCH_SIZE:
            await self.flush()

    async def flush(self) -> None:
        batch_counts = await _flush_stage(self.db, self.sync_id, self.generation, self.pending)
        for name, value in batch_counts.items():
            self.counts[name] += value


def _clip(value, size: int) -> str:
    return str(value or "").strip()[:size]


def _required_text(value, size: int, response: str, fallback=None) -> str:
    normalized = _clip(value, size)
    if normalized:
        return normalized
    normalized_fallback = _clip(fallback, size)
    if normalized_fallback:
        return normalized_fallback
    raise ShopifyAPIError(f"Shopify returned an invalid {response} response")


def shop_to_store(shop: dict, store_code: str) -> dict:
    """The shop itself is the store that Shopify orders are booked to (SHOPIFY_STORE_ID)."""
    code = _required_text(store_code, 100, "shop")
    return {"category": "Store", "code": code,
            "name": _required_text(shop.get("name"), 255, "shop", fallback=code),
            "active": True, "attributes": {"source": SOURCE, "kind": "shop", "shopify_id": shop.get("id"),
                                           "currency": shop.get("currency"), "timezone": shop.get("iana_timezone")}}


def location_to_store(location: dict) -> dict:
    location_id = _required_text(location.get("id"), 100, "locations")
    return {"category": "Store", "code": _clip(f"SHOPIFY-LOC-{location_id}", 100),
            "name": _required_text(location.get("name"), 255, "locations", fallback=f"Location {location_id}"),
            "active": bool(location.get("active", True)),
            "attributes": {"source": SOURCE, "kind": "location", "shopify_id": location["id"],
                           "city": location.get("city"), "province": location.get("province"),
                           "country": location.get("country_code") or location.get("country")}}


def product_to_items(product: dict) -> list[dict]:
    """One Item per variant, keyed by SKU (falling back to the variant id)."""
    product_id = _required_text(product.get("id"), 100, "products")
    product_name = _required_text(
        product.get("title"), 255, "products", fallback=f"Product {product_id}"
    )
    items = []
    for variant in product.get("variants") or []:
        sku = (variant.get("sku") or "").strip()
        label = (variant.get("title") or "").strip()
        variant_id = _required_text(variant.get("id"), 100, "products")
        name = product_name
        if label and label != "Default Title":
            name = f"{name} - {label}"
        items.append({"category": "Item", "code": _required_text(sku, 100, "products", fallback=f"SHOPIFY-VAR-{variant_id}"),
                      "name": _clip(name, 255), "active": product.get("status", "active") == "active",
                      "attributes": {"source": SOURCE, "shopify_product_id": product_id,
                                     "shopify_variant_id": variant_id, "price": variant.get("price"),
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
    """Delete an incomplete or already-applied staging snapshot in the caller's transaction."""
    await db.execute(delete(ShopifyReferenceStage).where(ShopifyReferenceStage.sync_id == sync_id))


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
    accumulator = StageAccumulator(db, sync_id, generation)
    try:
        await fetch_catalog_into_stage(adapter, store_code, accumulator)
        await accumulator.flush()
        return {**accumulator.counts, "generation": generation}
    except Exception:
        await db.rollback()
        try:
            await discard(db, sync_id)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Could not discard failed Shopify reference stage %s", sync_id)
        raise


async def fetch_catalog_into_stage(adapter, store_code: str, accumulator: StageAccumulator) -> None:
    """Fetch shop, locations, and products in source order into a staging accumulator."""
    shop = await adapter.fetch_shop()
    await accumulator.add(shop_to_store(shop, store_code))
    async for location in adapter.fetch_locations():
        await accumulator.add(location_to_store(location))
    async for product in adapter.fetch_products():
        for item in product_to_items(product):
            await accumulator.add(item)


async def _apply_staged_rows(db: AsyncSession, sync_id: str, actor: str, counts: dict) -> set[str]:
    """Apply staged references in bounded pages and report Store eligibility changes."""
    last_id = ""
    affected_stores: set[str] = set()
    while True:
        rows = (await db.execute(select(ShopifyReferenceStage).where(
            ShopifyReferenceStage.sync_id == sync_id,
            ShopifyReferenceStage.id > last_id,
        ).order_by(ShopifyReferenceStage.id).limit(STAGE_BATCH_SIZE))).scalars().all()
        if not rows:
            return affected_stores
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
                if staged.category == "Store":
                    affected_stores.add(staged.code)
                counts["created"] += 1
            elif (row.attributes or {}).get("source") != SOURCE:
                counts["skipped_manual"] += 1
            elif (row.name, row.active, row.attributes) != (staged.name, staged.active, staged.attributes):
                if staged.category == "Store" and (
                    row.active != staged.active
                    or _store_location_traits(row.attributes) != _store_location_traits(staged.attributes)
                ):
                    affected_stores.add(staged.code)
                row.name, row.active, row.attributes = staged.name, staged.active, staged.attributes
                row.updated_by, row.updated_at = actor, utcnow()
                counts["updated"] += 1
        last_id = rows[-1].id
        await db.flush()


def _store_location_traits(attributes) -> object:
    """Read the Store attribute that determines total-definition eligibility."""
    return attributes.get("location_traits") if isinstance(attributes, dict) else None


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

    await lock_configuration(db, exclusive=True)
    affected_stores = await _apply_staged_rows(db, sync_id, actor, counts)

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
    ).values(active=False, updated_by=actor, updated_at=utcnow())
      .returning(FoundationReference.category, FoundationReference.code)
      .execution_options(synchronize_session=False))
    deactivated = result.all()
    counts["deactivated"] = len(deactivated)
    affected_stores.update(code for category, code in deactivated if category == "Store")
    if affected_stores:
        await invalidate_configuration_locked(
            db,
            actor=actor,
            reason="Shopify Store references changed",
            stores=affected_stores,
        )
    await db.execute(delete(ShopifyReferenceStage).where(ShopifyReferenceStage.sync_id == sync_id))
    state.applied_generation = generation
    return counts

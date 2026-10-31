"""Sync Shopify locations and products into the retail foundation data (Store and Item references)."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import FoundationReference, utcnow

SOURCE = "Shopify"


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


async def collect(adapter, store_code: str) -> list[dict]:
    wanted = [shop_to_store(await adapter.fetch_shop(), store_code)]
    wanted += [location_to_store(loc) for loc in await adapter.fetch_locations()]
    async for product in adapter.fetch_products():
        wanted += product_to_items(product)
    return wanted


async def apply(db: AsyncSession, wanted: list[dict], actor: str) -> dict:
    """Upsert the wanted references. Hand-entered records are never overwritten, and Shopify records that
    disappeared from the shop are deactivated rather than deleted."""
    existing = {(r.category, r.code): r for r in (await db.execute(select(FoundationReference).where(
        FoundationReference.category.in_(["Store", "Item"])))).scalars().all()}
    counts = {"created": 0, "updated": 0, "deactivated": 0, "skipped_manual": 0, "duplicates": 0}
    seen: set[tuple[str, str]] = set()
    for ref in wanted:
        key = (ref["category"], ref["code"])
        if key in seen:
            counts["duplicates"] += 1  # two variants sharing a SKU; the first one wins
            continue
        seen.add(key)
        row = existing.get(key)
        if row is None:
            db.add(FoundationReference(id=str(uuid.uuid4()), updated_by=actor, **ref))
            counts["created"] += 1
        elif (row.attributes or {}).get("source") != SOURCE:
            counts["skipped_manual"] += 1
        elif (row.name, row.active, row.attributes) != (ref["name"], ref["active"], ref["attributes"]):
            row.name, row.active, row.attributes, row.updated_by, row.updated_at = (
                ref["name"], ref["active"], ref["attributes"], actor, utcnow())
            counts["updated"] += 1
    for key, row in existing.items():
        if key not in seen and row.active and (row.attributes or {}).get("source") == SOURCE:
            row.active, row.updated_by, row.updated_at = False, actor, utcnow()
            counts["deactivated"] += 1
    await db.commit()
    return counts

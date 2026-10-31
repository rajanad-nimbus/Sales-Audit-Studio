"""REST endpoints for Shopify integration."""
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest import service as ingest
from apps.ingest.shopify import adapter as shopify_adapter
from apps.ingest.limits import MAX_DELIVERY_BYTES
from apps.ingest import source_settings
from apps.ingest.shopify import reference_sync
from apps.request_body import read_limited_body
from apps.security.authentication import require_role
from database import async_session, get_db
from model_base import utcnow
from apps.ingest.models import SourceRecord
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/ingest/shopify")
logger = logging.getLogger(__name__)


class ShopifySyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at_min: datetime | None = None
    created_at_max: datetime | None = None

    @model_validator(mode="after")
    def validate_date_range(self):
        start, end = self.created_at_min, self.created_at_max
        for field, value in (("created_at_min", start), ("created_at_max", end)):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field} must include a timezone")
        if start is not None and end is not None:
            if start >= end:
                raise ValueError("created_at_min must be before created_at_max")
            if end - start > timedelta(days=90):
                raise ValueError("Shopify sync range cannot exceed 90 days")
        return self


class ShopifySyncResponse(BaseModel):
    status: str
    received: int
    archived: int
    quarantined: int
    canonical_created: int
    errors: list[str] = []


@router.post("/sync")
async def sync_shopify(
    req: ShopifySyncRequest,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "finance")),
) -> ShopifySyncResponse:
    """Fetch orders and refunds from Shopify and ingest them."""
    await source_settings.require_allowed(db, "Shopify", manual=True)
    errors = []
    store_id = os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN")

    try:
        feed = await shopify_adapter.fetch_shopify_feed(
            store_id,
            created_at_min=req.created_at_min,
            created_at_max=req.created_at_max,
        )
    except shopify_adapter.ShopifyFeedLimitError as e:
        raise HTTPException(status_code=413, detail=shopify_adapter.safe_failure_message(e)) from e
    except shopify_adapter.ShopifyAPIError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch from Shopify: {shopify_adapter.safe_failure_message(e)}",
        ) from e

    setting = await source_settings.lock_source_settings(db, "Shopify", shared=True)
    state = source_settings.effective("Shopify", setting)
    if not state["enabled"]:
        raise HTTPException(status_code=409, detail="Shopify was switched off while the sync was fetching data")
    if not state["manual_sync"]:
        raise HTTPException(status_code=409, detail="Manual sync was switched off while Shopify was fetching data")

    delivery_id = f"SHOPIFY-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"

    try:
        stats = await ingest.ingest(db, "Shopify", delivery_id, feed.get("Shopify", []))
        audit(db, "SHOPIFY_SYNC_COMPLETED", None, "ShopifySync", delivery_id,
              f"Shopify sync for store {store_id}: {stats['canonical_created']} transactions created",
              actor="shopify-connector")
        await db.commit()
    except shopify_adapter.ShopifyAPIError as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {shopify_adapter.safe_failure_message(e)}",
        ) from e
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Ingestion failed because the database rejected the Shopify delivery",
        ) from e

    return ShopifySyncResponse(
        status="success",
        received=stats["received"],
        archived=stats["archived"],
        quarantined=stats["quarantined"],
        canonical_created=stats["canonical_created"],
        errors=errors,
    )


@router.post("/sync-reference")
async def sync_reference(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "finance")),
):
    """Sync Shopify locations (Stores) and product variants (Items) into the retail foundation data."""
    await source_settings.require_allowed(db, "Shopify")
    store_code = os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN")
    sync_id = str(uuid.uuid4())
    adapter = shopify_adapter.ShopifyAdapter()
    try:
        async with async_session() as staging_db:
            staged = await reference_sync.stage(adapter, staging_db, sync_id, store_code)
    except shopify_adapter.ShopifyAPIError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch from Shopify: {shopify_adapter.safe_failure_message(e)}",
        ) from e
    finally:
        await adapter.close()
    actor = user.get("user") or "shopify-connector"
    try:
        counts = await reference_sync.apply(
            db, sync_id, staged["generation"], actor, staged["duplicates"]
        )
        if counts["superseded"]:
            event_type = "SHOPIFY_REFERENCE_SYNC_SUPERSEDED"
            description = f"Shopify reference sync generation {staged['generation']} was superseded"
        else:
            event_type = "SHOPIFY_REFERENCE_SYNCED"
            description = (
                f"Shopify reference sync: {counts['created']} created, {counts['updated']} updated, "
                f"{counts['deactivated']} deactivated"
            )
        audit(db, event_type, None, "ShopifySync", store_code, description, actor=actor)
        await db.commit()
    except Exception:
        await db.rollback()
        try:
            async with async_session() as cleanup_db:
                await reference_sync.discard(cleanup_db, sync_id)
                await cleanup_db.commit()
        except Exception:
            logger.exception("Could not discard failed Shopify reference stage %s", sync_id)
        raise
    return {"status": "superseded" if counts["superseded"] else "success", **staged, **counts}


WEBHOOK_TOPICS = {"orders/create", "orders/updated", "refunds/create"}


@router.post("/webhook")
async def shopify_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Verify and ingest a Shopify webhook delivery (authenticated by HMAC, not a user token)."""
    body = await read_limited_body(request, MAX_DELIVERY_BYTES)
    signature = request.headers.get("X-Shopify-Hmac-SHA256", "")
    topic = request.headers.get("X-Shopify-Topic", "unknown")

    if not shopify_adapter.verify_webhook_signature(os.getenv("SHOPIFY_WEBHOOK_SECRET"), body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if not (await source_settings.load(db))["Shopify"]["enabled"]:
        # Acknowledge so Shopify does not keep retrying (and eventually drop the webhook), but ingest nothing.
        return {"status": "ignored", "reason": "Shopify is switched off in Nimbus", "topic": topic}

    try:
        payload = json.loads(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

    if topic not in WEBHOOK_TOPICS:
        return {"status": "ignored", "topic": topic}

    store_id = os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN")
    try:
        if topic == "refunds/create":
            record = shopify_adapter.normalize_refund(payload, payload["order_id"], None, store_id)
        else:
            record = shopify_adapter.normalize_order(payload, store_id)
    except (KeyError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Malformed {topic} payload: {e}")

    delivery_id = request.headers.get("X-Shopify-Webhook-Id")
    if not delivery_id:
        delivery_id = f"SHOPIFY-WH-{hashlib.sha256(body).hexdigest()}"
    elif len(delivery_id) > 255:
        delivery_id = f"SHOPIFY-WH-ID-{hashlib.sha256(delivery_id.encode()).hexdigest()}"
    audit_id = delivery_id if len(delivery_id) <= 36 else str(uuid.uuid5(uuid.NAMESPACE_URL, delivery_id))
    try:
        stats = await ingest.ingest(db, "Shopify", delivery_id, [record])
        audit(db, "SHOPIFY_WEBHOOK_RECEIVED", None, "ShopifyWebhook", audit_id,
              f"Webhook {topic} for {record['source_record_id']}: {stats['canonical_created']} created, "
              f"{stats['quarantined']} quarantined", actor="shopify-webhook")
        await db.commit()
    except shopify_adapter.ShopifyAPIError as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Webhook processing failed: {shopify_adapter.safe_failure_message(e)}",
        ) from e
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Webhook processing failed because the database rejected the delivery",
        ) from e

    return {"status": "received", "topic": topic, **stats}


STALE_AFTER_HOURS = 26


@router.get("/status")
async def shopify_status(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    """Get Shopify connector health and last sync status."""
    last_sync = (await db.execute(select(func.max(SourceRecord.extracted_at)).where(
        SourceRecord.source_system == "Shopify", SourceRecord.status == "Processed"))).scalar()
    last_received = (await db.execute(select(func.max(SourceRecord.received_at)).where(
        SourceRecord.source_system == "Shopify"))).scalar()
    quarantined = (await db.execute(select(func.count()).select_from(SourceRecord).where(
        SourceRecord.source_system == "Shopify", SourceRecord.status == "Quarantined"))).scalar()

    hours = (utcnow() - last_sync).total_seconds() / 3600 if last_sync else None
    status = "no_syncs" if last_sync is None else "stale" if hours > STALE_AFTER_HOURS else "healthy"
    return {
        "connector": "shopify",
        "status": status,
        "last_sync_timestamp": last_sync,
        "last_received_timestamp": last_received,
        "quarantined_records": quarantined,
        "hours_since_last_sync": hours,
    }

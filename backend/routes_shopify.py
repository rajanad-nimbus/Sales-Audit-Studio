"""REST endpoints for Shopify integration."""
import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import ingest
import shopify_adapter
from auth import require_role
from database import get_db
from models import SourceRecord, utcnow
from nimbus import audit

router = APIRouter(prefix="/api/ingest/shopify")


class ShopifySyncRequest(BaseModel):
    store_id: str | None = None  # defaults to SHOPIFY_STORE_ID
    created_at_min: datetime | None = None
    created_at_max: datetime | None = None


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
    errors = []
    store_id = req.store_id or os.getenv("SHOPIFY_STORE_ID", "SHOPIFY-MAIN")

    try:
        feed = await shopify_adapter.fetch_shopify_feed(
            store_id,
            created_at_min=req.created_at_min,
            created_at_max=req.created_at_max,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch from Shopify: {str(e)}")

    delivery_id = f"SHOPIFY-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"

    try:
        stats = await ingest.ingest(db, "Shopify", delivery_id, feed.get("Shopify", []))
        audit(db, "SHOPIFY_SYNC_COMPLETED", None, "ShopifySync", delivery_id,
              f"Shopify sync for store {store_id}: {stats['canonical_created']} transactions created",
              actor="shopify-connector")
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

    return ShopifySyncResponse(
        status="success",
        received=stats["received"],
        archived=stats["archived"],
        quarantined=stats["quarantined"],
        canonical_created=stats["canonical_created"],
        errors=errors,
    )


WEBHOOK_TOPICS = {"orders/create", "orders/updated", "refunds/create"}


@router.post("/webhook")
async def shopify_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Verify and ingest a Shopify webhook delivery (authenticated by HMAC, not a user token)."""
    body = await request.body()
    signature = request.headers.get("X-Shopify-Hmac-SHA256", "")
    topic = request.headers.get("X-Shopify-Topic", "unknown")

    if not shopify_adapter.verify_webhook_signature(os.getenv("SHOPIFY_WEBHOOK_SECRET"), body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

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

    delivery_id = (request.headers.get("X-Shopify-Webhook-Id") or f"SHOPIFY-WH-{uuid.uuid4().hex[:12]}")[:36]  # audit object_id is 36 chars
    try:
        stats = await ingest.ingest(db, "Shopify", delivery_id, [record])
        audit(db, "SHOPIFY_WEBHOOK_RECEIVED", None, "ShopifyWebhook", delivery_id,
              f"Webhook {topic} for {record['source_record_id']}: {stats['canonical_created']} created, "
              f"{stats['quarantined']} quarantined", actor="shopify-webhook")
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

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

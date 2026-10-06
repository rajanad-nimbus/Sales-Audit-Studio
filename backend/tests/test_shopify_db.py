"""DB-backed tests for Shopify ingestion, reconciliation and routes (in-memory SQLite)."""
import asyncio
import base64
import hashlib
import hmac
import json

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import ingest
import routes_shopify
from auth import require_role
from database import get_db
from models import Base, Case, SourceRecord


def run(coro):
    return asyncio.run(coro)


async def make_session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def order(i="1", amount="100.00", status="paid"):
    return {"source_record_id": f"SHOP-{i}", "record_type": "Order", "store_id": "S1",
            "event_time": "2024-01-15T10:00:00Z", "amount": amount, "currency": "USD",
            "payment_reference": i, "financial_status": status}


def refund(rid, order_id="1", amount="40.00", day="2024-01-16"):
    return {"source_record_id": f"SHREF-{rid}", "record_type": "Refund", "store_id": "S1",
            "event_time": f"{day}T10:00:00Z", "amount": amount, "currency": "USD",
            "payment_reference": order_id, "order_id": order_id, "refund_id": rid}


async def case_types(db, date=None):
    await ingest.reconcile(db, date)
    await db.commit()
    return sorted((await db.execute(select(Case.case_type))).scalars().all())


def test_resync_with_changed_status_is_skipped_not_quarantined():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d1", [order(status="paid")])
            stats = await ingest.ingest(db, "Shopify", "d2", [order(status="refunded")])
            assert stats["duplicates_skipped"] == 1 and stats["quarantined"] == 0
            stats = await ingest.ingest(db, "Shopify", "d3", [order(amount="999.00")])
            assert stats["quarantined"] == 1
    run(go())


def test_partial_refund_flagged():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d", [order(), refund("r1", amount="40.00")])
            assert await case_types(db) == ["Shopify Partial Refund"]
    run(go())


def test_multiple_refunds_are_summed_to_full_and_match():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d", [order(), refund("r1", amount="40.00"), refund("r2", amount="60.00")])
            assert await case_types(db) == []
    run(go())


def test_overage_and_unmatched_refund():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d", [order(), refund("r1", amount="120.00"),
                                                      refund("r2", order_id="404", amount="5.00")])
            assert await case_types(db) == ["Shopify Overage Refund", "Shopify Unmatched Refund"]
    run(go())


def test_refund_matches_order_from_earlier_business_date():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d1", [order()])
            await ingest.reconcile(db, "2024-01-15")
            await db.commit()
            await ingest.ingest(db, "Shopify", "d2", [refund("r1", amount="100.00", day="2024-01-17")])
            assert await case_types(db, "2024-01-17") == []
    run(go())


def webhook_client(Session):
    app = FastAPI()
    app.include_router(routes_shopify.router)

    async def get_test_db():
        async with Session() as db:
            yield db
    app.dependency_overrides[get_db] = get_test_db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_webhook_signature_and_ingest(monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "s3cret")
    body = json.dumps({"id": 77, "created_at": "2024-01-15T10:00:00Z", "total_price": "10.00",
                       "currency": "USD", "financial_status": "paid"}).encode()
    sig = base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()

    async def go():
        Session = await make_session()
        async with webhook_client(Session) as c:
            url = "/api/ingest/shopify/webhook"
            bad = await c.post(url, content=body, headers={"X-Shopify-Hmac-SHA256": "nope", "X-Shopify-Topic": "orders/create"})
            assert bad.status_code == 401
            ok = await c.post(url, content=body, headers={"X-Shopify-Hmac-SHA256": sig, "X-Shopify-Topic": "orders/create"})
            assert ok.status_code == 200 and ok.json()["canonical_created"] == 1
            ign = await c.post(url, content=body, headers={"X-Shopify-Hmac-SHA256": sig, "X-Shopify-Topic": "products/update"})
            assert ign.json()["status"] == "ignored"
        async with Session() as db:
            assert len((await db.execute(select(SourceRecord))).scalars().all()) == 1
    run(go())


def test_paid_order_without_refund_is_closed_but_pending_stays_open():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d", [order("1", status="paid"), order("2", status="pending")])
            assert await case_types(db) == []
            from models import CanonicalTransaction as T
            rows = {t.payment_reference: t.reconciliation_status
                    for t in (await db.execute(select(T))).scalars().all()}
            assert rows == {"1": "Matched", "2": "Unmatched"}
    run(go())


def test_refund_after_closed_order_reopens_it_as_exception():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d1", [order()])
            await ingest.reconcile(db, "2024-01-15")
            await db.commit()
            await ingest.ingest(db, "Shopify", "d2", [refund("r1", amount="40.00")])
            assert await case_types(db, "2024-01-16") == ["Shopify Partial Refund"]
    run(go())


def test_audit_object_ids_fit_column(monkeypatch):
    """audit_events.object_id is String(36); SQLite won't enforce it, so check explicitly."""
    from models import AuditEvent

    async def go():
        Session = await make_session()
        async with Session() as db:
            routes_shopify.audit(db, "X", None, "ShopifySync", "SHOPIFY-20240115T101500Z-ab12cd", "d")
            await db.commit()
    run(go())
    assert len("SHOPIFY-20240115T101500Z-ab12cd") <= AuditEvent.__table__.c.object_id.type.length

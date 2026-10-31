"""DB-backed tests for Shopify ingestion, reconciliation and routes (in-memory SQLite)."""
import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest import reconciliation, service as ingest
from apps.ingest.shopify import routes as routes_shopify
from database import get_db
from models import Base, CanonicalTransaction, Case, SourceRecord
from pydantic import ValidationError

_engines = []


def run(coro):
    async def with_cleanup():
        try:
            return await coro
        finally:
            while _engines:
                await _engines.pop().dispose()
    return asyncio.run(with_cleanup())


async def make_session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    _engines.append(engine)
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


def test_manual_sync_request_cannot_override_configured_store_identity():
    with pytest.raises(ValidationError, match="store_id"):
        routes_shopify.ShopifySyncRequest(store_id="ANOTHER-STORE")


@pytest.mark.parametrize(
    ("start", "end", "error"),
    [
        ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z", "must be before"),
        ("2026-01-01T00:00:00", "2026-01-02T00:00:00Z", "must include a timezone"),
        ("2026-01-01T00:00:00Z", "2026-04-02T00:00:00Z", "cannot exceed 90 days"),
    ],
)
def test_manual_sync_rejects_invalid_date_ranges(start, end, error):
    with pytest.raises(ValidationError, match=error):
        routes_shopify.ShopifySyncRequest(created_at_min=start, created_at_max=end)


def test_manual_sync_accepts_exactly_ninety_days():
    request = routes_shopify.ShopifySyncRequest(
        created_at_min="2026-01-01T00:00:00Z",
        created_at_max="2026-04-01T00:00:00Z",
    )
    assert request.created_at_max - request.created_at_min == timedelta(days=90)


def test_manual_sync_fetches_and_stamps_configured_store(monkeypatch):
    class FakeDb:
        def add(self, _row):
            pass

        async def commit(self):
            pass

    seen = {}

    async def fetch(store_id, **_kwargs):
        seen["store_id"] = store_id
        return {"Shopify": []}

    async def ingest_records(_db, _source, _delivery_id, records):
        seen["record_store_ids"] = [record["store_id"] for record in records]
        return {"received": 0, "archived": 0, "quarantined": 0, "canonical_created": 0}

    async def allowed(*_args, **_kwargs):
        return None

    monkeypatch.setenv("SHOPIFY_STORE_ID", "CONFIGURED-STORE")
    monkeypatch.setattr(routes_shopify.shopify_adapter, "fetch_shopify_feed", fetch)
    monkeypatch.setattr(routes_shopify.ingest, "ingest", ingest_records)
    monkeypatch.setattr(routes_shopify.source_settings, "require_allowed", allowed)

    result = asyncio.run(routes_shopify.sync_shopify(
        routes_shopify.ShopifySyncRequest(), FakeDb(), {"user": "finance"}
    ))
    assert result.status == "success"
    assert seen == {"store_id": "CONFIGURED-STORE", "record_store_ids": []}


async def case_types(db, date=None):
    await reconciliation.reconcile(db, date)
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


def test_large_refund_history_is_summed_across_bounded_pages(monkeypatch):
    monkeypatch.setattr(reconciliation, "SETTLEMENT_QUERY_PAGE_SIZE", 17)

    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "large-refunds", [
                order(amount="10.01"),
                *(refund(f"r{index}", amount="0.01") for index in range(1001)),
            ])
            assert await case_types(db) == []

    run(go())


def test_refund_reconciliation_batches_order_and_history_queries():
    async def go():
        Session = await make_session()
        async with Session() as db:
            records = []
            for index in range(12):
                reference = f"ORDER-{index}"
                records.extend((
                    order(i=reference, amount="1.00"),
                    refund(f"refund-{index}", order_id=reference, amount="1.00"),
                ))
            await ingest.ingest(db, "Shopify", "many-orders", records)
            transactions = (await db.execute(select(CanonicalTransaction).where(
                CanonicalTransaction.reconciliation_status == "Unmatched"
            ))).scalars().all()

            statements = []

            def count_transaction_queries(_connection, _cursor, statement, _parameters, _context, _executemany):
                if "from canonical_transactions" in statement.lower():
                    statements.append(statement)

            event.listen(_engines[-1].sync_engine, "before_cursor_execute", count_transaction_queries)
            findings = []
            try:
                await reconciliation.reconcile_shopify(db, transactions, lambda *finding: findings.append(finding))
            finally:
                event.remove(_engines[-1].sync_engine, "before_cursor_execute", count_transaction_queries)

            assert findings == []
            assert all(row.reconciliation_status == "Matched" for row in transactions)
            assert len(statements) <= 6

    run(go())


def test_refund_reconciliation_chooses_earliest_order_by_event_time_not_id():
    async def go():
        Session = await make_session()
        async with Session() as db:
            early = datetime(2024, 1, 15, 10, tzinfo=timezone.utc)
            late = datetime(2024, 1, 15, 11, tzinfo=timezone.utc)
            shared = {
                "business_date": "2024-01-15", "transaction_date": early,
                "processing_timestamp": late, "settlement_date": "2024-01-15",
                "transaction_type": "Order", "source_lineage": "shopify-map-v1",
                "currency": "USD", "store_id": "S1", "channel_id": "online",
                "payment_reference": "order-1", "reconciliation_status": "Unmatched",
                "disposition": "Open",
            }
            earliest_order = CanonicalTransaction(
                id="z-earliest", event_timestamp=early, signed_amount=100, **shared,
            )
            later_order = CanonicalTransaction(
                id="a-later", event_timestamp=late, signed_amount=200, **shared,
            )
            refund_row = CanonicalTransaction(
                id="refund", business_date="2024-01-16", transaction_date=late,
                event_timestamp=late, processing_timestamp=late, settlement_date="2024-01-16",
                transaction_type="Refund", source_lineage="shopify-map-v1", currency="USD",
                signed_amount=-100, store_id="S1", channel_id="online", payment_reference="order-1",
                reconciliation_status="Unmatched", disposition="Open",
            )
            db.add_all([earliest_order, later_order, refund_row])
            await db.commit()

            findings = []
            await reconciliation.reconcile_shopify(db, [refund_row], lambda *finding: findings.append(finding))

            assert findings == []
            assert earliest_order.reconciliation_status == "Matched"
            assert refund_row.reconciliation_status == "Matched"
            assert later_order.reconciliation_status == "Unmatched"

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
            await reconciliation.reconcile(db, "2024-01-15")
            await db.commit()
            await ingest.ingest(db, "Shopify", "d2", [refund("r1", amount="100.00", day="2024-01-17")])
            assert await case_types(db, "2024-01-17") == []
    run(go())


def test_later_refund_includes_earlier_refund_amount():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d1", [order(), refund("r1", amount="40.00")])
            assert await case_types(db, "2024-01-16") == ["Shopify Partial Refund"]
            await ingest.ingest(db, "Shopify", "d2", [refund("r2", amount="60.00", day="2024-01-17")])
            await reconciliation.reconcile(db, "2024-01-17")
            await db.commit()
            rows = (await db.execute(select(CanonicalTransaction).where(
                CanonicalTransaction.business_date == "2024-01-17"))).scalars().all()
            assert [row.reconciliation_status for row in rows] == ["Matched"]
            assert await case_types(db) == ["Shopify Partial Refund"]
    run(go())


def test_late_refund_does_not_overwrite_manually_matched_order():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d1", [order()])
            order_row = (await db.execute(select(CanonicalTransaction))).scalar_one()
            order_row.reconciliation_status, order_row.disposition = "Manually Matched", "Closed"
            await db.commit()
            await ingest.ingest(db, "Shopify", "d2", [refund("r1", amount="100.00", day="2024-01-17")])
            assert await case_types(db, "2024-01-17") == ["Shopify Refund Requires Manual Review"]
            await db.refresh(order_row)
            assert (order_row.reconciliation_status, order_row.disposition) == ("Manually Matched", "Closed")
    run(go())


def test_paid_order_ignores_other_store_or_currency_refunds_with_same_reference():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "delivery", [order(),
                {**refund("other-store"), "store_id": "S2"},
                {**refund("other-currency"), "currency": "EUR"}])
            await reconciliation.reconcile(db, "2024-01-15")
            order_row = (await db.execute(select(CanonicalTransaction).where(
                CanonicalTransaction.transaction_type == "Order"))).scalar_one()
            assert order_row.reconciliation_status == "Matched"
    run(go())


def test_manual_refund_without_reference_does_not_match_unrelated_order():
    async def go():
        Session = await make_session()
        async with Session() as db:
            from apps.corrections.routes import approve_adjustment
            from models import TransactionAdjustment
            for identifier, kind in (("order", "Order"), ("refund", "Refund")):
                db.add(TransactionAdjustment(id=identifier, adjustment_type="MissingTransaction", status="Proposed",
                    proposed_values={"business_date": "2024-01-15", "store_id": "S1", "amount": "10",
                                     "transaction_type": kind, "source_system": "Shopify"},
                    rationale="test", proposed_by="analyst"))
            await db.commit()
            await approve_adjustment("order", db, {"user": "finance"})
            await approve_adjustment("refund", db, {"user": "finance"})
            assert await case_types(db, "2024-01-15") == ["Shopify Unmatched Refund"]
            rows = (await db.execute(select(CanonicalTransaction))).scalars().all()
            assert next(row for row in rows if row.transaction_type == "Order").reconciliation_status == "Unmatched"
            assert next(row for row in rows if row.transaction_type == "Refund").reconciliation_status == "Exception"
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


def test_webhook_rejects_request_body_over_limit(monkeypatch):
    monkeypatch.setattr(routes_shopify, "MAX_DELIVERY_BYTES", 4)

    async def go():
        Session = await make_session()
        async with webhook_client(Session) as client:
            response = await client.post(
                "/api/ingest/shopify/webhook",
                content=b"12345",
                headers={"X-Shopify-Hmac-SHA256": "unused", "X-Shopify-Topic": "orders/create"},
            )
            assert response.status_code == 413
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


def test_paid_order_closure_batches_large_source_record_lookup():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "large-delivery", [
                order(str(index)) for index in range(1001)
            ])
            rows = (await db.execute(select(CanonicalTransaction).where(
                CanonicalTransaction.transaction_type == "Order"
            ))).scalars().all()
            await reconciliation.close_paid_orders(db, rows)
            assert len(rows) == 1001
            assert all((row.reconciliation_status, row.disposition) == ("Matched", "Closed")
                       for row in rows)
    run(go())


def test_refund_after_closed_order_reopens_it_as_exception():
    async def go():
        Session = await make_session()
        async with Session() as db:
            await ingest.ingest(db, "Shopify", "d1", [order()])
            await reconciliation.reconcile(db, "2024-01-15")
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

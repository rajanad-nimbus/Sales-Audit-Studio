import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest import service
from models import Base, Exception as DBException, ReconciliationPolicy


def sale(rid, amount="113.00", **extra):
    return {"source_record_id": rid, "event_time": "2026-10-01T10:00:00Z", "record_type": "Sale", "amount": amount,
            "store_id": "S1", "payment_reference": f"PAY-{rid}", **extra}


def run_reconcile(pos=(), manifest=None, policy=None):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                if policy:
                    db.add(ReconciliationPolicy(id="p1", name="p", active=True, **policy))
                await service.ingest(db, "POS", "d1", list(pos))
                if manifest:
                    await service.ingest(db, "POSControl", "d2", [manifest])
                await service.reconcile(db)
                return sorted((await db.execute(select(DBException.exception_type))).scalars().all())
        finally:
            await engine.dispose()
    return asyncio.run(go())


LINE = {"item_code": "A", "quantity": "1", "unit_price": "100.00", "tax_amount": "13.00"}


def test_out_of_balance_transaction_raises_tran_out_bal():
    types = run_reconcile([sale("T1", lines=[LINE], tenders=[{"tender_type": "Cash", "amount": "100.00"}])])
    assert "TRAN_OUT_BAL" in types


def test_balanced_transaction_does_not_raise_tran_out_bal():
    types = run_reconcile([sale("T1", lines=[LINE], tenders=[{"tender_type": "Cash", "amount": "113.00"}])])
    assert "TRAN_OUT_BAL" not in types


def test_manual_discount_without_reason_raises_disc_reason_req():
    base = {"lines": [LINE], "tenders": [{"tender_type": "Cash", "amount": "103.00"}]}
    missing = run_reconcile([sale("T1", **base, discounts=[{"kind": "Manual", "amount": "10.00"}])])
    given = run_reconcile([sale("T1", **base, discounts=[{"kind": "Manual", "amount": "10.00", "description": "Damaged"}])])
    assert "DISC_REASON_REQ" in missing and "DISC_REASON_REQ" not in given


def manifest(declared):
    return {"source_record_id": "M1", "business_date": "2026-10-01", "store_id": "S1",
            "declared_count": declared, "declared_total": "113.00"}


def test_large_gap_raises_big_gap_as_well_as_incomplete_feed():
    big = run_reconcile([sale("T1")], manifest(50))
    small = run_reconcile([sale("T1")], manifest(2))
    assert "MISSING_TRAN_BIG_GAP" in big and "INCOMPLETE_FEED" in big
    assert "INCOMPLETE_FEED" in small and "MISSING_TRAN_BIG_GAP" not in small


def test_gap_thresholds_come_from_the_reconciliation_policy():
    strict = run_reconcile([sale("T1")], manifest(2), policy={"gap_min_count": 1, "gap_share": 0.1})
    lenient = run_reconcile([sale("T1")], manifest(50), policy={"gap_min_count": 100, "gap_share": 1})
    assert "MISSING_TRAN_BIG_GAP" in strict and "MISSING_TRAN_BIG_GAP" not in lenient

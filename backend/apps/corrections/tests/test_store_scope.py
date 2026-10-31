import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.corrections import routes
from apps.ingest.service import ingest
from apps.transactions import routes as transactions_routes
from models import Base, CanonicalTransaction, TransactionAdjustment
from sqlalchemy import select


def test_correction_endpoints_enforce_store_scope_before_limits_and_writes():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {"record_type": "Sale", "event_time": "2026-10-01T10:00:00Z", "amount": "10"}
                await ingest(db, "POS", "delivery", [
                    {**common, "source_record_id": "one", "store_id": "S1", "payment_reference": "common"},
                    {**common, "source_record_id": "two", "store_id": "S2", "payment_reference": "common"},
                ])
                transactions = (await db.execute(select(CanonicalTransaction))).scalars().all()
                first, second = sorted(transactions, key=lambda row: row.store_id)
                db.add_all([
                    TransactionAdjustment(id="a", transaction_id=first.id, adjustment_type="Amount", status="Proposed",
                                          proposed_values={"amount_delta": "1"}, rationale="test", proposed_by="user"),
                    TransactionAdjustment(id="b", transaction_id=second.id, adjustment_type="Amount", status="Proposed",
                                          proposed_values={"amount_delta": "1"}, rationale="test", proposed_by="user"),
                    TransactionAdjustment(id="c", adjustment_type="MissingTransaction", status="Proposed",
                                          proposed_values={"store_id": "S2", "business_date": "2026-10-01", "amount": "1",
                                                           "transaction_type": "Sale"}, rationale="test", proposed_by="user"),
                ])
                await db.commit()
                user = {"user": "scoped", "store_scoped": True, "stores": ["S1"]}
                assert [row["store_id"] for row in await routes.search_transactions("common", 12, db, user)] == ["S1"]
                assert await routes.search_transactions("__", 12, db, user) == []
                assert (await routes.transaction_facets(db, user))["stores"] == ["S1"]
                listing = await transactions_routes.list_transactions(db=db, _=user)
                assert listing["total"] == 1 and listing["net_amount"] == "10.00"
                assert listing["by_status"] == {"Unmatched": 1}
                assert (await transactions_routes.facets(db, user))["stores"] == ["S1"]
                assert (await transactions_routes.transaction_detail(first.id, db, user))["linked_transactions"] == []
                adjustment_page = await routes.list_adjustments(None, None, db, user)
                assert [row["id"] for row in adjustment_page["items"]] == ["a"]
                for operation in (
                    routes.get_transaction(second.id, db, user),
                    transactions_routes.transaction_detail(second.id, db, user),
                    routes.approve_adjustment("b", db, user),
                    routes.approve_adjustment("c", db, user),
                    routes.reject_adjustment("c", db, user),
                    routes.propose_adjustment(routes.AdjustmentBody(transaction_id=second.id, adjustment_type="Amount",
                        proposed_values={"amount_delta": "1"}, rationale="test"), db, user),
                    routes.propose_adjustment(routes.AdjustmentBody(adjustment_type="MissingTransaction",
                        proposed_values={"store_id": "S2", "business_date": "2026-10-01", "amount": "1",
                                         "transaction_type": "Sale"}, rationale="test"), db, user),
                ):
                    with pytest.raises(HTTPException) as exc:
                        await operation
                    assert exc.value.status_code == 403
                assert (await db.get(TransactionAdjustment, "b")).status == "Proposed"
                assert (await db.get(TransactionAdjustment, "c")).status == "Proposed"
                empty = {"user": "empty", "store_scoped": True, "stores": []}
                assert (await routes.list_adjustments(None, None, db, empty))["items"] == []
        finally:
            await engine.dispose()
    asyncio.run(run())

"""Return linking should stop reading sales after resolving its pending page."""

import asyncio
from types import SimpleNamespace

from apps.ingest.service import link_return_page, return_candidate_query
from models import Base
from sqlalchemy.ext.asyncio import create_async_engine


class SaleStream:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.rows_seen = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            row = next(self.rows)
        except StopIteration:
            raise StopAsyncIteration from None
        self.rows_seen += 1
        return row

    async def close(self):
        self.closed = True


class SaleDatabase:
    def __init__(self, rows):
        self.stream_result = SaleStream(rows)

    async def stream(self, _query):
        return self.stream_result


def test_return_linking_stops_after_all_page_returns_find_their_earliest_sale():
    returns = [
        SimpleNamespace(store_id="S1", original_reference="PAY-1", original_transaction_id=None),
        SimpleNamespace(store_id="S1", original_reference="PAY-2", original_transaction_id=None),
    ]
    sales = [
        (SimpleNamespace(id="sale-1", store_id="S1", payment_reference="PAY-1"), None),
        (SimpleNamespace(id="sale-2", store_id="S1", payment_reference="PAY-2"), None),
        (SimpleNamespace(id="later-1", store_id="S1", payment_reference="PAY-1"), None),
        (SimpleNamespace(id="later-2", store_id="S1", payment_reference="PAY-2"), None),
    ]
    db = SaleDatabase(sales)

    linked = asyncio.run(link_return_page(db, returns))

    assert linked == 2
    assert [item.original_transaction_id for item in returns] == ["sale-1", "sale-2"]
    assert db.stream_result.rows_seen == 2
    assert db.stream_result.closed


def test_single_store_return_candidate_scan_uses_event_order_index():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                query = return_candidate_query({("S1", "PAY-1"), ("S1", "PAY-2")})
                sql = str(
                    query.compile(
                        dialect=connection.dialect,
                        compile_kwargs={"literal_binds": True},
                    )
                )
                plan = await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql)
                details = " ".join(row[3] for row in plan.fetchall())
                assert "ix_ct_store_type_event_id" in details
                assert "TEMP B-TREE FOR ORDER BY" not in details
        finally:
            await engine.dispose()

    asyncio.run(run())

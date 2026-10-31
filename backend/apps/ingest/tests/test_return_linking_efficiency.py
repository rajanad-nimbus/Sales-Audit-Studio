"""Return linking should stop reading sales after resolving its pending page."""

import asyncio
from types import SimpleNamespace

from apps.ingest.service import link_return_page


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
            raise StopAsyncIteration
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

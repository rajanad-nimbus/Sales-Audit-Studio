"""Transaction browsing: filters, status spread, raw source record and case links."""
import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import routes_transactions as rt
from models import AuditEvent, Base, CanonicalTransaction, Case, SourceRecord, TransactionAdjustment

NOW = datetime.now(timezone.utc)
USER = {"user": "x", "role": "finance"}


async def seed():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        def make(tid, source, store, bdate, typ, amount, status, pay=None, tender="Card", payload=b'{"k": 1}'):
            sr = SourceRecord(id="sr-" + tid, source_system=source, source_version="1", source_record_id="SRC-" + tid, delivery_id="D1",
                              payload=payload, payload_hash="h" + tid, received_at=NOW, source_event_time=NOW)
            tx = CanonicalTransaction(id=tid, business_date=bdate, transaction_date=NOW, event_timestamp=NOW, processing_timestamp=NOW,
                                      settlement_date=bdate, transaction_type=typ, source_lineage=source.lower(), signed_amount=Decimal(amount),
                                      store_id=store, channel_id="c", tender_type=tender, payment_reference=pay, reconciliation_status=status,
                                      source_record_id=sr.id)
            db.add_all([sr, tx])
        make("t1", "POS", "S1", "2026-10-01", "Sale", "100.00", "Matched", pay="PAY-1")
        make("t2", "Processor", "S1", "2026-10-01", "Sale", "100.00", "Matched", pay="PAY-1")
        make("t3", "POS", "S1", "2026-10-02", "Sale", "40.00", "Exception", pay="PAY-9", payload=b"not json")
        make("t4", "Bank", "S2", "2026-10-02", "Deposit", "-25.50", "Matched", tender="Cash")
        db.add(Case(id="c-cause", case_number="CASE-C", case_type="x", business_date="2026-10-02", store_id="S1"))
        db.add(Case(id="c-ctx", case_number="CASE-X", case_type="y", business_date="2026-10-02", store_id="S1"))
        await db.flush()
        db.add(AuditEvent(id=str(uuid.uuid4()), event_type="CASE_CREATED", actor="sys", object_type="Case", object_id="c-cause", case_id="c-cause",
                          action_description="x", after_state={"transaction_ids": ["t3"]}))
        db.add(TransactionAdjustment(id="a1", transaction_id="t3", adjustment_type="Amount", proposed_values={}, rationale="r", proposed_by="p"))
        await db.commit()
    return Session


def run(c):
    return asyncio.run(c)


def ids(out):
    return [i["id"] for i in out["items"]]


def test_filters_search_sort_and_status_spread():
    async def go():
        Session = await seed()
        async with Session() as db:
            allrows = await rt.list_transactions(db=db, _=USER)
            assert allrows["total"] == 4 and allrows["by_status"] == {"Matched": 3, "Exception": 1}
            assert allrows["net_amount"] == "214.50"
            assert ids(await rt.list_transactions(store_id="S1", source_system="POS", db=db, _=USER)) == ["t3", "t1"] or \
                set(ids(await rt.list_transactions(store_id="S1", source_system="POS", db=db, _=USER))) == {"t1", "t3"}
            by_status = await rt.list_transactions(status="Exception", db=db, _=USER)
            assert ids(by_status) == ["t3"]
            assert by_status["by_status"] == {"Matched": 3, "Exception": 1}      # the tiles keep showing the whole spread
            assert ids(await rt.list_transactions(q="PAY-1", db=db, _=USER)) and set(ids(await rt.list_transactions(q="PAY-1", db=db, _=USER))) == {"t1", "t2"}
            assert ids(await rt.list_transactions(q="SRC-t4", db=db, _=USER)) == ["t4"]         # matches the source record id too
            assert set(ids(await rt.list_transactions(date_from="2026-10-02", date_to="2026-10-02", db=db, _=USER))) == {"t3", "t4"}
            assert ids(await rt.list_transactions(sort="amount", dir="asc", db=db, _=USER))[0] == "t4"
            paged = await rt.list_transactions(page=2, page_size=3, db=db, _=USER)
            assert paged["pages"] == 2 and len(paged["items"]) == 1
    run(go())


def test_facets_lists_what_exists():
    async def go():
        Session = await seed()
        async with Session() as db:
            f = await rt.facets(db=db, _=USER)
            assert f["stores"] == ["S1", "S2"] and f["sources"] == ["Bank", "POS", "Processor"]
            assert f["statuses"] == ["Exception", "Matched"] and f["date_min"] == "2026-10-01" and f["date_max"] == "2026-10-02"
    run(go())


def test_detail_shows_source_record_cases_linked_transactions_and_adjustments():
    async def go():
        Session = await seed()
        async with Session() as db:
            d = await rt.transaction_detail("t3", db=db, _=USER)
            assert d["source"]["source_record_id"] == "SRC-t3" and d["source"]["payload"]["text"] == "not json"
            links = {c["case_number"]: c["link"] for c in d["cases"]}
            assert links == {"CASE-C": "raised from this transaction", "CASE-X": "same store and day"}
            assert d["cases"][0]["case_number"] == "CASE-C"                                   # causal first
            assert [a["id"] for a in d["adjustments"]] == ["a1"]
            t1 = await rt.transaction_detail("t1", db=db, _=USER)
            assert [t["id"] for t in t1["linked_transactions"]] == ["t2"] and t1["source"]["payload"]["text"].startswith("{")
            assert t1["cases"] == [] or all(c["link"] == "same store and day" for c in t1["cases"])
            with pytest.raises(HTTPException) as e:
                await rt.transaction_detail("nope", db=db, _=USER)
            assert e.value.status_code == 404
    run(go())

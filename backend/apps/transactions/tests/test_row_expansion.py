"""Opening a list row loads a small, bounded summary however large the transaction or the day."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from apps.transactions.detail_routes import transaction_brief
from apps.transactions.register_event_routes import register_event_context
from fastapi import HTTPException
from models import (
    Base,
    CanonicalTransaction,
    RegisterEvent,
    TransactionAdjustment,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}
NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)


def run_with_db(scenario):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(go())


def event(index, register="R1", store="S1", day="2026-10-01", minutes=None):
    return RegisterEvent(
        id=f"e{index:03d}",
        source_record_id=f"e{index}",
        source_format="x",
        store_id=store,
        business_date=day,
        register_id=register,
        event_time=NOW + timedelta(minutes=index if minutes is None else minutes),
        event_type="no_sale",
        transaction_number=str(index),
    )


def test_a_register_event_shows_the_events_either_side_in_order():
    async def scenario(db):
        db.add_all(event(n) for n in range(1, 8))
        await db.commit()
        context = await register_event_context("e004", db, FINANCE)
        assert [e["id"] for e in context["events"]] == [f"e{n:03d}" for n in range(1, 8)]
        assert context["event_id"] == "e004"
        assert context["earlier_not_shown"] is False and context["later_not_shown"] is False

    run_with_db(scenario)


def test_a_very_busy_register_returns_a_bounded_window_and_says_there_is_more():
    async def scenario(db):
        db.add_all(event(n) for n in range(1, 301))  # 300 events on one till in a day
        await db.commit()
        middle = await register_event_context("e150", db, FINANCE)
        ids = [e["id"] for e in middle["events"]]
        assert len(ids) == 15 and ids[7] == "e150"  # seven each side, the event in the middle
        assert ids == [f"e{n:03d}" for n in range(143, 158)]
        assert middle["earlier_not_shown"] and middle["later_not_shown"]
        first = await register_event_context("e001", db, FINANCE)
        assert len(first["events"]) == 8 and first["earlier_not_shown"] is False
        last = await register_event_context("e300", db, FINANCE)
        assert last["events"][-1]["id"] == "e300" and last["later_not_shown"] is False

    run_with_db(scenario)


def test_only_the_same_register_and_day_count_and_equal_times_keep_a_stable_order():
    async def scenario(db):
        db.add_all(
            [
                event(1),
                event(2),
                event(3, register="R2"),  # another till
                event(4, day="2026-10-02"),  # another day
                event(5, store="S2"),  # another store
                event(6, register=None),  # no register recorded
                event(7, minutes=2),  # the same minute as event 2
            ]
        )
        await db.commit()
        context = await register_event_context("e002", db, FINANCE)
        assert [e["id"] for e in context["events"]] == ["e001", "e002", "e007"]
        no_register = await register_event_context("e006", db, FINANCE)
        assert [e["id"] for e in no_register["events"]] == ["e006"]

    run_with_db(scenario)


def test_register_event_context_respects_store_scope_and_unknown_ids():
    async def scenario(db):
        db.add(event(1))
        await db.commit()
        with pytest.raises(HTTPException) as missing:
            await register_event_context("nope", db, FINANCE)
        assert missing.value.status_code == 404
        other = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S9"]}
        with pytest.raises(HTTPException) as forbidden:
            await register_event_context("e001", db, other)
        assert forbidden.value.status_code == 403

    run_with_db(scenario)


async def add_transaction(db, lines=300):
    db.add(
        CanonicalTransaction(
            id="t",
            business_date="2026-10-01",
            transaction_date=NOW,
            event_timestamp=NOW,
            processing_timestamp=NOW,
            settlement_date="2026-10-01",
            transaction_type="Sale",
            source_lineage="pos-map-v1",
            currency="USD",
            signed_amount=Decimal("600"),
            store_id="S1",
            register_id="R1",
            channel_id="store",
            transaction_number="42",
            detail_status="Balanced",
        )
    )
    await db.flush()
    for number in range(1, lines + 1):
        db.add(
            TransactionLine(
                id=f"l{number:04d}",
                transaction_id="t",
                line_no=number,
                item_code=f"SKU-{number}",
                description=f"Item {number}",
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                gross_amount=Decimal("2"),
            )
        )
    for index, (kind, amount) in enumerate((("Card", "500"), ("Voucher", "100"))):
        db.add(
            TransactionTender(
                id=f"td{index}",
                transaction_id="t",
                tender_type=kind,
                amount=Decimal(amount),
                reference="GC-1" if kind == "Voucher" else None,
            )
        )
    db.add(
        TransactionTax(
            id="tx",
            transaction_id="t",
            tax_code="VAT",
            tax_name="VAT 10%",
            rate=Decimal("0.1"),
            taxable_amount=Decimal("545"),
            tax_amount=Decimal("55"),
        )
    )
    db.add(
        TransactionAdjustment(
            id="a",
            transaction_id="t",
            adjustment_type="Amount",
            proposed_values={"amount_delta": "-1"},
            rationale="r",
            status="Proposed",
            proposed_by="p",
        )
    )
    await db.commit()


def test_a_transaction_with_hundreds_of_lines_returns_only_the_first_few():
    async def scenario(db):
        await add_transaction(db, lines=300)
        brief = await transaction_brief("t", db, FINANCE)
        assert brief["line_count"] == 300 and len(brief["lines"]) == 5
        assert [row["line_no"] for row in brief["lines"]] == [1, 2, 3, 4, 5]
        assert brief["lines"][0]["item_code"] == "SKU-1"
        assert [(t["tender_type"], t["amount"]) for t in brief["tenders"]] == [
            ("Card", Decimal("500")),
            ("Voucher", Decimal("100")),
        ]
        assert brief["tenders"][1]["reference"] == "GC-1"
        assert brief["taxes"][0]["tax_amount"] == Decimal("55")
        assert (brief["discount_count"], brief["corrections"]) == (0, 1)
        assert (brief["detail_status"], brief["transaction_number"]) == ("Balanced", "42")

    run_with_db(scenario)


def test_transaction_brief_respects_store_scope_and_unknown_ids():
    async def scenario(db):
        await add_transaction(db, lines=2)
        with pytest.raises(HTTPException) as missing:
            await transaction_brief("nope", db, FINANCE)
        assert missing.value.status_code == 404
        other = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S9"]}
        with pytest.raises(HTTPException) as forbidden:
            await transaction_brief("t", db, other)
        assert forbidden.value.status_code == 403

    run_with_db(scenario)

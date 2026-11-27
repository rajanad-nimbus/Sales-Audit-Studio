"""A basket of hundreds of lines is read a page at a time, searchable and filterable, with every line reachable."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.transactions.detail_routes import transaction_lines
from fastapi import HTTPException
from models import Base, CanonicalTransaction, TransactionDiscount, TransactionLine
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


async def basket(db, lines=1200):
    """A 1,200-line sale: every 10th line discounted, every 50th price-overridden, line 7 returned."""
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
            signed_amount=Decimal("2400"),
            store_id="S1",
            register_id="R1",
            channel_id="store",
            transaction_number="42",
            detail_status="Balanced",
        )
    )
    await db.flush()
    for n in range(1, lines + 1):
        db.add(
            TransactionLine(
                id=f"l{n:05d}",
                transaction_id="t",
                line_no=n,
                item_code=f"SKU-{n}",
                description="Basmati rice 5kg" if n == 640 else f"Item {n}",
                quantity=Decimal("1"),
                unit_price=Decimal("2"),
                original_unit_price=Decimal("3") if n % 50 == 0 else None,
                gross_amount=Decimal("2"),
                return_reason="Damaged" if n == 7 else None,
            )
        )
        if n % 10 == 0:
            db.add(
                TransactionDiscount(
                    id=f"d{n:05d}",
                    transaction_id="t",
                    kind="Promotion",
                    line_no=n,
                    amount=Decimal("0.50"),
                )
            )
    db.add(
        TransactionDiscount(
            id="whole", transaction_id="t", kind="Coupon", line_no=None, amount=Decimal("5")
        )
    )
    await db.commit()


def test_pages_reach_every_line_well_past_the_preview_limit():
    async def scenario(db):
        await basket(db)
        first = await transaction_lines("t", 0, 50, None, None, db, FINANCE)
        assert [line["line_no"] for line in first["items"]] == list(range(1, 51))
        assert first["total"] == 1200 and first["counts"]["all"] == 1200
        last = await transaction_lines("t", 1150, 50, None, None, db, FINANCE)
        assert [line["line_no"] for line in last["items"]] == list(range(1151, 1201))
        line_1160 = next(line for line in last["items"] if line["line_no"] == 1160)
        assert line_1160["discount_amount"] == Decimal("0.50")

    run_with_db(scenario)


def test_counts_each_kind_of_line_worth_a_look_for_the_whole_basket():
    async def scenario(db):
        await basket(db)
        page = await transaction_lines("t", 0, 50, None, None, db, FINANCE)
        # The whole-sale coupon has no line, so it does not mark any line as discounted.
        assert page["counts"] == {
            "all": 1200,
            "discounted": 120,
            "price_override": 24,
            "returned": 1,
        }

    run_with_db(scenario)


def test_narrows_to_one_kind_of_line_and_pages_within_it():
    async def scenario(db):
        await basket(db)
        overridden = await transaction_lines("t", 0, 50, None, "price_override", db, FINANCE)
        assert overridden["total"] == 24
        assert [line["line_no"] for line in overridden["items"]] == list(range(50, 1201, 50))
        discounted = await transaction_lines("t", 100, 50, None, "discounted", db, FINANCE)
        assert discounted["total"] == 120
        assert discounted["items"][0]["line_no"] == 1010
        returned = await transaction_lines("t", 0, 50, None, "returned", db, FINANCE)
        assert [line["line_no"] for line in returned["items"]] == [7]

    run_with_db(scenario)


def test_finds_a_line_by_item_code_or_description_ignoring_case():
    async def scenario(db):
        await basket(db)
        by_description = await transaction_lines("t", 0, 50, "BASMATI", None, db, FINANCE)
        assert [line["line_no"] for line in by_description["items"]] == [640]
        assert by_description["counts"]["all"] == 1
        by_code = await transaction_lines("t", 0, 50, "sku-115", None, db, FINANCE)
        assert [line["line_no"] for line in by_code["items"]] == [115] + list(range(1150, 1160))
        nothing = await transaction_lines("t", 0, 50, "no such thing", None, db, FINANCE)
        assert nothing["items"] == [] and nothing["total"] == 0

    run_with_db(scenario)


def test_a_store_outside_the_users_scope_is_refused_and_a_missing_transaction_is_not_found():
    async def scenario(db):
        await basket(db, lines=3)
        analyst = {"user": "a", "role": "analyst", "store_scoped": True, "stores": ["S9"]}
        with pytest.raises(HTTPException) as refused:
            await transaction_lines("t", 0, 50, None, None, db, analyst)
        assert refused.value.status_code == 403
        with pytest.raises(HTTPException) as missing:
            await transaction_lines("nope", 0, 50, None, None, db, FINANCE)
        assert missing.value.status_code == 404

    run_with_db(scenario)

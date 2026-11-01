"""Sales transaction detail: items, VAT, discounts, coupons, vouchers, tenders and returns."""

import asyncio
import random
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.ingest.simulation import simulate_feed
from apps.transactions import detail as td
from apps.transactions import routes as rt
from models import (
    Base,
    CanonicalTransaction,
    SourceRecord,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

USER = {"user": "x", "role": "finance"}
D = Decimal


def run(c):
    return asyncio.run(c)


async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def sale(rid="POS-1", amount="113.00", ref="PAY-1", tenders=None, **extra):
    return {
        "source_record_id": rid,
        "event_time": "2026-10-01T10:00:00+00:00",
        "record_type": "Sale",
        "amount": amount,
        "store_id": "S1",
        "register_id": "R1",
        "tender_type": "Card",
        "payment_reference": ref,
        "lines": [
            {
                "item_code": "SKU-1",
                "description": "Tea",
                "quantity": "2",
                "unit_price": "50.00",
                "tax_code": "VAT13",
                "tax_rate": "0.13",
                "tax_amount": "13.00",
            }
        ],
        "discounts": [{"kind": "Coupon", "code": "CPN-1", "amount": "0.00"}][:0],
        "tenders": tenders or [{"tender_type": "Card", "amount": amount}],
        **extra,
    }


def capture(amount, ref="PAY-1", rid="PRC-1"):
    return {
        "source_record_id": rid,
        "event_time": "2026-10-01T11:00:00+00:00",
        "record_type": "Capture",
        "amount": amount,
        "store_id": "S1",
        "payment_reference": ref,
        "settled_on": "2026-10-01",
    }


# ---- extraction and checks ------------------------------------------------------------------------------------
def test_extract_signs_returns_negative_and_balances():
    rec = sale()
    pos = td.extract(rec, 1)
    assert td.check(pos, D("113.00"))["status"] == "Balanced" and pos["taxes"][0][
        "tax_amount"
    ] == D("13.00")
    ret = td.extract({**rec, "original_reference": "POS-1", "return_reason": "Faulty"}, -1)
    assert (
        ret["lines"][0]["quantity"] == D("-2")
        and ret["tenders"][0]["amount"] == D("-113.00")
        and ret["original_reference"] == "POS-1"
    )
    assert td.check(ret, D("-113.00"))["status"] == "Balanced"


def test_unbalanced_detail_is_flagged_not_rejected():
    c = td.check(td.extract(sale(amount="120.00"), 1), D("120.00"))
    assert c["status"] == "Unbalanced" and c["total_variance"] == D(
        "-7.00"
    )  # lines + VAT come to 113.00
    c = td.check(
        td.extract(sale(tenders=[{"tender_type": "Card", "amount": "100.00"}]), 1), D("113.00")
    )
    assert c["status"] == "Unbalanced" and c["tender_variance"] == D("-13.00")


def test_discounts_reduce_the_total():
    rec = sale(
        amount="108.00",
        tenders=[{"tender_type": "Card", "amount": "108.00"}],
        discounts=[{"kind": "Coupon", "code": "CPN-1", "amount": "5.00"}],
    )
    assert td.check(td.extract(rec, 1), D("108.00"))["status"] == "Balanced"  # 100 - 5 + 13


@pytest.mark.parametrize(
    "bad",
    [
        {"lines": [{"item_code": "", "quantity": 1, "unit_price": "1"}]},
        {"discounts": [{"kind": "Magic", "amount": "1"}]},
        {"tenders": [{"tender_type": "Card", "amount": "0"}]},
        {"lines": "nope"},
        {
            "lines": [{"item_code": "A", "quantity": 1, "unit_price": "1"}],
            "discounts": [{"kind": "Coupon", "amount": "1", "line_no": 5}],
        },
    ],
)
def test_malformed_detail_is_rejected(bad):
    with pytest.raises(ValueError):
        td.extract(bad, 1)


# ---- ingestion and reconciliation -----------------------------------------------------------------------------
def test_detail_is_stored_and_a_bad_block_quarantines_the_record():
    async def go():
        Session = await session()
        async with Session() as db:
            stats = await ingest.ingest(
                db,
                "POS",
                "d1",
                [
                    sale(discounts=[{"kind": "Voucher", "code": "VCH-9", "amount": "0.50"}]),
                    sale(
                        rid="POS-BAD",
                        ref="PAY-2",
                        lines=[{"item_code": "", "quantity": 1, "unit_price": "1"}],
                    ),
                ],
            )
            await db.commit()
            assert stats["canonical_created"] == 1 and stats["quarantined"] == 1
            tx = (await db.execute(select(CanonicalTransaction))).scalar_one()
            assert (
                await db.execute(select(func.count()).select_from(TransactionLine))
            ).scalar() == 1
            assert (await db.execute(select(TransactionDiscount.kind))).scalar() == "Voucher"
            assert (
                tx.detail_status == "Unbalanced"
            )  # the 0.50 voucher makes it 112.50 against 113.00 (a cent of rounding is tolerated)
            reason = (
                await db.execute(
                    select(SourceRecord.quarantine_reason).where(
                        SourceRecord.source_record_id == "POS-BAD"
                    )
                )
            ).scalar()
            assert reason.startswith("invalid transaction detail: line 1 has no item_code")

    run(go())


def test_split_tender_matches_the_card_part_and_flags_a_wrong_capture():
    async def go():
        Session = await session()
        split = [
            {"tender_type": "Card", "amount": "80.00"},
            {"tender_type": "Voucher", "amount": "33.00", "reference": "VCH-1"},
        ]
        async with Session() as db:
            await ingest.ingest(
                db,
                "POS",
                "d1",
                [
                    sale(rid="POS-A", ref="PAY-A", tenders=split),
                    sale(rid="POS-B", ref="PAY-B", tenders=split),
                ],
            )
            await ingest.ingest(
                db,
                "Processor",
                "d2",
                [capture("80.00", "PAY-A", "PRC-A"), capture("113.00", "PAY-B", "PRC-B")],
            )
            await db.commit()
            out = await reconciliation.reconcile(db, "2026-10-01")
            await db.commit()
            status = {
                r.source_record_id: s
                for s, r in (
                    await db.execute(
                        select(CanonicalTransaction.reconciliation_status, SourceRecord).join(
                            SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id
                        )
                    )
                ).all()
            }
            assert (
                status["POS-A"] == "Matched" and status["PRC-A"] == "Matched"
            )  # processor captured the card part only
            assert status["POS-B"] == "Exception" and out["exceptions"].get("AMOUNT_MISMATCH") == 1

    run(go())


def test_a_sale_paid_without_a_card_has_no_processor_leg():
    async def go():
        Session = await session()
        async with Session() as db:
            await ingest.ingest(
                db, "POS", "d1", [sale(tenders=[{"tender_type": "Cash", "amount": "113.00"}])]
            )
            await db.commit()
            out = await reconciliation.reconcile(db, "2026-10-01")
            assert (
                out["exceptions"] == {}
                and (await db.execute(select(CanonicalTransaction.reconciliation_status))).scalar()
                == "Matched"
            )

    run(go())


@pytest.mark.parametrize("variation", ["missing_reference", "different_currency", "different_day"])
def test_distinct_cash_sales_are_not_marked_duplicates(variation):
    async def go():
        Session = await session()
        cash = [{"tender_type": "Cash", "amount": "113.00"}]
        first = sale(rid="POS-A", ref="PAY-1", tenders=cash)
        second = sale(rid="POS-B", ref="PAY-1", tenders=cash)
        if variation == "missing_reference":
            first["payment_reference"] = second["payment_reference"] = None
        elif variation == "different_currency":
            second["currency"] = "EUR"
        else:
            second["event_time"] = "2026-10-02T10:00:00+00:00"

        async with Session() as db:
            await ingest.ingest(db, "POS", "d1", [first, second])
            result = await reconciliation.reconcile(db)
            assert result["exceptions"].get("DUPLICATE_SALES", 0) == 0
            statuses = (
                (await db.execute(select(CanonicalTransaction.reconciliation_status)))
                .scalars()
                .all()
            )
            assert statuses == ["Matched", "Matched"]

    run(go())


def test_returns_link_to_the_original_sale_and_detect_over_returns():
    async def go():
        Session = await session()
        async with Session() as db:
            await ingest.ingest(db, "POS", "d1", [sale()])  # sold 2 x SKU-1

            def make_return(record_id, quantity, reference="POS-1"):
                return {
                    **sale(
                        rid=record_id,
                        amount="56.50",
                        ref="PAY-" + record_id,
                        tenders=[{"tender_type": "Card", "amount": "56.50"}],
                    ),
                    "record_type": "Return",
                    "original_reference": reference,
                    "return_reason": "Faulty",
                    "lines": [
                        {
                            "item_code": "SKU-1",
                            "quantity": quantity,
                            "unit_price": "50.00",
                            "tax_code": "VAT13",
                            "tax_rate": "0.13",
                            "tax_amount": "6.50",
                            "return_reason": "Faulty",
                        }
                    ],
                }

            stats = await ingest.ingest(
                db,
                "POS",
                "d2",
                [
                    make_return("R-1", "1"),
                    make_return("R-2", "2"),
                    make_return("R-LOST", "1", reference="POS-NOPE"),
                ],
            )
            await db.commit()
            assert stats["returns_linked"] == 2
            by = {
                s: t
                for t, s in (
                    await db.execute(
                        select(CanonicalTransaction, SourceRecord.source_record_id).join(
                            SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id
                        )
                    )
                ).all()
            }
            assert (
                by["R-1"].original_transaction_id == by["POS-1"].id
                and by["R-LOST"].original_transaction_id is None
            )
            assert by["R-1"].signed_amount == D("-56.50")

            r1 = (await rt.transaction_detail(by["R-1"].id, db=db, _=USER))["returns"]
            assert (
                r1["original"]["source_record_id"] == "POS-1" and r1["items"][0]["sold_qty"] == "2"
            )
            assert r1["over_returned"] is True  # R-1 (1) + R-2 (2) is 3 against 2 sold
            lost = (await rt.transaction_detail(by["R-LOST"].id, db=db, _=USER))["returns"]
            assert lost["original"] is None and lost["original_reference"] == "POS-NOPE"
            sold = await rt.transaction_detail(by["POS-1"].id, db=db, _=USER)
            assert {r["id"] for r in sold["returns"]["returns"]} == {by["R-1"].id, by["R-2"].id}
            assert sold["detail"]["status"] == "Balanced" and sold["detail"]["totals"]["tax"] == D(
                "13.00"
            )
            assert (
                sold["detail"]["lines"][0]["item_code"] == "SKU-1"
                and sold["detail"]["tenders"][0]["tender_type"] == "Card"
            )

    run(go())


def test_return_linking_preserves_earliest_matching_sale_order():
    async def go():
        Session = await session()
        async with Session() as db:
            await ingest.ingest(
                db,
                "POS",
                "sales",
                [
                    sale(rid="POS-LATER", ref="PAY-SHARED", event_time="2026-10-01T12:00:00+00:00"),
                    sale(
                        rid="POS-EARLIER", ref="PAY-SHARED", event_time="2026-10-01T09:00:00+00:00"
                    ),
                ],
            )
            await ingest.ingest(
                db,
                "POS",
                "returns",
                [
                    sale(
                        rid="RETURN",
                        ref="PAY-RETURN",
                        event_time="2026-10-02T09:00:00+00:00",
                        record_type="Return",
                        original_reference="PAY-SHARED",
                    ),
                ],
            )
            linked = (
                await db.execute(
                    select(CanonicalTransaction)
                    .join(SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id)
                    .where(SourceRecord.source_record_id == "RETURN")
                )
            ).scalar_one()
            earliest = (
                await db.execute(
                    select(CanonicalTransaction)
                    .join(SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id)
                    .where(SourceRecord.source_record_id == "POS-EARLIER")
                )
            ).scalar_one()
            assert linked.original_transaction_id == earliest.id

    run(go())


def test_late_sale_links_a_previously_unmatched_return():
    async def go():
        Session = await session()
        async with Session() as db:
            return_record = sale(
                rid="RETURN-LATE",
                ref="PAY-RETURN",
                record_type="Return",
                original_reference="POS-LATE",
            )
            first = await ingest.ingest(db, "POS", "return-first", [return_record])
            assert first["returns_linked"] == 0

            unrelated = await ingest.ingest(
                db,
                "Bank",
                "unrelated",
                [
                    {
                        "source_record_id": "BANK-1",
                        "event_time": "2026-10-01T12:00:00+00:00",
                        "record_type": "Deposit",
                        "amount": "10.00",
                        "store_id": "S1",
                    }
                ],
            )
            assert unrelated["returns_linked"] == 0

            late_sale = await ingest.ingest(db, "POS", "sale-late", [sale(rid="POS-LATE")])
            assert late_sale["returns_linked"] == 1
            linked = (
                await db.execute(
                    select(CanonicalTransaction)
                    .join(SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id)
                    .where(SourceRecord.source_record_id == "RETURN-LATE")
                )
            ).scalar_one()
            original = (
                await db.execute(
                    select(CanonicalTransaction)
                    .join(SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id)
                    .where(SourceRecord.source_record_id == "POS-LATE")
                )
            ).scalar_one()
            assert linked.original_transaction_id == original.id

    run(go())


def test_sale_detail_bounds_large_return_history():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                original = CanonicalTransaction(
                    id="sale",
                    business_date="2026-10-01",
                    transaction_date=timestamp,
                    event_timestamp=timestamp,
                    processing_timestamp=timestamp,
                    settlement_date="2026-10-01",
                    transaction_type="Sale",
                    source_lineage="pos-map-v1",
                    currency="USD",
                    signed_amount=D("1"),
                    store_id="S1",
                    channel_id="store",
                )
                returns = [
                    CanonicalTransaction(
                        id=f"return-{index:03}",
                        business_date="2026-10-01",
                        transaction_date=timestamp,
                        event_timestamp=timestamp,
                        processing_timestamp=timestamp,
                        settlement_date="2026-10-01",
                        transaction_type="Return",
                        source_lineage="pos-map-v1",
                        currency="USD",
                        signed_amount=D("-1"),
                        store_id="S1",
                        channel_id="store",
                        original_transaction_id="sale",
                    )
                    for index in range(rt.MAX_RELATED_RETURNS + 5)
                ]
                db.add_all([original, *returns])
                await db.flush()

                response = await rt.transaction_detail("sale", db=db, _=USER)

                assert len(response["returns"]["returns"]) == rt.MAX_RELATED_RETURNS
                assert response["returns"]["returns_truncated"] is True
        finally:
            await engine.dispose()

    run(go())


def test_transaction_detail_bounds_child_rows_and_uses_full_totals():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                count = rt.MAX_TRANSACTION_DETAIL_ROWS + 2
                db.add(
                    CanonicalTransaction(
                        id="large-sale",
                        business_date="2026-10-01",
                        transaction_date=timestamp,
                        event_timestamp=timestamp,
                        processing_timestamp=timestamp,
                        settlement_date="2026-10-01",
                        transaction_type="Sale",
                        source_lineage="test",
                        currency="USD",
                        signed_amount=D(count) * D("0.99"),
                        store_id="S1",
                        channel_id="store",
                        detail_status="Balanced",
                    )
                )
                db.add_all(
                    TransactionLine(
                        id=f"line-{index}",
                        transaction_id="large-sale",
                        line_no=index,
                        item_code=f"SKU-{index}",
                        quantity=D("1"),
                        unit_price=D("1.00"),
                        gross_amount=D("1.00"),
                        tax_rate=D("0"),
                        tax_amount=D("0"),
                    )
                    for index in range(1, count + 1)
                )
                db.add_all(
                    TransactionDiscount(
                        id=f"discount-{index}",
                        transaction_id="large-sale",
                        line_no=index,
                        kind="Coupon",
                        amount=D("0.01"),
                    )
                    for index in range(1, count + 1)
                )
                db.add_all(
                    TransactionTax(
                        id=f"tax-{index}",
                        transaction_id="large-sale",
                        tax_code="TAX",
                        tax_name="Tax",
                        rate=D("0"),
                        taxable_amount=D("1.00"),
                        tax_amount=D("0"),
                    )
                    for index in range(1, count + 1)
                )
                db.add_all(
                    TransactionTender(
                        id=f"tender-{index}",
                        transaction_id="large-sale",
                        tender_type="Card",
                        amount=D("0.99"),
                    )
                    for index in range(1, count + 1)
                )
                await db.commit()

                response = await rt.transaction_detail("large-sale", db=db, _=USER)
                detail = response["detail"]
                assert len(detail["lines"]) == rt.MAX_TRANSACTION_DETAIL_ROWS
                assert len(detail["discounts"]) == rt.MAX_TRANSACTION_DETAIL_ROWS
                assert len(detail["taxes"]) == rt.MAX_TRANSACTION_DETAIL_ROWS
                assert len(detail["tenders"]) == rt.MAX_TRANSACTION_DETAIL_ROWS
                assert detail["lines_truncated"] and detail["discounts_truncated"]
                assert detail["taxes_truncated"] and detail["tenders_truncated"]
                assert detail["line_count"] == detail["discount_count"] == count
                assert detail["tax_count"] == detail["tender_count"] == count
                assert detail["totals"]["expected_total"] == D(count) * D("0.99")
                assert detail["totals"]["tendered"] == D(count) * D("0.99")
                assert detail["status"] == "Balanced"
        finally:
            await engine.dispose()

    run(go())


def test_return_item_history_is_bounded_without_hiding_over_returning():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                original = CanonicalTransaction(
                    id="sale",
                    business_date="2026-10-01",
                    transaction_date=timestamp,
                    event_timestamp=timestamp,
                    processing_timestamp=timestamp,
                    settlement_date="2026-10-01",
                    transaction_type="Sale",
                    source_lineage="test",
                    currency="USD",
                    signed_amount=D("100"),
                    store_id="S1",
                    channel_id="store",
                )
                returned = CanonicalTransaction(
                    id="return",
                    business_date="2026-10-02",
                    transaction_date=timestamp,
                    event_timestamp=timestamp,
                    processing_timestamp=timestamp,
                    settlement_date="2026-10-02",
                    transaction_type="Return",
                    source_lineage="test",
                    currency="USD",
                    signed_amount=D("-101"),
                    store_id="S1",
                    channel_id="store",
                    original_transaction_id="sale",
                )
                db.add_all([original, returned])
                db.add_all(
                    TransactionLine(
                        id=f"sold-{index}",
                        transaction_id="sale",
                        line_no=index,
                        item_code=f"SKU-{index:03}",
                        quantity=D("1"),
                        unit_price=D("1"),
                        gross_amount=D("1"),
                        tax_rate=D("0"),
                        tax_amount=D("0"),
                    )
                    for index in range(100)
                )
                db.add_all(
                    TransactionLine(
                        id=f"returned-{index}",
                        transaction_id="return",
                        line_no=index,
                        item_code=f"SKU-{index:03}",
                        quantity=D("-1"),
                        unit_price=D("1"),
                        gross_amount=D("-1"),
                        tax_rate=D("0"),
                        tax_amount=D("0"),
                    )
                    for index in range(101)
                )
                await db.commit()

                return_detail = (await rt.transaction_detail("return", db=db, _=USER))["returns"]
                sale_detail = (await rt.transaction_detail("sale", db=db, _=USER))["returns"]
                assert return_detail["items_truncated"] is True
                assert return_detail["over_returned"] is True
                assert len(return_detail["items"]) == rt.MAX_RETURN_ITEM_ROWS
                assert sale_detail["returns"][0]["items_truncated"] is True
                assert len(sale_detail["returns"][0]["items"]) == rt.MAX_RETURN_ITEM_ROWS
        finally:
            await engine.dispose()

    run(go())


# ---- the demo feed ------------------------------------------------------------------------------------------------
def test_demo_feed_detail_balances_and_keeps_the_known_exceptions():
    async def go():
        random.seed(7)
        Session = await session()
        feeds = simulate_feed("2026-10-01")
        async with Session() as db:
            for source, records in feeds.items():
                await ingest.ingest(db, source, "demo", records)
            await db.commit()
            detailed = (
                (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.detail_status != "No detail"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(detailed) >= 25 and all(t.detail_status == "Balanced" for t in detailed)
            kinds = set((await db.execute(select(TransactionDiscount.kind))).scalars().all())
            tenders = set((await db.execute(select(TransactionTender.tender_type))).scalars().all())
            assert (
                {"Card", "Voucher"} <= tenders
                and kinds <= {"Promotion", "Coupon", "Voucher"}
                and kinds
            )
            out = await reconciliation.reconcile(db, "2026-10-01")
            ex = out["exceptions"]
            # Split-tender sales must not create false amount mismatches: exactly the one deliberate case.
            assert (
                ex.get("AMOUNT_MISMATCH") == 1
                and ex.get("DUPLICATE_SALES") == 1
                and ex.get("ORPHAN_PAYMENT") == 1
            )
            returns = (
                (
                    await db.execute(
                        select(CanonicalTransaction).where(
                            CanonicalTransaction.transaction_type == "Return"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert (
                len(returns) == 3 and sum(1 for r in returns if r.original_transaction_id) == 2
            )  # the missing-original case stays unlinked

    run(go())

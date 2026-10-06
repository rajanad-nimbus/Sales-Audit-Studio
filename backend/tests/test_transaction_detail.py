"""Sales transaction detail: items, VAT, discounts, coupons, vouchers, tenders and returns."""
import asyncio
import random
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import ingest
import routes_transactions as rt
import transaction_detail as td
from models import (Base, CanonicalTransaction, SourceRecord, TransactionDiscount, TransactionLine, TransactionTender)

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
    return {"source_record_id": rid, "event_time": "2026-10-01T10:00:00+00:00", "record_type": "Sale", "amount": amount, "store_id": "S1",
            "register_id": "R1", "tender_type": "Card", "payment_reference": ref,
            "lines": [{"item_code": "SKU-1", "description": "Tea", "quantity": "2", "unit_price": "50.00", "tax_code": "VAT13", "tax_rate": "0.13", "tax_amount": "13.00"}],
            "discounts": [{"kind": "Coupon", "code": "CPN-1", "amount": "0.00"}][:0],
            "tenders": tenders or [{"tender_type": "Card", "amount": amount}], **extra}


def capture(amount, ref="PAY-1", rid="PRC-1"):
    return {"source_record_id": rid, "event_time": "2026-10-01T11:00:00+00:00", "record_type": "Capture", "amount": amount, "store_id": "S1",
            "payment_reference": ref, "settled_on": "2026-10-01"}


# ---- extraction and checks ------------------------------------------------------------------------------------
def test_extract_signs_returns_negative_and_balances():
    rec = sale()
    pos = td.extract(rec, 1)
    assert td.check(pos, D("113.00"))["status"] == "Balanced" and pos["taxes"][0]["tax_amount"] == D("13.00")
    ret = td.extract({**rec, "original_reference": "POS-1", "return_reason": "Faulty"}, -1)
    assert ret["lines"][0]["quantity"] == D("-2") and ret["tenders"][0]["amount"] == D("-113.00") and ret["original_reference"] == "POS-1"
    assert td.check(ret, D("-113.00"))["status"] == "Balanced"


def test_unbalanced_detail_is_flagged_not_rejected():
    c = td.check(td.extract(sale(amount="120.00"), 1), D("120.00"))
    assert c["status"] == "Unbalanced" and c["total_variance"] == D("-7.00")      # lines + VAT come to 113.00
    c = td.check(td.extract(sale(tenders=[{"tender_type": "Card", "amount": "100.00"}]), 1), D("113.00"))
    assert c["status"] == "Unbalanced" and c["tender_variance"] == D("-13.00")


def test_discounts_reduce_the_total():
    rec = sale(amount="108.00", tenders=[{"tender_type": "Card", "amount": "108.00"}],
               discounts=[{"kind": "Coupon", "code": "CPN-1", "amount": "5.00"}])
    assert td.check(td.extract(rec, 1), D("108.00"))["status"] == "Balanced"        # 100 - 5 + 13


@pytest.mark.parametrize("bad", [{"lines": [{"item_code": "", "quantity": 1, "unit_price": "1"}]}, {"discounts": [{"kind": "Magic", "amount": "1"}]},
                                 {"tenders": [{"tender_type": "Card", "amount": "0"}]}, {"lines": "nope"},
                                 {"lines": [{"item_code": "A", "quantity": 1, "unit_price": "1"}], "discounts": [{"kind": "Coupon", "amount": "1", "line_no": 5}]}])
def test_malformed_detail_is_rejected(bad):
    with pytest.raises(ValueError):
        td.extract(bad, 1)


# ---- ingestion and reconciliation -----------------------------------------------------------------------------
def test_detail_is_stored_and_a_bad_block_quarantines_the_record():
    async def go():
        Session = await session()
        async with Session() as db:
            stats = await ingest.ingest(db, "POS", "d1", [sale(discounts=[{"kind": "Voucher", "code": "VCH-9", "amount": "0.50"}]),
                                                          sale(rid="POS-BAD", ref="PAY-2", lines=[{"item_code": "", "quantity": 1, "unit_price": "1"}])])
            await db.commit()
            assert stats["canonical_created"] == 1 and stats["quarantined"] == 1
            tx = (await db.execute(select(CanonicalTransaction))).scalar_one()
            assert (await db.execute(select(func.count()).select_from(TransactionLine))).scalar() == 1
            assert (await db.execute(select(TransactionDiscount.kind))).scalar() == "Voucher"
            assert tx.detail_status == "Unbalanced"                      # the 0.50 voucher makes it 112.50 against 113.00 (a cent of rounding is tolerated)
            reason = (await db.execute(select(SourceRecord.quarantine_reason).where(SourceRecord.source_record_id == "POS-BAD"))).scalar()
            assert reason.startswith("invalid transaction detail: line 1 has no item_code")
    run(go())


def test_split_tender_matches_the_card_part_and_flags_a_wrong_capture():
    async def go():
        Session = await session()
        split = [{"tender_type": "Card", "amount": "80.00"}, {"tender_type": "Voucher", "amount": "33.00", "reference": "VCH-1"}]
        async with Session() as db:
            await ingest.ingest(db, "POS", "d1", [sale(rid="POS-A", ref="PAY-A", tenders=split), sale(rid="POS-B", ref="PAY-B", tenders=split)])
            await ingest.ingest(db, "Processor", "d2", [capture("80.00", "PAY-A", "PRC-A"), capture("113.00", "PAY-B", "PRC-B")])
            await db.commit()
            out = await ingest.reconcile(db, "2026-10-01")
            await db.commit()
            status = {r.source_record_id: s for s, r in (await db.execute(select(CanonicalTransaction.reconciliation_status, SourceRecord).join(
                SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id))).all()}
            assert status["POS-A"] == "Matched" and status["PRC-A"] == "Matched"      # processor captured the card part only
            assert status["POS-B"] == "Exception" and out["exceptions"].get("AMOUNT_MISMATCH") == 1
    run(go())


def test_a_sale_paid_without_a_card_has_no_processor_leg():
    async def go():
        Session = await session()
        async with Session() as db:
            await ingest.ingest(db, "POS", "d1", [sale(tenders=[{"tender_type": "Cash", "amount": "113.00"}])])
            await db.commit()
            out = await ingest.reconcile(db, "2026-10-01")
            assert out["exceptions"] == {} and (await db.execute(select(CanonicalTransaction.reconciliation_status))).scalar() == "Matched"
    run(go())


def test_returns_link_to_the_original_sale_and_detect_over_returns():
    async def go():
        Session = await session()
        async with Session() as db:
            await ingest.ingest(db, "POS", "d1", [sale()])      # sold 2 x SKU-1
            ret = lambda rid, qty, ref="POS-1": {**sale(rid=rid, amount="56.50", ref="PAY-" + rid, tenders=[{"tender_type": "Card", "amount": "56.50"}]),
                                                 "record_type": "Return", "original_reference": ref, "return_reason": "Faulty",
                                                 "lines": [{"item_code": "SKU-1", "quantity": qty, "unit_price": "50.00", "tax_code": "VAT13", "tax_rate": "0.13", "tax_amount": "6.50", "return_reason": "Faulty"}]}
            stats = await ingest.ingest(db, "POS", "d2", [ret("R-1", "1"), ret("R-2", "2"), ret("R-LOST", "1", ref="POS-NOPE")])
            await db.commit()
            assert stats["returns_linked"] == 2
            by = {s: t for t, s in (await db.execute(select(CanonicalTransaction, SourceRecord.source_record_id).join(
                SourceRecord, SourceRecord.id == CanonicalTransaction.source_record_id))).all()}
            assert by["R-1"].original_transaction_id == by["POS-1"].id and by["R-LOST"].original_transaction_id is None
            assert by["R-1"].signed_amount == D("-56.50")

            r1 = (await rt.transaction_detail(by["R-1"].id, db=db, _=USER))["returns"]
            assert r1["original"]["source_record_id"] == "POS-1" and r1["items"][0]["sold_qty"] == "2"
            assert r1["over_returned"] is True                     # R-1 (1) + R-2 (2) is 3 against 2 sold
            lost = (await rt.transaction_detail(by["R-LOST"].id, db=db, _=USER))["returns"]
            assert lost["original"] is None and lost["original_reference"] == "POS-NOPE"
            sold = (await rt.transaction_detail(by["POS-1"].id, db=db, _=USER))
            assert {r["id"] for r in sold["returns"]["returns"]} == {by["R-1"].id, by["R-2"].id}
            assert sold["detail"]["status"] == "Balanced" and sold["detail"]["totals"]["tax"] == D("13.00")
            assert sold["detail"]["lines"][0]["item_code"] == "SKU-1" and sold["detail"]["tenders"][0]["tender_type"] == "Card"
    run(go())


# ---- the demo feed ------------------------------------------------------------------------------------------------
def test_demo_feed_detail_balances_and_keeps_the_known_exceptions():
    async def go():
        random.seed(7)
        Session = await session()
        feeds = ingest.simulate_feed("2026-10-01")
        async with Session() as db:
            for source, records in feeds.items():
                await ingest.ingest(db, source, "demo", records)
            await db.commit()
            detailed = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.detail_status != "No detail"))).scalars().all()
            assert len(detailed) >= 25 and all(t.detail_status == "Balanced" for t in detailed)
            kinds = set((await db.execute(select(TransactionDiscount.kind))).scalars().all())
            tenders = set((await db.execute(select(TransactionTender.tender_type))).scalars().all())
            assert {"Card", "Voucher"} <= tenders and kinds <= {"Promotion", "Coupon", "Voucher"} and kinds
            out = await ingest.reconcile(db, "2026-10-01")
            ex = out["exceptions"]
            # Split-tender sales must not create false amount mismatches: exactly the one deliberate case.
            assert ex.get("AMOUNT_MISMATCH") == 1 and ex.get("DUPLICATE_SALES") == 1 and ex.get("ORPHAN_PAYMENT") == 1
            returns = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.transaction_type == "Return"))).scalars().all()
            assert len(returns) == 3 and sum(1 for r in returns if r.original_transaction_id) == 2     # the missing-original case stays unlinked
    run(go())

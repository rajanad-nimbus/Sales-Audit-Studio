"""Field-level change history: each correction with the value before and after it."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import Base, CanonicalTransaction, TransactionAdjustment, TransactionTax
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.transactions.detail_routes import transaction_changes

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
FINANCE = {"user": "finance", "role": "finance"}


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
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
                        signed_amount=Decimal("100"),
                        store_id="S1",
                        register_id="R1",
                        channel_id="store",
                        tender_type="Card",
                        payment_reference="REF-1",
                    )
                )
                db.add(
                    TransactionTax(
                        id="x",
                        transaction_id="t",
                        tax_code="A",
                        tax_name="VAT",
                        rate=Decimal("0.1"),
                        taxable_amount=Decimal("90"),
                        tax_amount=Decimal("10"),
                    )
                )
                await db.commit()
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def adjustment(identifier, kind, values, status, minutes, reviewer="approver"):
    stamp = NOW + timedelta(minutes=minutes)
    return TransactionAdjustment(
        id=identifier,
        transaction_id="t",
        adjustment_type=kind,
        proposed_values=values,
        rationale=f"{identifier} reason",
        status=status,
        proposed_by="clerk",
        reviewed_by=None if status == "Proposed" else reviewer,
        created_at=stamp,
        reviewed_at=None if status == "Proposed" else stamp + timedelta(minutes=1),
    )


def test_amounts_chain_through_approved_corrections_only():
    async def scenario(db):
        db.add_all(
            [
                adjustment("a1", "Amount", {"amount_delta": "-5.00"}, "Approved", 1),
                adjustment("a2", "Amount", {"corrected_amount": "90.00"}, "Approved", 10),
                adjustment("a3", "Amount", {"amount_delta": "1000"}, "Rejected", 20),
                adjustment("a4", "Amount", {"amount_delta": "2.00"}, "Proposed", 30),
            ]
        )
        await db.commit()
        result = await transaction_changes("t", db, FINANCE)
        rows = {c["id"]: c for c in result["changes"]}
        assert (rows["a1"]["before"], rows["a1"]["after"], rows["a1"]["change"]) == (
            Decimal("100"),
            Decimal("95"),
            Decimal("-5"),
        )
        assert (rows["a2"]["before"], rows["a2"]["after"]) == (Decimal("95"), Decimal("90"))
        # A rejected or pending correction shows what it would do but does not move the value.
        assert (rows["a3"]["before"], rows["a3"]["after"], rows["a3"]["applied"]) == (
            Decimal("90"),
            Decimal("1090"),
            False,
        )
        assert rows["a4"]["before"] == Decimal("90")
        assert result["original"]["amount"] == Decimal("100")
        assert result["current"]["amount"] == Decimal("90")

    run_with_db(scenario)


def test_tax_tender_and_reference_changes_start_from_the_recorded_values():
    async def scenario(db):
        db.add_all(
            [
                adjustment("t1", "Tax", {"tax_delta": "1.50"}, "Approved", 1),
                adjustment("d1", "Tender", {"tender_type": "Cash"}, "Approved", 2),
                adjustment("r1", "Reference", {"reference": "REF-2"}, "Approved", 3),
                adjustment("bad", "Amount", {"amount_delta": "abc"}, "Approved", 4),
            ]
        )
        await db.commit()
        result = await transaction_changes("t", db, FINANCE)
        rows = {c["id"]: c for c in result["changes"]}
        assert (rows["t1"]["field"], rows["t1"]["before"], rows["t1"]["after"]) == (
            "Tax",
            Decimal("10"),
            Decimal("11.50"),
        )
        assert (rows["d1"]["before"], rows["d1"]["after"]) == ("Card", "Cash")
        assert (rows["r1"]["before"], rows["r1"]["after"]) == ("REF-1", "REF-2")
        assert rows["bad"]["invalid"] is True and rows["bad"]["after"] is None
        assert result["current"]["tender"] == "Cash" and result["current"]["amount"] == Decimal(
            "100"
        )

    run_with_db(scenario)


def test_unknown_transaction_and_store_scope():
    async def scenario(db):
        with pytest.raises(HTTPException) as missing:
            await transaction_changes("nope", db, FINANCE)
        assert missing.value.status_code == 404
        other = {"user": "m", "role": "analyst", "store_scoped": True, "stores": ["S9"]}
        with pytest.raises(HTTPException) as forbidden:
            await transaction_changes("t", db, other)
        assert forbidden.value.status_code == 403

    run_with_db(scenario)

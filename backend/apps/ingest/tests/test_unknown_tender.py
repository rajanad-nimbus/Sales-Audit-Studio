"""A tender the retailer has not enabled in its Tender reference data is an audit error."""

import asyncio

import pytest
from apps.controls.foundation.routes import ReferenceBody
from apps.ingest.reconciliation import reconcile
from apps.ingest.service import ingest
from models import Base, FoundationReference
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DAY = "2026-10-01"


def sale(record_id: str, tender: str) -> dict:
    return {
        "source_record_id": record_id,
        "record_type": "Sale",
        "event_time": f"{DAY}T10:00:00Z",
        "store_id": "S1",
        "amount": "10",
        "tenders": [{"tender_type": tender, "amount": "10"}],
    }


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unknown_tender_is_flagged_only_once_tenders_are_defined():
    async def scenario(db):
        await ingest(db, "POS", "d1", [sale("a", "GiftCard")])
        await db.commit()
        assert "TENDER_NOT_FOUND" not in (await reconcile(db, DAY))["exceptions"]

        db.add(
            FoundationReference(id="t", category="Tender", code="Cash", name="Cash", active=True)
        )
        await ingest(db, "POS", "d2", [sale("b", "GiftCard"), sale("c", "Cash")])
        await db.commit()
        result = await reconcile(db, DAY)
        # a was matched before tenders were defined and is not re-audited; b is flagged; c is enabled.
        assert result["exceptions"]["TENDER_NOT_FOUND"] == 1

    run_with_db(scenario)


def test_tender_group_must_be_known():
    ok = ReferenceBody(
        category="Tender", code="V", name="Voucher", attributes={"tender_group": "Voucher"}
    )
    assert ok.attributes["tender_group"] == "Voucher"
    with pytest.raises(ValidationError):
        ReferenceBody(category="Tender", code="X", name="X", attributes={"tender_group": "Crypto"})
    # Other categories are unaffected.
    ReferenceBody(category="Item", code="I", name="I", attributes={"tender_group": "anything"})

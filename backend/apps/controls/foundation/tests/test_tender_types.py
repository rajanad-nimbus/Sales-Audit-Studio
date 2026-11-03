"""Tender types: validated attributes, a dedicated API, and the rules they drive."""

import asyncio

import pytest
from apps.controls.foundation.routes import ReferenceBody
from apps.controls.foundation.tenders import (
    TenderBody,
    create_tender,
    list_tenders,
    normalize_attributes,
    update_tender,
)
from apps.ingest.reconciliation import reconcile
from apps.ingest.service import ingest
from fastapi import HTTPException
from models import AuditEvent, Base, FoundationReference
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}
DAY = "2026-10-01"


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


def test_attributes_are_validated_and_normalized():
    assert normalize_attributes({}) == {
        "tender_group": "Other",
        "requires_reference": False,
        "max_amount": None,
    }
    assert normalize_attributes({"max_amount": "250", "x": 1})["max_amount"] == "250.00"
    for bad in (
        {"tender_group": "Crypto"},
        {"requires_reference": "yes"},
        {"max_amount": "-5"},
        {"max_amount": "lots"},
        {"max_amount": "NaN"},
    ):
        with pytest.raises(ValueError):
            normalize_attributes(bad)
    # The generic foundation form applies the same checks to a Tender.
    with pytest.raises(ValidationError):
        ReferenceBody(category="Tender", code="Cash", name="Cash", attributes={"max_amount": "0"})


def test_create_update_and_list_tenders_with_an_audit_trail():
    async def scenario(db):
        created = await create_tender(
            TenderBody(
                code="Voucher",
                name="Voucher",
                tender_group="Voucher",
                requires_reference=True,
                max_amount="500",
            ),
            db,
            FINANCE,
        )
        assert (created["tender_group"], created["requires_reference"], created["max_amount"]) == (
            "Voucher",
            True,
            "500.00",
        )
        with pytest.raises(HTTPException) as duplicate:
            await create_tender(TenderBody(code="Voucher", name="Again"), db, FINANCE)
        assert duplicate.value.status_code == 409
        with pytest.raises(HTTPException) as unknown:
            await create_tender(TenderBody(code="Bitcoin", name="Bitcoin"), db, FINANCE)
        assert unknown.value.status_code == 422 and "Card" in unknown.value.detail

        updated = await update_tender(
            created["id"],
            TenderBody(code="Voucher", name="Gift voucher", active=False, tender_group="Voucher"),
            db,
            FINANCE,
        )
        assert updated["name"] == "Gift voucher" and not updated["active"]
        assert updated["max_amount"] is None and not updated["requires_reference"]
        with pytest.raises(HTTPException) as renamed:
            await update_tender(created["id"], TenderBody(code="Cash", name="x"), db, FINANCE)
        assert renamed.value.status_code == 422

        listing = await list_tenders(None, db, FINANCE)
        assert [t["code"] for t in listing["items"]] == ["Voucher"]
        assert "Cash" in listing["known_codes"] and "Voucher" in listing["groups"]
        assert (await list_tenders(True, db, FINANCE))["items"] == []
        events = (await db.execute(select(AuditEvent.event_type))).scalars().all()
        assert {"TENDER_TYPE_CREATED", "TENDER_TYPE_UPDATED"} <= set(events)

    run_with_db(scenario)


def sale(record_id, tender, amount, reference=None):
    return {
        "source_record_id": record_id,
        "record_type": "Sale",
        "event_time": f"{DAY}T10:00:00Z",
        "store_id": "S1",
        "amount": amount,
        "tenders": [{"tender_type": tender, "amount": amount, "reference": reference}],
    }


def test_reference_and_limit_rules_flag_the_right_transactions():
    async def scenario(db):
        db.add_all(
            [
                FoundationReference(
                    id="a",
                    category="Tender",
                    code="Cash",
                    name="Cash",
                    active=True,
                    attributes={"max_amount": "100"},
                ),
                FoundationReference(
                    id="b",
                    category="Tender",
                    code="GiftCard",
                    name="Gift card",
                    active=True,
                    attributes={"requires_reference": True},
                ),
            ]
        )
        await ingest(
            db,
            "POS",
            "d",
            [
                sale("ok1", "Cash", "40"),
                sale("big", "Cash", "150"),  # over the Cash limit
                sale("noref", "GiftCard", "20"),  # reference required, none given
                sale("ref", "GiftCard", "20", reference="GC-1"),  # fine
                sale("off", "Voucher", "10"),  # not enabled
            ],
        )
        await db.commit()
        exceptions = (await reconcile(db, DAY))["exceptions"]
        assert exceptions["TENDER_OVER_LIMIT"] == 1
        assert exceptions["TENDER_REF_REQ"] == 1
        assert exceptions["TENDER_NOT_FOUND"] == 1

    run_with_db(scenario)

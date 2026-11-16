"""Tender types promote between environments through a previewed, audited package."""

import asyncio

import pytest
from apps.controls.foundation import tender_packages as packages
from apps.controls.foundation.models import FoundationReference
from fastapi import HTTPException
from models import AuditEvent, Base
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}
REASON = "Align with production tenders"


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


def package(*tenders):
    return packages.TenderPackage(format="zetsa.tender-types", version=1, tenders=list(tenders))


def tender(code="Cash", name="Cash", **overrides):
    return {"code": code, "name": name, "tender_group": "Cash", **overrides}


async def existing(db, code, name, **attributes):
    db.add(
        FoundationReference(
            id=f"id-{code}",
            category="Tender",
            code=code,
            name=name,
            active=True,
            attributes={"tender_group": "Other", "requires_reference": False, **attributes},
        )
    )
    await db.commit()


def test_apply_creates_updates_and_leaves_unnamed_tenders_alone():
    async def scenario(db):
        await existing(db, "Cash", "Cash")
        await existing(db, "Voucher", "Old voucher")
        await existing(db, "Other", "Other tender")  # not in the package
        wanted = package(
            tender("Cash"),
            tender("Voucher", "Gift voucher", tender_group="Voucher", requires_reference=True),
            tender("Card", "Card", tender_group="Card", max_amount="5000"),
        )
        plan = await packages.preview_tender_package(
            packages.PreviewRequest(package=wanted), db, FINANCE
        )
        assert plan["valid"]
        assert plan["counts"] == {"create": 1, "update": 2, "unchanged": 0}
        result = await packages.apply_tender_package(
            packages.ApplyRequest(
                package=wanted, preview_token=plan["preview_token"], reason=REASON
            ),
            db,
            FINANCE,
        )
        assert result["changed"] and result["created"] == 1
        rows = {r.code: r for r in (await db.execute(select(FoundationReference))).scalars().all()}
        assert rows["Voucher"].name == "Gift voucher"
        assert rows["Voucher"].attributes["requires_reference"] is True
        assert rows["Card"].attributes["max_amount"] == "5000.00"
        assert rows["Other"].name == "Other tender" and rows["Other"].active
        events = (await db.execute(select(AuditEvent))).scalars().all()
        assert {e.event_type for e in events} == {"TENDER_TYPE_PACKAGE_APPLIED"}
        assert all(REASON in e.action_description for e in events)

        exported = await packages.export_tender_package(db, FINANCE)
        assert [t["code"] for t in exported["tenders"]] == ["Card", "Cash", "Other", "Voucher"]

    run_with_db(scenario)


def test_the_preview_names_each_bad_row_and_an_identical_package_changes_nothing():
    async def scenario(db):
        await existing(db, "Cash", "Cash", tender_group="Cash")
        plan = await packages.preview_tender_package(
            packages.PreviewRequest(
                package=package(
                    tender("Bitcoin", "Bitcoin"),
                    tender("Card", "Card", tender_group="Crypto"),
                    tender("Card", "Card"),
                    tender("Voucher", "Voucher", max_amount="-5"),
                    tender("Other", "Other", surprise=True),
                )
            ),
            db,
            FINANCE,
        )
        assert not plan["valid"] and plan["preview_token"] is None
        assert [e["row"] for e in plan["errors"]] == [1, 2, 4, 5]

        same = package(tender("Cash"))
        unchanged = await packages.preview_tender_package(
            packages.PreviewRequest(package=same), db, FINANCE
        )
        assert unchanged["valid"] and not unchanged["changed"]
        assert await packages.apply_tender_package(
            packages.ApplyRequest(
                package=same, preview_token=unchanged["preview_token"], reason=REASON
            ),
            db,
            FINANCE,
        ) == {"changed": False}

    run_with_db(scenario)


def test_a_stale_preview_is_refused():
    async def scenario(db):
        wanted = package(tender("Card", "Card", tender_group="Card"))
        plan = await packages.preview_tender_package(
            packages.PreviewRequest(package=wanted), db, FINANCE
        )
        await existing(db, "Card", "Created meanwhile")
        with pytest.raises(HTTPException) as caught:
            await packages.apply_tender_package(
                packages.ApplyRequest(
                    package=wanted, preview_token=plan["preview_token"], reason=REASON
                ),
                db,
                FINANCE,
            )
        assert caught.value.status_code == 409

    run_with_db(scenario)

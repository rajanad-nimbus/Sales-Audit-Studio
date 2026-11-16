"""Variance tolerances promote between environments through a previewed, audited package."""

import asyncio
from decimal import Decimal

import pytest
from apps.store_days import tolerance_packages as packages
from fastapi import HTTPException
from models import AuditEvent, Base, VarianceTolerance
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}
REASON = "Align with production policy"


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


def package(*rules):
    return packages.TolerancePackage(
        format="zetsa.variance-tolerances", version=1, tolerances=list(rules)
    )


def rule(total=None, trait=None, within="1", case="10"):
    return {
        "total_name": total,
        "location_trait": trait,
        "within_amount": within,
        "case_amount": case,
    }


def test_the_package_rejects_a_repeated_scope_and_a_case_amount_below_the_tolerance():
    with pytest.raises(ValidationError):
        package(rule("POS to GL Variance"), rule("POS to GL Variance"))
    with pytest.raises(ValidationError):
        package(rule(within="5", case="1"))


def test_apply_creates_changes_and_retires_rules_in_one_audited_step():
    async def scenario(db):
        db.add_all(
            [
                VarianceTolerance(id="keep", total_name="A", within_amount=1, case_amount=5),
                VarianceTolerance(id="change", total_name="B", within_amount=1, case_amount=5),
                VarianceTolerance(id="retire", total_name="C", within_amount=1, case_amount=5),
            ]
        )
        await db.commit()
        wanted = package(
            rule("A", within="1", case="5"), rule("B", within="2", case="8"), rule("D", "North")
        )
        plan = await packages.preview_tolerance_package(
            packages.PreviewRequest(package=wanted), db, FINANCE
        )
        assert plan["changed"] and plan["unchanged"] == 1
        assert [r["total_name"] for r in plan["created"]] == ["D"]
        assert [r["total_name"] for r in plan["updated"]] == ["B"]
        assert [r["total_name"] for r in plan["retired"]] == ["C"]

        result = await packages.apply_tolerance_package(
            packages.ApplyRequest(
                package=wanted, preview_token=plan["preview_token"], reason=REASON
            ),
            db,
            FINANCE,
        )
        assert result == {"changed": True, "created": 1, "updated": 1, "retired": 1}
        rows = (await db.execute(select(VarianceTolerance))).scalars().all()
        active = {(r.total_name, r.location_trait): r.case_amount for r in rows if r.active}
        assert active == {("A", None): 5, ("B", None): Decimal("8"), ("D", "North"): 10}
        assert not next(r for r in rows if r.id == "retire").active
        events = (await db.execute(select(AuditEvent))).scalars().all()
        assert [e.event_type for e in events] == ["VARIANCE_TOLERANCE_PACKAGE_APPLIED"]
        assert REASON in events[0].action_description

        exported = await packages.export_tolerance_package(db, FINANCE)
        assert [t["total_name"] for t in exported["tolerances"]] == ["A", "B", "D"]

    run_with_db(scenario)


def test_a_stale_preview_is_refused_and_an_identical_package_changes_nothing():
    async def scenario(db):
        db.add(VarianceTolerance(id="a", total_name="A", within_amount=1, case_amount=5))
        await db.commit()
        wanted = package(rule("A", within="1", case="5"))
        same = await packages.preview_tolerance_package(
            packages.PreviewRequest(package=wanted), db, FINANCE
        )
        assert not same["changed"]
        assert await packages.apply_tolerance_package(
            packages.ApplyRequest(
                package=wanted, preview_token=same["preview_token"], reason=REASON
            ),
            db,
            FINANCE,
        ) == {"changed": False}

        other = package(rule("A", within="2", case="9"))
        stale = await packages.preview_tolerance_package(
            packages.PreviewRequest(package=other), db, FINANCE
        )
        (await db.get(VarianceTolerance, "a")).case_amount = Decimal("6")  # changed after preview
        await db.commit()
        with pytest.raises(HTTPException) as caught:
            await packages.apply_tolerance_package(
                packages.ApplyRequest(
                    package=other, preview_token=stale["preview_token"], reason=REASON
                ),
                db,
                FINANCE,
            )
        assert caught.value.status_code == 409

    run_with_db(scenario)

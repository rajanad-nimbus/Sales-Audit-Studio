"""Store-day audit rules move between environments as worksheets that still need approval."""

import asyncio
from decimal import Decimal

import pytest
from apps.controls.rules import packages
from apps.controls.rules.routes import RuleBody, create_rule
from fastapi import HTTPException
from models import AuditEvent, Base, ConfiguredAuditRule
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FINANCE = {"user": "finance", "role": "finance"}


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
    return packages.RulePackage(format="zetsa.audit-rules", version=1, rules=list(rules))


def rule(name="Net limit", **overrides):
    return {"name": name, "total_name": "POS Net", "threshold": "25.00", **overrides}


def test_an_exported_rule_imports_as_a_disabled_worksheet_in_another_environment():
    async def source(db):
        await create_rule(
            RuleBody(
                name="Net limit",
                total_name="POS Net",
                threshold=Decimal("25"),
                location_traits=["North"],
                severity="High",
            ),
            db,
            FINANCE,
        )
        return await packages.export_rule_package(db, FINANCE)

    exported = {}

    async def capture(db):
        exported.update(await source(db))

    run_with_db(capture)
    assert exported["format"] == "zetsa.audit-rules" and exported["version"] == 1
    [packaged] = exported["rules"]
    assert packaged["name"] == "Net limit" and packaged["location_traits"] == ["North"]
    assert "status" not in packaged and "enabled" not in packaged  # state is never exported

    async def target(db):
        wanted = packages.RulePackage(**exported)
        plan = await packages.preview_rule_package(
            packages.PreviewRequest(package=wanted), db, FINANCE
        )
        assert plan["valid"] and plan["counts"] == {"worksheets": 1}
        result = await packages.apply_rule_package(
            packages.ApplyRequest(package=wanted, preview_token=plan["preview_token"]),
            db,
            FINANCE,
        )
        assert result["created"] == 1
        [row] = (await db.execute(select(ConfiguredAuditRule))).scalars().all()
        assert (row.status, row.enabled, row.severity) == ("Worksheet", False, "High")
        assert row.location_traits == ["North"] and row.threshold == Decimal("25")
        events = (await db.execute(select(AuditEvent))).scalars().all()
        assert [e.event_type for e in events] == ["AUDIT_RULE_IMPORTED"]

    run_with_db(target)


def test_the_preview_reports_each_bad_row_and_gives_no_token():
    async def scenario(db):
        await create_rule(
            RuleBody(name="Existing", total_name="POS Net", threshold=Decimal("1")), db, FINANCE
        )
        plan = await packages.preview_rule_package(
            packages.PreviewRequest(
                package=package(
                    rule("Existing"),
                    rule("Twice"),
                    rule("Twice"),
                    rule("Unknown total", total_name="No Such Total"),
                    rule("Negative", threshold="-1"),
                    rule("Extra field", surprise=True),
                )
            ),
            db,
            FINANCE,
        )
        assert not plan["valid"] and plan["preview_token"] is None
        assert [e["row"] for e in plan["errors"]] == [1, 3, 4, 5, 6]
        reasons = " | ".join(e["error"] for e in plan["errors"])
        assert "already exists" in reasons and "Duplicate rule name" in reasons
        assert "Unknown field" in reasons

    run_with_db(scenario)


def test_a_changed_rule_set_after_preview_refuses_the_import():
    async def scenario(db):
        wanted = package(rule())
        plan = await packages.preview_rule_package(
            packages.PreviewRequest(package=wanted), db, FINANCE
        )
        await create_rule(
            RuleBody(name="Created meanwhile", total_name="POS Net", threshold=Decimal("1")),
            db,
            FINANCE,
        )
        with pytest.raises(HTTPException) as caught:
            await packages.apply_rule_package(
                packages.ApplyRequest(package=wanted, preview_token=plan["preview_token"]),
                db,
                FINANCE,
            )
        assert caught.value.status_code == 409

    run_with_db(scenario)

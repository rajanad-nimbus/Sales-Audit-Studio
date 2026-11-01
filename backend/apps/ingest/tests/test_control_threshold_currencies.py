import asyncio
from decimal import Decimal

import pytest
from apps.controls.rules.routes import RuleBody, create_rule
from apps.ingest import reconciliation, routes, service
from apps.store_days.routes import retotal_store_day
from fastapi import HTTPException
from models import AuditTotalDefinition, Base, Case, ConfiguredAuditRule, StoreDay
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_rules_apply_to_configured_currency_and_count_rules_are_dimensionless():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        StoreDay(id="day", store_id="S1", business_date="2026-10-01"),
                        AuditTotalDefinition(
                            id="count", name="Count", aggregation="Count", active=True
                        ),
                    ]
                )
                await db.commit()
                user = {"user": "finance"}
                await create_rule(
                    RuleBody(name="USD limit", total_name="POS Net", threshold=Decimal("1")),
                    db,
                    user,
                )
                await create_rule(
                    RuleBody(
                        name="EUR limit",
                        total_name="POS Net",
                        threshold=Decimal("1"),
                        threshold_currency="EUR",
                    ),
                    db,
                    user,
                )
                await create_rule(
                    RuleBody(
                        name="Count limit",
                        total_name="Configured: Count",
                        threshold=Decimal("0"),
                        threshold_currency=None,
                    ),
                    db,
                    user,
                )
                count_rule = (
                    await db.execute(
                        select(ConfiguredAuditRule).where(ConfiguredAuditRule.name == "Count limit")
                    )
                ).scalar_one()
                assert count_rule.threshold_currency is None
                with pytest.raises(HTTPException):
                    await create_rule(
                        RuleBody(
                            name="Invalid",
                            total_name="POS Net",
                            threshold=Decimal("1"),
                            threshold_currency=None,
                        ),
                        db,
                        user,
                    )
                await service.ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {
                            "source_record_id": "s",
                            "record_type": "Sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "store_id": "S1",
                            "amount": "10",
                            "currency": "EUR",
                        }
                    ],
                )
                await db.commit()
                rule_query_count = 0

                def count_rule_queries(
                    _connection, _cursor, statement, _parameters, _context, _executemany
                ):
                    nonlocal rule_query_count
                    normalized = statement.lower()
                    if (
                        normalized.lstrip().startswith("select")
                        and "configured_audit_rules" in normalized
                    ):
                        rule_query_count += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_rule_queries)
                try:
                    await retotal_store_day("S1", "2026-10-01", db, user)
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", count_rule_queries)
                assert rule_query_count == 1
                cases = (await db.execute(select(Case))).scalars().all()
                assert {case.case_type for case in cases} == {
                    "Rule breach: EUR limit",
                    "Rule breach: Count limit",
                }
                count_case = next(
                    case for case in cases if case.case_type == "Rule breach: Count limit"
                )
                assert count_case.currency is None and count_case.total_exposure == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_tolerance_applies_only_to_its_configured_currency():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = {"user": "finance"}
                policy = routes.ReconciliationPolicyRequest(
                    amount_tolerance=Decimal("1"), amount_tolerance_currency="USD"
                )
                await routes.update_reconciliation_policy(policy, db, user)
                assert (await routes.get_reconciliation_policy(db, user))[
                    "amount_tolerance_currency"
                ] == "USD"
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1"}
                for currency in ("USD", "EUR"):
                    await service.ingest(
                        db,
                        "POS",
                        currency,
                        [
                            {
                                **common,
                                "source_record_id": f"pos-{currency}",
                                "record_type": "Sale",
                                "amount": "10",
                                "currency": currency,
                                "payment_reference": currency,
                            }
                        ],
                    )
                    await service.ingest(
                        db,
                        "Processor",
                        currency,
                        [
                            {
                                **common,
                                "source_record_id": f"proc-{currency}",
                                "record_type": "Capture",
                                "amount": "9.50",
                                "currency": currency,
                                "payment_reference": currency,
                            }
                        ],
                    )
                await reconciliation.reconcile(db, "2026-10-01")
                cases = (await db.execute(select(Case))).scalars().all()
                assert {(case.currency, case.total_exception_amount) for case in cases} == {
                    ("EUR", Decimal("0.50"))
                }
        finally:
            await engine.dispose()

    asyncio.run(run())

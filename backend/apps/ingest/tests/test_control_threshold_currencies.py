import asyncio
from decimal import Decimal

from apps.ingest import reconciliation, routes, service
from apps.store_days.routes import retotal_store_day
from integrations.ontology.audit_controls import TotalRule
from models import AuditTotalDefinition, Base, Case, StoreDay
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def rule(name, total_name, currency, threshold="1"):
    return TotalRule(
        id=name.upper().replace(" ", "_"),
        name=name,
        total_name=total_name,
        balance_level="Store",
        comparison="GT",
        threshold=Decimal(threshold),
        threshold_currency=currency,
        severity="Medium",
    )


def test_rules_apply_to_configured_currency_and_count_rules_are_dimensionless(install_controls):
    install_controls(
        total_rules=[
            rule("USD limit", "POS Net", "USD"),
            rule("EUR limit", "POS Net", "EUR"),
            rule("Count limit", "Configured: Count", None, threshold="0"),
        ]
    )

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
                await retotal_store_day("S1", "2026-10-01", db, user)
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
                    amount_tolerance=Decimal("1"),
                    amount_tolerance_currency="USD",
                    reason="Currency specific tolerance test",
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

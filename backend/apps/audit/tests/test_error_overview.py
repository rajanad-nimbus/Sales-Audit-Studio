import asyncio
from decimal import Decimal

from models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.audit.error_routes import cases_for_error, overview
from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.totals.models import SalesAuditErrorDefinition

USER = {"role": "analyst"}


def case(case_id, store, status="Open", currency="USD", amount="100.00"):
    return Case(
        id=case_id,
        case_number=f"C-{case_id}",
        case_type="x",
        status=status,
        business_date="2026-10-01",
        store_id=store,
        currency=currency,
        total_exception_amount=Decimal(amount),
    )


def error(error_id, case_id, code, amount="100.00"):
    return ExceptionRow(
        id=error_id,
        case_id=case_id,
        exception_type=code,
        exception_family="Transaction Audit",
        source_system="Reconciliation",
        detection_origin="Reconciliation",
        detection_rule_id=f"RECON-{code}",
        exception_amount=Decimal(amount),
        estimated_exposure=Decimal(amount),
        severity="Medium",
        confidence="High",
        close_impact="Yes",
    )


def run(*calls, user=USER):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        SalesAuditErrorDefinition(
                            code="RETURN_OVER_SOLD",
                            description="Returned more than sold.",
                            short_description="Return exceeds original sale",
                            form="Transaction Detail",
                            tab="Item",
                            recommended_solution="Compare with the original sale.",
                        ),
                        case("a", "S1"),
                        case("b", "S2", currency="CAD", amount="40.00"),
                        case("c", "S1", status="Closed"),
                        error("e1", "a", "RETURN_OVER_SOLD"),
                        error("e2", "b", "RETURN_OVER_SOLD", "40.00"),
                        error("e3", "c", "RETURN_OVER_SOLD"),
                        error("e4", "a", "TRAN_OUT_BAL", "7.50"),
                    ]
                )
                await db.commit()
                return [await call(db, user) for call in calls]
        finally:
            await engine.dispose()

    return asyncio.run(go())


def by_code(result):
    return {item["code"]: item for item in result["items"]}


def test_overview_lists_the_whole_catalog_with_open_counts_per_currency():
    [result] = run(lambda db, user: overview(db, user))
    items = by_code(result)
    over = items["RETURN_OVER_SOLD"]
    assert (over["open_count"], over["total_count"]) == (2, 3)
    assert over["open_by_currency"] == [
        {"currency": "CAD", "amount": "40.00", "exposure": "40.00"},
        {"currency": "USD", "amount": "100.00", "exposure": "100.00"},
    ]
    assert (over["form"], over["tab"]) == ("Transaction Detail", "Item")
    assert over["recommended_solution"] == "Compare with the original sale."
    assert items["TRAN_OUT_BAL"]["open_count"] == 1 and items["TRAN_OUT_BAL"]["description"] is None
    assert items["TAX_MISMATCH"]["open_count"] == 0  # known type, nothing raised yet
    assert result["open_total"] == 3 and result["open_types"] == 2


def test_overview_orders_open_errors_first_and_falls_back_to_ontology_steps():
    [result] = run(lambda db, user: overview(db, user))
    assert [i["code"] for i in result["items"][:2]] == ["RETURN_OVER_SOLD", "TRAN_OUT_BAL"]
    out_of_balance = by_code(result)["TRAN_OUT_BAL"]
    assert "Re-audit" in out_of_balance["recommended_solution"]
    assert out_of_balance["label"] == "Transaction Out of Balance"


def test_store_scoped_users_only_count_their_stores():
    scoped = {"role": "store_manager", "store_scoped": True, "stores": ["S2"]}
    [result] = run(lambda db, user: overview(db, user), user=scoped)
    over = by_code(result)["RETURN_OVER_SOLD"]
    assert (over["open_count"], over["total_count"]) == (1, 1)
    assert by_code(result)["TRAN_OUT_BAL"]["total_count"] == 0


def test_cases_for_an_error_put_open_cases_first_and_respect_scope():
    [everyone, scoped] = run(
        lambda db, user: cases_for_error("RETURN_OVER_SOLD", 25, db, user),
        lambda db, user: cases_for_error(
            "RETURN_OVER_SOLD",
            25,
            db,
            {"role": "store_manager", "store_scoped": True, "stores": ["S2"]},
        ),
    )
    assert [c["id"] for c in everyone["items"]][-1] == "c"  # the closed case last
    assert {c["id"] for c in everyone["items"]} == {"a", "b", "c"}
    assert [c["id"] for c in scoped["items"]] == ["b"]
    assert everyone["truncated"] is False

"""The workbook's client totals are valid definitions and form a closed, acyclic combined graph."""

from types import SimpleNamespace

from apps.totals.client_totals import CLIENT_TOTALS
from apps.totals.domain import validate_definition
from apps.totals.schemas import TotalDefinitionBody


def test_every_client_total_is_valid():
    for total in CLIENT_TOTALS:
        body = {k: v for k, v in total.items() if k not in {"id", "joins"}}
        TotalDefinitionBody.model_validate(body)
        validate_definition(SimpleNamespace(**{**total, "combined_operator": None}))


def test_names_and_ids_are_unique_and_combined_operands_exist():
    names = [t["name"] for t in CLIENT_TOTALS]
    ids = {t["id"]: t for t in CLIENT_TOTALS}
    assert len(set(names)) == len(names) and len(ids) == len(names)
    assert "OVRSHT_S" not in names  # reserved for the system-required total
    for total in CLIENT_TOTALS:
        for operand in total["combined_total_ids"]:
            assert operand in ids
            assert ids[operand]["level"] == total["level"]
            assert ids[operand]["active"], total["name"]


def test_combined_graph_is_acyclic():
    by_id = {t["id"]: t for t in CLIENT_TOTALS}
    done: set[str] = set()

    def visit(identifier: str, path: tuple[str, ...]) -> None:
        assert identifier not in path
        if identifier in done:
            return
        for operand in by_id[identifier]["combined_total_ids"]:
            visit(operand, (*path, identifier))
        done.add(identifier)

    for identifier in by_id:
        visit(identifier, ())


def test_workbook_scale():
    assert len(CLIENT_TOTALS) >= 190
    names = {t["name"] for t in CLIENT_TOTALS}
    assert {"CASH", "CASHP", "CASHO", "OVRSHT_B", "OVRSHT_POS", "SLSTAXACC", "DONATE"} <= names


def test_full_client_set_loads_and_calculates_a_store_day():
    import asyncio

    from models import AuditTotalDefinition, Base, StoreDay
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from apps.ingest.service import ingest
    from apps.store_days import routes

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(
                    AuditTotalDefinition(**{**t, "joins": t["joins"]}, revision=1)
                    for t in CLIENT_TOTALS
                )
                await ingest(
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
                        }
                    ],
                )
                await db.commit()
                result = await routes.retotal_store_day(
                    "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
                )
                assert result is not None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_every_tender_a_total_matches_on_is_accepted_by_ingest():
    from apps.transactions.detail import CLIENT_TENDER_CODES

    codes = {
        r["value"]
        for t in CLIENT_TOTALS
        for r in t["restrictions"]
        if r["realm"] == "Tender" and r["field"] == "client_tender_code" and r["operator"] == "eq"
    }
    assert len(codes) >= 17 and codes <= set(CLIENT_TENDER_CODES)


def test_a_visa_tender_lands_in_the_visa_total_and_not_in_amex():
    import asyncio

    from models import AuditTotalDefinition, Base, StoreDay
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from apps.ingest.service import ingest
    from apps.totals.calculation import calculate_store_day, effective_definitions

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                await ingest(
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
                            "tender_type": "Card",
                            "tenders": [
                                {
                                    "tender_type": "Card",
                                    "client_tender_code": "VISA",
                                    "amount": "10",
                                }
                            ],
                        }
                    ],
                )
                await db.commit()
                definitions = await effective_definitions(db, "2026-10-01", "S1")
                result = await calculate_store_day(db, "S1", "2026-10-01", definitions)
                ids = {d.name: d.id for d in definitions}
                total = lambda name: sum(result.configured_totals[ids[name]].values())  # noqa: E731
                assert total("VISA") == 10
                assert total("AMEX") == 0
        finally:
            await engine.dispose()

    asyncio.run(run())

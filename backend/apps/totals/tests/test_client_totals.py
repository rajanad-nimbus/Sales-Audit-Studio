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
        code
        for t in CLIENT_TOTALS
        for r in t["restrictions"]
        if r["realm"] == "Tender" and r["field"] == "client_tender_code"
        for code in r["value"].split(",")
    }
    assert len(codes) >= 17 and codes <= set(CLIENT_TENDER_CODES)


def test_a_legacy_visa_tender_uses_its_retained_rtlog_tender_attributes():
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
                                    "source_attributes": {
                                        "tender_group": "CCARD",
                                        "tender_type_id": "3000",
                                    },
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


def test_register_lists_are_exact_and_condition_matches_in_lists():
    from types import SimpleNamespace

    from apps.controls.rules.transaction_logic import condition_matches

    by_name = {t["name"]: t for t in CLIENT_TOTALS}
    amazon = by_name["SLSTXAMZN"]["restrictions"][0]
    assert amazon["operator"] == "in" and amazon["value"] == "892,897"
    assert condition_matches(amazon, SimpleNamespace(register_id="892"))
    assert not condition_matches(amazon, SimpleNamespace(register_id="895"))
    store = by_name["SLSTAXACC"]["restrictions"][0]
    assert store["operator"] == "not_in"
    assert condition_matches(store, SimpleNamespace(register_id="R1"))
    assert not condition_matches(store, SimpleNamespace(register_id="897"))
    assert not any(r["field"] == "channel_id" for t in CLIENT_TOTALS for r in t["restrictions"])
    for name, expected in (
        ("SHIPAMZN", {"892", "897"}),
        ("SHIPEBAY", {"895", "896"}),
        ("SHIPWLMT", {"880", "881"}),
    ):
        shipping = next(r for r in by_name[name]["restrictions"] if r["field"] == "register_id")
        for register in ("001", "880", "881", "892", "895", "896", "897"):
            assert condition_matches(shipping, SimpleNamespace(register_id=register)) == (
                register in expected
            )


def test_item_lists_are_exact_on_sales_and_paid_in_out_totals():
    by_name = {t["name"]: t for t in CLIENT_TOTALS}

    def item_restriction(name):
        return next(r for r in by_name[name]["restrictions"] if r["field"] == "item_code")

    remote = item_restriction("REMSLSOUT")
    assert remote["operator"] == "not_in" and len(remote["value"].split(",")) == 30
    assert item_restriction("REMNMSBSI")["operator"] == "in"
    assert item_restriction("BCOLLPLCC") == {
        "realm": "Line",
        "field": "item_code",
        "operator": "in",
        "value": "NM050OUT,NM050BSI",
    }
    assert not any(
        r["operator"] == "contains" and r["value"].startswith("NM")
        for t in CLIENT_TOTALS
        for r in t["restrictions"]
    )


def test_a_trait_excluded_operand_counts_as_zero_inside_a_combined_total():
    from decimal import Decimal

    from models import AuditTotalDefinition

    from apps.totals.calculation import keep_trait_applicable, trait_excluded
    from apps.totals.combined import calculate_combined

    by_name = {t["name"]: t for t in CLIENT_TOTALS}
    # The workbook limits these by store area; operators opt in by setting the trait on the total.
    limited = {"SLSCLBSI": ["BSI"], "SLSCLOUT": ["OUTLET"]}
    definitions = [
        AuditTotalDefinition(**{**t, "revision": 1, "location_traits": limited.get(t["name"], [])})
        for t in CLIENT_TOTALS
    ]
    assert by_name["SALESCLAS"]["combined_total_ids"]
    live = keep_trait_applicable(definitions, {"BSI"})
    names = {d.name: d for d in live}
    assert trait_excluded(names["SLSCLOUT"]) and not trait_excluded(names["SLSCLBSI"])
    values = {d.id: {} for d in live if d.calculation_basis != "Combined"}
    values[names["SLSCLBSI"].id] = {None: Decimal("40")}
    calculate_combined(live, values)  # would raise if SLSCLOUT were missing
    assert values[names["SALESCLAS"].id] == {None: Decimal("40")}
    unrestricted = keep_trait_applicable(
        [AuditTotalDefinition(**{**t, "revision": 1}) for t in CLIENT_TOTALS], set()
    )
    assert not any(trait_excluded(d) for d in unrestricted)


def test_voucher_totals_use_the_number_range_classification():
    from apps.transactions.detail import parse_line, voucher_kind_for

    assert voucher_kind_for("100000000001") == "GIFTCARD"
    assert voucher_kind_for("250000000000") == "MCR"
    assert voucher_kind_for("399999999999") == "VIRTUAL"
    assert voucher_kind_for("4000") is None and voucher_kind_for(None) is None
    line = parse_line(
        {
            "item_code": "GC",
            "quantity": "1",
            "unit_price": "25",
            "source_attributes": {"voucher_number": "250000000000", "item_type": "GCN"},
        },
        1,
        1,
    )
    assert line["voucher_kind"] == "MCR"
    by_name = {t["name"]: t for t in CLIENT_TOTALS}
    for name, expected in (("GFTCRDSL", "GIFTCARD,MCR"), ("VIRTGCSL", "VIRTUAL")):
        restriction = by_name[name]["restrictions"][0]
        assert restriction["field"] == "voucher_kind" and restriction["value"] == expected


def test_store_area_limits_the_bsi_and_outlet_sales_totals():
    import asyncio

    from models import AuditTotalDefinition, Base, FoundationReference
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from apps.ingest.service import ingest
    from apps.totals.calculation import calculate_store_day, effective_definitions

    names = ("SLSCLBSI", "SLSCLOUT", "SALESCLAS")

    async def run(area):
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                db.add(
                    FoundationReference(
                        id="s1",
                        category="Store",
                        code="S1",
                        name="Store 1",
                        attributes={} if area is None else {"area": area},
                    )
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
                            "amount": "8",
                            "lines": [{"item_code": "SKU1", "quantity": "1", "unit_price": "10"}],
                            "discounts": [{"kind": "Promotion", "line_no": 1, "amount": "2"}],
                            "tenders": [{"tender_type": "Cash", "amount": "8"}],
                        }
                    ],
                )
                await db.commit()
                definitions = await effective_definitions(db, "2026-10-01", "S1")
                result = await calculate_store_day(db, "S1", "2026-10-01", definitions)
                ids = {d.name: d.id for d in definitions}
                return {n: sum(result.configured_totals.get(ids[n], {}).values()) for n in names}
        finally:
            await engine.dispose()

    assert asyncio.run(run(2500)) == {"SLSCLBSI": 8, "SLSCLOUT": 0, "SALESCLAS": 8}
    assert asyncio.run(run(3500)) == {"SLSCLBSI": 0, "SLSCLOUT": 8, "SALESCLAS": -8}
    assert asyncio.run(run(None)) == {"SLSCLBSI": 0, "SLSCLOUT": 0, "SALESCLAS": 0}


def test_a_store_area_must_be_a_whole_number():
    import pytest
    from pydantic import ValidationError

    from apps.controls.foundation.routes import ReferenceBody

    assert (
        ReferenceBody(
            category="Store", code="S1", name="S", attributes={"area": "2500"}
        ).attributes["area"]
        == 2500
    )
    for bad in ("x", 12.5, True, 100000, -1):
        with pytest.raises(ValidationError):
            ReferenceBody(category="Store", code="S1", name="S", attributes={"area": bad})


def test_workbook_subtracts_the_first_operand_in_each_negative_group():
    from decimal import Decimal

    from models import AuditTotalDefinition

    from apps.totals.combined import calculate_combined

    definitions = [AuditTotalDefinition(**total, rollups=[]) for total in CLIENT_TOTALS]
    values = {row.id: {} for row in definitions if row.calculation_basis == "Raw"}
    by_name = {row.name: row.id for row in definitions}
    for name in ("BLSPRDEXP", "PROPSTEXP", "AGNCYVOUP", "GFTCRDSLP", "SHIPEBAYP"):
        values[by_name[name]] = {None: Decimal("10")}

    calculate_combined(definitions, values)

    assert values[by_name["REMSLSCL"]] == {None: Decimal("-20")}
    assert values[by_name["OVRSHT_POS"]] == {None: Decimal("-30")}

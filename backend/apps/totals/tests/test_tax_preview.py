import asyncio

import pytest
from fastapi import HTTPException
from models import AuditTotalDefinition, Base, CanonicalTransaction, TransactionAdjustment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest import service
from apps.totals.routes import preview_definition


def preview(measure, *, adjustment_values=None, adjustment_status="Approved"):
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                sale = {
                    "event_time": "2026-10-01T10:00:00Z",
                    "store_id": "S1",
                    "source_record_id": "T1",
                    "payment_reference": "P1",
                    "lines": [
                        {
                            "item_code": "A",
                            "quantity": "1",
                            "unit_price": "100.00",
                            "tax_code": "VAT",
                            "tax_rate": "0.13",
                        }
                    ],
                }
                await service.ingest(
                    db,
                    "POS",
                    "d1",
                    [
                        {**sale, "record_type": "Sale", "amount": "113.00"},
                        {
                            **sale,
                            "source_record_id": "R1",
                            "payment_reference": "P2",
                            "record_type": "Return",
                            "amount": "26.00",
                            "lines": [
                                {
                                    "item_code": "A",
                                    "quantity": "1",
                                    "unit_price": "20.00",
                                    "tax_code": "VAT",
                                    "tax_rate": "0.30",
                                }
                            ],
                        },
                    ],
                )
                if adjustment_values is not None:
                    transaction_id = (
                        await db.execute(
                            select(CanonicalTransaction.id).where(
                                CanonicalTransaction.payment_reference == "P1"
                            )
                        )
                    ).scalar_one()
                    db.add(
                        TransactionAdjustment(
                            id="tax-adjustment",
                            transaction_id=transaction_id,
                            adjustment_type="Tax",
                            proposed_values=adjustment_values,
                            status=adjustment_status,
                            rationale="Correct recorded tax",
                            proposed_by="finance",
                        )
                    )
                db.add(
                    AuditTotalDefinition(
                        id="d",
                        name="T",
                        measure=measure,
                        aggregation="Sum",
                        source_system="POS",
                        transaction_types=["Sale", "Return"],
                        level="Store",
                        active=True,
                    )
                )
                await db.commit()
                return await preview_definition("d", "S1", "2026-10-01", db, {})
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_tax_total_sums_recorded_tax_signed_like_the_transaction():
    assert preview("Tax")["value"] == 13 - 6  # sale tax 13.00, return tax -6.00


def test_tax_total_applies_only_approved_tax_corrections():
    assert preview("Tax", adjustment_values={"tax_delta": "2.50"})["value"] == 13 - 6 + 2.50
    assert (
        preview("Tax", adjustment_values={"tax_delta": "2.50"}, adjustment_status="Proposed")[
            "value"
        ]
        == 13 - 6
    )


def test_tax_calculation_reports_malformed_approved_adjustment_data():
    with pytest.raises(HTTPException, match="invalid stored values"):
        preview("Tax", adjustment_values=["not", "a", "mapping"])


def test_amount_total_still_sums_the_header_amount():
    assert preview("Amount")["value"] == 113 - 26


def test_preview_rejects_unassigned_store_before_querying():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            preview_definition(
                "d", "S2", "2026-10-01", None, {"store_scoped": True, "stores": ["S1"]}
            )
        )
    assert exc.value.status_code == 403


def test_preview_ignores_unrelated_invalid_definitions_and_rejects_ineffective_dependencies():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all(
                    [
                        AuditTotalDefinition(id="a", name="A", level="Store", active=True),
                        AuditTotalDefinition(id="b", name="B", level="Store", active=True),
                        AuditTotalDefinition(
                            id="bad",
                            name="Bad",
                            level="Store",
                            calculation_basis="Combined",
                            combined_total_ids=["missing", "a"],
                            combined_operator="+",
                            active=True,
                        ),
                        AuditTotalDefinition(
                            id="sum",
                            name="Sum",
                            level="Store",
                            calculation_basis="Combined",
                            combined_total_ids=["a", "b"],
                            combined_operator="+",
                            active=True,
                        ),
                    ]
                )
                await db.commit()
                assert (await preview_definition("sum", "S1", "2026-10-01", db, {}))["value"] == 0
                db.add(
                    AuditTotalDefinition(id="a2", name="A", revision=2, level="Store", active=True)
                )
                await db.commit()
                with pytest.raises(HTTPException, match="ineffective"):
                    await preview_definition("sum", "S1", "2026-10-01", db, {})
        finally:
            await engine.dispose()

    asyncio.run(go())

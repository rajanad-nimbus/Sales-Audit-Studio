"""Expected tender amounts from the ReSA setup, exercised through POS ingestion."""

import asyncio
from decimal import Decimal

from models import AuditTotalDefinition, Base, StoreDay
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.totals.calculation import calculate_store_day, effective_definitions
from apps.totals.client_totals import CLIENT_TOTALS

# Group and type IDs are transcribed from the workbook's ReSA Calculated sheet, row 30.
TENDERS = [
    ("AGNCYVOU", "VOUCH", "4000"),
    ("AMAZON", "OTHERS", "9012"),
    ("AMEX", "CCARD", "3020"),
    ("BEALLSCC", "CCARD", "3120"),
    ("CASH", "CASH", "1000"),
    ("CHECK", "CHECK", "2000"),
    ("DISCOVER", "CCARD", "3030"),
    ("EBAY", "OTHERS", "9011"),
    ("GIFTCRDU", "VOUCH", "4040"),
    ("MASTERCD", "CCARD", "3010"),
    ("MCVISADBT", "DCARD", "1300"),
    ("OUTLTCC", "CCARD", "3130"),
    ("PAYPAL", "PAYPAL", "9010"),
    ("VIRTGC", "VOUCH", "4120"),
    ("VISA", "CCARD", "3000"),
    ("WALMART", "OTHERS", "9013"),
]


def test_workbook_tenders_are_separated_and_cash_returns_reduce_totals():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01"))
                db.add_all(AuditTotalDefinition(**t, revision=1) for t in CLIENT_TOTALS)
                records = []
                for index, (_, group, type_id) in enumerate(TENDERS, 1):
                    records.append(
                        {
                            "source_record_id": f"sale-{index}",
                            "record_type": "Sale",
                            "event_time": "2026-10-01T10:00:00Z",
                            "store_id": "S1",
                            "amount": str(index),
                            "tender_type": "Other",
                            "tenders": [
                                {
                                    "tender_type": "Other",
                                    "amount": str(index),
                                    "source_attributes": {
                                        "tender_group": group,
                                        "tender_type_id": type_id,
                                    },
                                }
                            ],
                        }
                    )
                records.append(
                    {
                        "source_record_id": "cash-return",
                        "record_type": "Return",
                        "event_time": "2026-10-01T11:00:00Z",
                        "store_id": "S1",
                        "amount": "2",
                        "tender_type": "Cash",
                        "tenders": [
                            {
                                "tender_type": "Cash",
                                "amount": "2",
                                "source_attributes": {
                                    "tender_group": "CASH",
                                    "tender_type_id": "1000",
                                },
                            }
                        ],
                    }
                )
                records.append(
                    {
                        "source_record_id": "split-sale",
                        "record_type": "Sale",
                        "event_time": "2026-10-01T12:00:00Z",
                        "store_id": "S1",
                        "amount": "10",
                        "tender_type": "Cash",
                        "tenders": [
                            {"tender_type": "Cash", "amount": "4", "client_tender_code": "CASH"},
                            {"tender_type": "Card", "amount": "6", "client_tender_code": "VISA"},
                        ],
                    }
                )
                outcome = await ingest(db, "POS", "tenders", records)
                assert outcome["quarantined"] == 0
                definitions = await effective_definitions(db, "2026-10-01", "S1")
                calculated = await calculate_store_day(db, "S1", "2026-10-01", definitions)
                ids = {d.name: d.id for d in definitions}
                for index, (name, _, _) in enumerate(TENDERS, 1):
                    expected = Decimal(index + {"CASH": 2, "VISA": 6}.get(name, 0))
                    assert sum(calculated.configured_totals[ids[name]].values()) == expected, name
                assert sum(calculated.configured_totals[ids["CASHTOTAL"]].values()) == Decimal("13")
        finally:
            await engine.dispose()

    asyncio.run(run())

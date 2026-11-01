import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from apps.ingest import reconciliation, service
from models import Base, CanonicalTransaction, Case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_settlement_batches_separate_dates_currencies_and_apply_tolerance():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                base = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1", "settlement_reference": "batch"}
                await service.ingest(db, "Processor", "processor", [
                    {**base, "source_record_id": "usd", "record_type": "Capture", "amount": "100", "currency": "USD"},
                    {**base, "source_record_id": "eur", "record_type": "Capture", "amount": "40", "currency": "EUR"},
                    {**base, "source_record_id": "old", "record_type": "Capture", "amount": "999", "currency": "USD",
                     "settled_on": "2026-09-01", "settlement_reference": "old-batch"},
                ])
                await service.ingest(db, "Bank", "bank", [
                    {**base, "source_record_id": "usd-part1", "record_type": "Deposit", "amount": "40", "currency": "USD"},
                    {**base, "source_record_id": "usd-part2", "record_type": "Deposit", "amount": "60.05", "currency": "USD"},
                    {**base, "source_record_id": "eur-bank", "record_type": "Deposit", "amount": "50", "currency": "EUR"},
                ])
                rows = (await db.execute(select(CanonicalTransaction))).scalars().all()
                processors = [row for row in rows if row.source_lineage == service.LINEAGE["Processor"]]
                banks = [row for row in rows if row.source_lineage == service.LINEAGE["Bank"]]
                for row in processors:
                    row.reconciliation_status = "Matched"
                first_usd = next(row for row in banks if row.signed_amount == Decimal("40"))
                first_usd.reconciliation_status = "Matched"
                await db.flush()
                findings = []
                await reconciliation.run_settlement_controls(db, [row for row in banks if row.id != first_usd.id],
                                                      lambda *args: findings.append(args), Decimal("0.10"))
                assert findings == [("BANK_VARIANCE", "S1", "2026-10-01", Decimal("10.00"), "EUR")]
                assert next(row for row in banks if row.signed_amount == Decimal("60.05")).reconciliation_status == "Matched"
                assert next(row for row in banks if row.currency == "EUR").reconciliation_status == "Exception"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_reconciliation_cases_do_not_sum_different_currencies():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {"event_time": "2026-10-01T10:00:00Z", "store_id": "S1"}
                for currency in ("USD", "EUR"):
                    await service.ingest(db, "POS", currency, [{**common, "source_record_id": f"pos-{currency}",
                        "record_type": "Sale", "amount": "100", "currency": currency, "payment_reference": currency}])
                    await service.ingest(db, "Processor", currency, [{**common, "source_record_id": f"proc-{currency}",
                        "record_type": "Capture", "amount": "90", "currency": currency, "payment_reference": currency}])
                await reconciliation.reconcile(db, "2026-10-01")
                cases = (await db.execute(select(Case))).scalars().all()
                assert {(case.currency, case.total_exception_amount) for case in cases} == {
                    ("USD", Decimal("10")), ("EUR", Decimal("10"))}
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_daily_bank_control_includes_previously_matched_deposits():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                common = {
                    "business_date": "2026-10-01",
                    "transaction_date": timestamp,
                    "event_timestamp": timestamp,
                    "processing_timestamp": timestamp,
                    "settlement_date": "2026-10-01",
                    "currency": "USD",
                    "store_id": "S1",
                    "channel_id": "store",
                    "settlement_reference": None,
                }
                processor = CanonicalTransaction(
                    id="processor", transaction_type="Capture",
                    source_lineage=service.LINEAGE["Processor"], reconciliation_status="Matched",
                    signed_amount=Decimal("100.00"), **common,
                )
                previously_matched = CanonicalTransaction(
                    id="bank-matched", transaction_type="Deposit",
                    source_lineage=service.LINEAGE["Bank"], reconciliation_status="Matched",
                    signed_amount=Decimal("40.00"), **common,
                )
                newly_arrived = CanonicalTransaction(
                    id="bank-unmatched", transaction_type="Deposit",
                    source_lineage=service.LINEAGE["Bank"], reconciliation_status="Unmatched",
                    signed_amount=Decimal("60.00"), **common,
                )
                db.add_all([processor, previously_matched, newly_arrived])
                await db.flush()

                findings = []
                await reconciliation.run_settlement_controls(
                    db, [newly_arrived], lambda *args: findings.append(args)
                )

                assert findings == []
                assert (previously_matched.reconciliation_status, previously_matched.disposition) == (
                    "Matched", "Closed"
                )
                assert (newly_arrived.reconciliation_status, newly_arrived.disposition) == (
                    "Matched", "Closed"
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_referenced_settlement_controls_batch_exact_scopes():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                banks = []
                processors = []
                for index in range(501):
                    store = f"STORE-{index:03}"
                    reference = f"SETTLEMENT-{index:03}"
                    common = {
                        "business_date": "2026-10-01",
                        "transaction_date": timestamp,
                        "event_timestamp": timestamp,
                        "processing_timestamp": timestamp,
                        "settlement_date": "2026-10-01",
                        "currency": "USD",
                        "signed_amount": Decimal("10.00"),
                        "store_id": store,
                        "channel_id": "store",
                        "settlement_reference": reference,
                    }
                    processors.append(CanonicalTransaction(
                        id=f"processor-{index}", transaction_type="Capture",
                        source_lineage=service.LINEAGE["Processor"], reconciliation_status="Matched",
                        **common,
                    ))
                    banks.append(CanonicalTransaction(
                        id=f"bank-{index}", transaction_type="Deposit",
                        source_lineage=service.LINEAGE["Bank"], reconciliation_status="Unmatched",
                        **common,
                    ))
                db.add_all([*processors, *banks])
                await db.flush()

                findings = []
                await reconciliation.run_settlement_controls(db, banks, lambda *args: findings.append(args))

                assert findings == []
                assert all((bank.reconciliation_status, bank.disposition) == ("Matched", "Closed")
                           for bank in banks)
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_referenced_settlement_totals_are_accumulated_in_bounded_pages(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                timestamp = datetime(2026, 10, 1, tzinfo=UTC)
                processor_rows = []
                bank_rows = []
                for index in range(5):
                    common = {
                        "business_date": "2026-10-01",
                        "transaction_date": timestamp,
                        "event_timestamp": timestamp,
                        "processing_timestamp": timestamp,
                        "settlement_date": "2026-10-01",
                        "currency": "USD",
                        "signed_amount": Decimal("10.00"),
                        "store_id": "S1",
                        "channel_id": "store",
                        "settlement_reference": "batch-1",
                    }
                    processor_rows.append(CanonicalTransaction(
                        id=f"processor-{index}", transaction_type="Capture",
                        source_lineage=service.LINEAGE["Processor"], reconciliation_status="Matched",
                        **common,
                    ))
                    bank_rows.append(CanonicalTransaction(
                        id=f"bank-{index}", transaction_type="Deposit",
                        source_lineage=service.LINEAGE["Bank"], reconciliation_status="Unmatched",
                        **common,
                    ))
                db.add_all([*processor_rows, *bank_rows])
                await db.flush()
                monkeypatch.setattr(reconciliation, "SETTLEMENT_QUERY_PAGE_SIZE", 2)
                observed_page_sizes = []
                original_effective_amounts = reconciliation.effective_amounts

                async def track_page_size(session, rows):
                    observed_page_sizes.append(len(rows))
                    return await original_effective_amounts(session, rows)

                monkeypatch.setattr(reconciliation, "effective_amounts", track_page_size)
                findings = []
                await reconciliation.run_settlement_controls(db, bank_rows, lambda *args: findings.append(args))

                assert findings == []
                assert observed_page_sizes and max(observed_page_sizes) <= 2
                assert all((row.reconciliation_status, row.disposition) == ("Matched", "Closed")
                           for row in bank_rows)
        finally:
            await engine.dispose()

    asyncio.run(run())

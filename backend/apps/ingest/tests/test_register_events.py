"""Register events: stored idempotently, audited once, and raised as cases through reconciliation."""

import asyncio
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from apps.ingest import reconciliation, service
from apps.ingest.pos_formats import rtlog, standard
from apps.ingest.register_events import (
    NO_SALE_LIMIT,
    ingest_register_events,
    run_register_event_rules,
)
from models import Base, RegisterEvent
from models import Exception as DBException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SAMPLES = sorted((Path(__file__).resolve().parents[4] / "TLOG").glob("RTLOG_*.DAT"))
DAY = "2014-07-17"


def event(kind, number, register="001", **extra) -> dict:
    return {
        "source_record_id": f"RTLOG-4241-{DAY}-{register}-{number}",
        "store_id": "4241",
        "business_date": DAY,
        "register_id": register,
        "transaction_number": str(number),
        "event_time": f"{DAY}T10:{number % 60:02d}:00+00:00",
        "cashier_id": "C1",
        "salesperson_id": "",
        "type": kind,
        "rtlog_type": kind.upper(),
        "sub_type": "",
        "original_transaction_number": None,
        "original_register_id": None,
        "reason_code": None,
        "vendor": None,
        "value": None,
        "lines": [],
        "discounts": [],
        "taxes": [],
        "tenders": [],
        **extra,
    }


def log_of(*events) -> standard.StandardLog:
    return standard.StandardLog("rtlog", "4241", DAY, f"{DAY}T00:00:00+00:00", "POS", list(events))


def sale_record(number=7, register="001") -> dict:
    return {
        "source_record_id": f"RTLOG-4241-{DAY}-{register}-{number}",
        "record_type": "Sale",
        "event_time": f"{DAY}T09:00:00+00:00",
        "amount": "10.00",
        "store_id": "4241",
        "register_id": register,
        "currency": "USD",
        "payment_reference": f"RTLOG-4241-{DAY}-{register}-{number}",
        "tender_type": "Cash",
        "lines": [{"item_code": "A", "quantity": "1", "unit_price": "10.00", "tax_amount": "0"}],
        "tenders": [{"tender_type": "Cash", "amount": "10.00"}],
    }


def run(*steps):
    """Run steps (async callables taking a session) against a fresh database; return their results."""

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return [await step(db) for step in steps]
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def audit_codes(db, **scope):
    findings = []
    await run_register_event_rules(db, lambda *f: findings.append(f), **scope)
    return sorted(f[0] for f in findings)


def test_events_are_stored_with_their_amounts_and_never_twice():
    paid_out = event(
        "paid_out", 1, tenders=[{"tender_type": "Cash", "amount": "25.00"}], reason_code="SUPPLY"
    )
    total = event("total", 2, value="1234.5")
    sale_like = event("sale", 3)
    log = log_of(paid_out, total, event("no_sale", 4), sale_like)

    async def twice(db):
        first = await ingest_register_events(db, log)
        second = await ingest_register_events(db, log)
        rows = (
            (await db.execute(select(RegisterEvent).order_by(RegisterEvent.transaction_number)))
            .scalars()
            .all()
        )
        return first, second, [(r.event_type, r.amount) for r in rows]

    [(first, second, rows)] = run(twice)
    assert first == {
        "received": 3,
        "created": 3,
        "duplicates_skipped": 0,
    }  # the sale is not a register event
    assert second == {"received": 3, "created": 0, "duplicates_skipped": 3}
    assert rows == [
        ("paid_out", Decimal("25.00")),
        ("total", Decimal("1234.50")),
        ("no_sale", None),
    ]


def test_a_paid_in_or_out_without_a_reason_is_flagged_and_one_with_a_reason_is_not():
    async def go(db):
        await ingest_register_events(
            db,
            log_of(
                event("paid_out", 1),
                event("paid_in", 2),
                event("paid_out", 3, reason_code="SUPPLY"),
                event("pull", 4),
            ),
        )
        return await audit_codes(db)

    assert run(go) == [["PAIDOUT_REASON_REQ", "PAIDOUT_REASON_REQ"]]


def test_a_post_void_needs_its_original_on_file():
    async def go(db):
        await service.ingest(db, "POS", "d1", [sale_record(7)])
        await ingest_register_events(
            db,
            log_of(
                event("post_void", 20, original_transaction_number="7"),  # a stored sale
                event("post_void", 21, original_transaction_number="99"),  # nothing on file
                event("no_sale", 5),
                event("post_void", 22, original_transaction_number="5"),  # another register event
            ),
        )
        return await audit_codes(db)

    assert run(go) == [["VOID_NO_ORIG"]]


def test_a_post_void_that_names_no_original_at_all_is_flagged():
    async def go(db):
        await ingest_register_events(db, log_of(event("post_void", 20)))
        return await audit_codes(db)

    assert run(go) == [["VOID_NO_ORIG"]]


@pytest.mark.parametrize(("count", "flagged"), [(NO_SALE_LIMIT, 0), (NO_SALE_LIMIT + 1, 1)])
def test_excess_no_sales_are_flagged_once_per_register_and_day(count, flagged):
    async def go(db):
        await ingest_register_events(
            db, log_of(*[event("no_sale", n) for n in range(1, count + 1)])
        )
        return await audit_codes(db)

    [codes] = run(go)
    assert codes.count("NOSALE_EXCESS") == flagged


def test_no_sales_are_counted_per_register():
    async def go(db):
        busy = [event("no_sale", n, register="001") for n in range(1, 4)]
        other = [event("no_sale", n, register="002") for n in range(10, 13)]
        await ingest_register_events(db, log_of(*busy, *other))
        return await audit_codes(db)

    assert run(go) == [[]]


def test_events_are_audited_once_and_the_scope_is_respected():
    async def go(db):
        await ingest_register_events(db, log_of(event("paid_out", 1)))
        elsewhere = await audit_codes(db, business_date="2020-01-01")
        other_store = await audit_codes(db, store_ids={"9999"})
        first = await audit_codes(db, business_date=DAY, store_ids={"4241"})
        again = await audit_codes(db)
        flagged = (await db.execute(select(RegisterEvent.audit_code, RegisterEvent.audited))).all()
        return elsewhere, other_store, first, again, flagged

    [(elsewhere, other_store, first, again, flagged)] = run(go)
    assert (elsewhere, other_store, first, again) == ([], [], ["PAIDOUT_REASON_REQ"], [])
    assert flagged == [("PAIDOUT_REASON_REQ", True)]


def test_reconciliation_raises_register_findings_as_cases():
    async def go(db):
        await ingest_register_events(
            db,
            log_of(
                event("paid_out", 1, tenders=[{"tender_type": "Cash", "amount": "40.00"}]),
                event("post_void", 2),
            ),
        )
        result = await reconciliation.reconcile(db)
        types = (
            await db.execute(select(DBException.exception_type, DBException.exception_amount))
        ).all()
        return result["exceptions"], sorted((t, str(a)) for t, a in types)

    [(counts, rows)] = run(go)
    assert counts == {"PAIDOUT_REASON_REQ": 1, "VOID_NO_ORIG": 1}
    assert rows == [("PAIDOUT_REASON_REQ", "40.00"), ("VOID_NO_ORIG", "0.00")]


@pytest.mark.skipif(not SAMPLES, reason="sample RTLOG files are not in TLOG/")
def test_sample_files_register_events_are_stored_and_audited():
    logs = [rtlog.parse_rtlog_file(path) for path in SAMPLES]
    expected = sum(len(standard.register_events(log)) for log in logs)
    busy = {
        key
        for key, n in Counter(
            (e["store_id"], e["business_date"], e["register_id"])
            for log in logs
            for e in standard.register_events(log)
            if e["type"] == "no_sale"
        ).items()
        if n > NO_SALE_LIMIT
    }

    async def go(db):
        created = 0
        for log in logs:
            created += (await ingest_register_events(db, log))["created"]
        findings = []
        await run_register_event_rules(db, lambda *f: findings.append(f))
        kinds = (await db.execute(select(RegisterEvent.event_type))).scalars().all()
        return created, Counter(f[0] for f in findings), Counter(kinds)

    [(created, findings, kinds)] = run(go)
    assert created == expected == 162
    assert {"void", "no_sale", "open", "close", "total", "day_close"} <= set(kinds)
    assert findings.get("NOSALE_EXCESS", 0) == len(busy)
    assert "VOID_NO_ORIG" not in findings and "PAIDOUT_REASON_REQ" not in findings


def test_the_pos_log_endpoint_ingests_sales_and_stores_register_events():
    from apps.ingest.contracts import PosLogRequest
    from apps.ingest.routes import ingest_pos_log
    from fastapi import HTTPException

    # a file with a header and no transactions is still a valid file
    text = "FHEAD0000000001RTLG20150507064837201407174241      V.14.0\n"
    request = PosLogRequest(format="rtlog", delivery_id="log-1", content=text)

    async def go(db):
        result = await ingest_pos_log(request, db, {"role": "it"})
        bad = None
        try:
            await ingest_pos_log(
                PosLogRequest(format="rtlog", delivery_id="x", content="nope"), db, {"role": "it"}
            )
        except HTTPException as exc:
            bad = exc.status_code
        return result, bad

    [(result, bad)] = run(go)
    assert (result["format"], result["store_id"], result["business_date"]) == (
        "rtlog",
        "4241",
        "2014-07-17",
    )
    assert result["transactions"] == 0 and result["sales_and_returns"]["received"] == 0
    assert bad == 422


@pytest.mark.skipif(not SAMPLES, reason="sample RTLOG files are not in TLOG/")
def test_the_pos_log_endpoint_takes_real_files_and_a_second_delivery_adds_nothing():
    from apps.ingest.contracts import PosLogRequest
    from apps.ingest.routes import ingest_pos_log

    async def go(db):
        out = []
        for n, path in enumerate(SAMPLES):
            request = PosLogRequest(
                format="rtlog", delivery_id=f"d{n}", content=path.read_text(encoding="ascii")
            )
            first = await ingest_pos_log(request, db, {"role": "it"})
            second = await ingest_pos_log(
                request.model_copy(update={"delivery_id": f"again-{n}"}), db, {"role": "it"}
            )
            out.append((path, first, second))
        return out

    [results] = run(go)
    unmapped = {}
    for path, first, second in results:
        log = rtlog.parse_rtlog_file(path)
        mapped, _ = standard.ingest_records(log)
        assert first["problems"] == [] and first["transactions"] == len(log.transactions)
        assert first["sales_and_returns"]["canonical_created"] == len(mapped)
        assert first["register_events"]["created"] == len(standard.register_events(log))
        assert (
            second["sales_and_returns"]["canonical_created"] == 0
            and second["register_events"]["created"] == 0
        )
        unmapped.update({x["transaction_number"]: x["type"] for x in first["not_mapped"]})
    # a sale with no item lines, and a return that also sells an item (an exchange)
    assert unmapped == {"1290000035": "sale", "1290000044": "return"}

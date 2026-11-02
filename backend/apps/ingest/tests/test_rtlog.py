"""RTLOG parsing into the standard POS log, then ingest."""

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from apps.ingest import reconciliation, service
from apps.ingest.pos_formats import rtlog, standard
from models import Base, CanonicalTransaction
from models import Exception as DBException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SAMPLES = sorted((Path(__file__).resolve().parents[4] / "TLOG").glob("RTLOG_*.DAT"))
NUMERIC = {
    "line_id", "transaction_number", "original_transaction_number", "quantity", "unit_retail",
    "original_unit_retail", "unit_discount", "amount", "rate", "record_count", "value", "department",
    "class", "subclass", "reference",
}  # fmt: skip


def num(value, width) -> str:
    return str(value).rjust(width, "0")


def render(kind: str, **values) -> str:
    """One fixed-width record built from the layout table: numbers right-aligned with zeros, text left-aligned."""
    out = []
    for name, width in rtlog.LAYOUTS[kind]:
        value = values.get(name, "")
        if name == "descriptor":
            value = kind
        out.append(
            num(value, width) if name in NUMERIC and value != "" else str(value).ljust(width)
        )
    return "".join(out)


class Builder:
    """Assembles a file with correct line ids and trailer counts, unless told otherwise."""

    def __init__(self):
        self.lines, self.bad_tail = [], {}

    def add(self, kind, **values):
        self.lines.append((kind, values))

    def head(self, **values):
        self.add(
            "THEAD",
            **{
                "register": "001",
                "event_time": "20140717101500",
                "type": "SALE",
                "sub_type": "SALE",
                **values,
            },
        )

    def item(self, code="SKU1", qty="10000", unit="100000", sign="P", status="S", **values):
        self.add(
            "TITEM",
            status=status,
            item_type="ITEM",
            item=code,
            quantity=qty,
            quantity_sign=sign,
            unit_retail=unit,
            taxable="Y",
            **values,
        )

    def tax(self, amount="5250", rate="52500", sign="P"):
        self.add("IGTAX", tax_code="DSTRCT", rate=rate, amount_sign=sign, amount=amount)

    def tender(self, group="CASH", amount="105250", sign="P", **values):
        self.add("TTEND", group=group, type_id="1000", sign=sign, amount=amount, **values)


def build(*transactions) -> str:
    """Each transaction is a function filling a Builder; trailers are added with true counts."""
    lines = [
        render(
            "FHEAD",
            line_id=1,
            file_type="RTLG",
            created_at="20150507064837",
            business_date="20140717",
            location="4241",
            reference="V.14.0",
            origin_system="POS",
        )
    ]
    line_id = 1
    for fill, tail_override in transactions:
        b = Builder()
        fill(b)
        count = len(b.lines) - 1
        for kind, values in b.lines:
            line_id += 1
            lines.append(render(kind, line_id=line_id, **values))
        line_id += 1
        lines.append(
            render(
                "TTAIL",
                line_id=line_id,
                record_count=count if tail_override is None else tail_override,
            )
        )
    lines.append(render("FTAIL", line_id=line_id + 1, record_count=len(lines) - 1))
    return "\n".join(lines)


def sale(b):
    b.head(transaction_number=7, cashier="C9")
    b.item("SHIRT", qty="20000", unit="100000")  # 2 x 10.00
    b.tax(amount="10500", rate="52500")  # 1.05 at 5.25%
    b.tender(amount="210500")  # 21.05


def run(text: str):
    return rtlog.parse_rtlog(text)


def test_a_sale_becomes_a_standard_transaction_with_tax_and_tender():
    log = run(build((sale, None)))
    assert (log.store_id, log.business_date, log.origin_system, log.problems) == (
        "4241",
        "2014-07-17",
        "POS",
        [],
    )
    [tx] = log.transactions
    assert (tx["type"], tx["transaction_number"], tx["register_id"], tx["cashier_id"]) == (
        "sale",
        "7",
        "001",
        "C9",
    )
    [line] = tx["lines"]
    assert (line["item_code"], line["quantity"], line["unit_price"]) == ("SHIRT", "2", "10")
    assert (line["tax_code"], line["tax_rate"], line["tax_amount"]) == ("DSTRCT", "0.0525", "1.05")
    assert tx["tenders"][0]["tender_type"] == "Cash" and tx["tenders"][0]["amount"] == "21.05"
    assert standard.net_amount(tx) == Decimal("21.05")


def test_the_standard_sale_maps_to_a_pos_ingest_record():
    [tx] = run(build((sale, None))).transactions
    record = standard.ingest_record(tx, "USD")
    assert (record["record_type"], record["amount"], record["store_id"], record["register_id"]) == (
        "Sale",
        "21.05",
        "4241",
        "001",
    )
    assert record["lines"][0]["gross_amount"] == "20" and record["lines"][0]["tax_amount"] == "1.05"
    assert record["tenders"] == [{"tender_type": "Cash", "amount": "21.05", "reference": None}]


def test_change_comes_off_the_tender_that_was_overpaid():
    def paid_with_check(b):
        b.head(transaction_number=1)
        b.item("A", unit="100000")
        b.tax(amount="5250")
        b.tender("CHECK", amount="200000")  # 20.00 handed over
        b.tender("CASH", amount="94750", sign="N")  # 9.475 of change

    [tx] = run(build((paid_with_check, None))).transactions
    record = standard.ingest_record(tx, "USD")
    assert [(t["tender_type"], t["amount"]) for t in record["tenders"]] == [("Other", "10.525")]


def test_a_return_maps_to_positive_amounts_on_a_return_record():
    def returned(b):
        b.head(transaction_number=2, type="RETURN", sub_type="RETURN", reason_code="DEFECT")
        b.item("A", sign="N", status="R", unit="100000")
        b.tax(amount="5250", sign="N")
        b.tender(amount="105250", sign="N")

    [tx] = run(build((returned, None))).transactions
    record = standard.ingest_record(tx, "USD")
    assert (record["record_type"], record["amount"], record["return_reason"]) == (
        "Return",
        "10.525",
        "DEFECT",
    )
    assert record["tenders"][0]["amount"] == "10.525"


def test_a_scanned_then_voided_line_nets_out_of_the_sale():
    def with_void(b):
        b.head(transaction_number=3)
        b.item("A", unit="100000")
        b.tax(amount="5250")
        b.item("B", unit="50000")
        b.tax(amount="2625")
        b.item("B", sign="N", status="V", unit="50000")
        b.tax(amount="2625", sign="N")
        b.tender(amount="105250")

    [tx] = run(build((with_void, None))).transactions
    record = standard.ingest_record(tx, "USD")
    assert [x["item_code"] for x in record["lines"]] == ["A"] and record["amount"] == "10.525"


def test_a_return_that_also_sells_an_item_is_left_for_exchange_handling():
    def exchange(b):
        b.head(transaction_number=4, type="RETURN", sub_type="RETURN")
        b.item("A", sign="N", status="R")
        b.item("B", unit="10000")

    [tx] = run(build((exchange, None))).transactions
    assert standard.ingest_record(tx, "USD") is None


def test_register_events_are_kept_apart_from_sales():
    def events(b):
        b.head(transaction_number=5, type="NOSALE", sub_type="TCHG")

    def paid_out(b):
        b.head(transaction_number=6, type="PAIDOU", sub_type="MV", reason_code="SUPPLY")
        b.tender(amount="250000")

    def voided(b):
        b.head(
            transaction_number=8,
            type="PVOID",
            original_transaction_number=7,
            original_register="001",
        )

    log = run(build((sale, None), (events, None), (paid_out, None), (voided, None)))
    mapped, rest = standard.ingest_records(log)
    assert len(mapped) == 1
    assert [t["type"] for t in standard.register_events(log)] == [
        "no_sale",
        "paid_out",
        "post_void",
    ]
    assert standard.register_events(log)[2]["original_transaction_number"] == "7"
    assert standard.register_events(log)[1]["reason_code"] == "SUPPLY"
    assert len(rest) == 3


def test_counts_that_do_not_add_up_are_reported_not_raised():
    log = run(build((sale, 9)))
    assert any("TTAIL says 9 records, found 3" in p for p in log.problems)
    text = (
        build((sale, None)).rsplit("\n", 1)[0] + "\n" + render("FTAIL", line_id=99, record_count=50)
    )
    assert any("FTAIL says 50" in p for p in run(text).problems)


def test_an_unknown_transaction_type_is_kept_and_reported():
    def odd(b):
        b.head(transaction_number=9, type="WEIRD")

    log = run(build((odd, None)))
    assert log.transactions[0]["type"] == "weird" and any(
        "unknown transaction type" in p for p in log.problems
    )


def test_a_file_without_fhead_is_refused():
    with pytest.raises(ValueError, match="not an RTLOG file"):
        rtlog.parse_rtlog("THEAD0000000001\n")


@pytest.mark.skipif(not SAMPLES, reason="sample RTLOG files are not in TLOG/")
def test_sample_files_parse_cleanly_and_match_their_trailers():
    counts = {}
    for path in SAMPLES:
        log = rtlog.parse_rtlog_file(path)
        assert log.problems == [], (path.name, log.problems)
        counts[path.name[-18:-4]] = len(log.transactions)
    assert sorted(counts.values()) == [17, 52, 146]


@pytest.mark.skipif(not SAMPLES, reason="sample RTLOG files are not in TLOG/")
def test_sample_files_flow_through_ingest_and_the_audit_finds_the_one_real_variance():
    records = []
    for path in SAMPLES:
        mapped, _ = standard.ingest_records(rtlog.parse_rtlog_file(path))
        records += mapped

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                result = await service.ingest(db, "POS", "rtlog", records)
                statuses = (
                    (await db.execute(select(CanonicalTransaction.detail_status))).scalars().all()
                )
                await reconciliation.reconcile(db)
                types = (await db.execute(select(DBException.exception_type))).scalars().all()
                return result, statuses, types
        finally:
            await engine.dispose()

    result, statuses, types = asyncio.run(go())
    assert (result["received"], result["quarantined"]) == (51, 0)
    assert sorted(set(statuses)) == ["Balanced", "Unbalanced"] and statuses.count("Unbalanced") == 1
    assert types.count("TRAN_OUT_BAL") == 1

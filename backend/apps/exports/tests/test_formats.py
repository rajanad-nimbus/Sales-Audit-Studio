"""Flat and XML payloads: delimiters and markup in the data cannot break the file."""

from xml.etree import ElementTree

from models import ExportDestination

from apps.exports.delivery import render
from apps.exports.formats import element_name, extension, flat_value, media_type

RECORDS = [
    {"journal_id": "J1", "amount": "10.00", "memo": "pipe | and\nnewline", "ref": None},
    {"journal_id": "J2", "amount": "5.50", "memo": "a < b & c", "ref": "R2"},
]


def destination(fmt: str) -> ExportDestination:
    return ExportDestination(id="d", name="ERP", kind="file", config={}, format=fmt)


def test_flat_file_has_a_header_numbered_details_and_a_count_trailer():
    body = render(destination("flat"), "adjustments", RECORDS, None, "key-1").decode()
    lines = body.splitlines()
    assert lines[0].startswith("FHEAD|adjustments|key-1|")
    assert lines[1] == "FCOLS|journal_id|amount|memo|ref"
    assert lines[2] == "FDETL|1|J1|10.00|pipe and newline|"
    assert lines[3] == "FDETL|2|J2|5.50|a < b & c|R2"
    assert lines[-1] == "FTAIL|2"
    assert all(line.count("|") == lines[2].count("|") for line in lines[2:4])


def test_an_empty_flat_file_still_has_header_and_trailer_so_a_short_file_is_detectable():
    lines = render(destination("flat"), "audit", [], None, "key-2").decode().splitlines()
    assert [line.split("|")[0] for line in lines] == ["FHEAD", "FTAIL"]
    assert lines[-1] == "FTAIL|0"


def test_xml_is_well_formed_and_escapes_markup():
    body = render(destination("xml"), "adjustments", RECORDS, None, "key-3")
    root = ElementTree.fromstring(body)
    assert root.tag == "export" and root.attrib["count"] == "2"
    first, second = root.findall("record")
    assert first.find("journal_id").text == "J1" and first.find("ref").text is None
    assert second.find("memo").text == "a < b & c"


def test_format_helpers():
    assert (media_type("flat"), extension("flat")) == ("text/plain", "dat")
    assert (media_type("xml"), extension("xml")) == ("application/xml", "xml")
    assert (media_type("csv"), extension("csv")) == ("text/csv", "csv")
    assert flat_value(None) == "" and flat_value("a|b\r\nc") == "a b c"
    assert element_name("Total name") == "Total_name" and element_name("1st") == "_1st"


def test_destinations_accept_only_known_formats():
    import asyncio

    import pytest
    from fastapi import HTTPException
    from models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from apps.exports.destinations import create_destination
    from apps.exports.schemas import DestIn

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = {"user": "it", "role": "it"}
                for fmt in ("flat", "xml"):
                    made = await create_destination(
                        DestIn(name=f"GL-{fmt}", kind="file", format=fmt), db, user
                    )
                    assert made["format"] == fmt
                with pytest.raises(HTTPException) as refused:
                    await create_destination(
                        DestIn(name="Bad", kind="file", format="pdf"), db, user
                    )
                assert refused.value.status_code == 400 and "flat" in refused.value.detail
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_flat_and_xml_files_are_delivered_with_their_own_extension(tmp_path, monkeypatch):
    import asyncio
    import hashlib
    from pathlib import Path

    from models import ExportBatch

    from apps.exports import routes

    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path)

    async def run():
        for fmt, suffix in (("flat", ".dat"), ("xml", ".xml")):
            dest = destination(fmt)
            dest.id, dest.name = f"d-{fmt}", f"GL {fmt}"
            body = render(dest, "adjustments", RECORDS, None, "key").decode()
            batch = ExportBatch(
                id=f"batch-{fmt}",
                dataset="adjustments",
                destination_id=dest.id,
                idempotency_key=f"key-{fmt}",
                created_by="it",
                payload=body,
                payload_hash=hashlib.sha256(body.encode()).hexdigest(),
            )
            await routes.deliver(dest, batch)
            path = Path(batch.response_ref)
            assert batch.status == "Sent" and path.suffix == suffix
            assert path.read_text() == body

    asyncio.run(run())

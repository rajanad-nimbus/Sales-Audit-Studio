import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.audit.models import AuditEvent
from apps.ingest.common import LINEAGE
from apps.store_days.duplicates import reopen_auto_resolved, resolve_duplicate_cases
from models import (
    Base,
    CanonicalTransaction,
    Case,
    SourceRecord,
)
from models import Exception as DBException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DAY = "2026-10-01"
NOW = datetime(2026, 10, 1, 10, tzinfo=UTC)


def _order(key, minutes, reference, amount="20"):
    return SourceRecord(
        id=f"sr-{key}",
        source_system="Shopify",
        source_version="1",
        source_record_id=key,
        delivery_id="d",
        payload=b"{}",
        payload_hash=key,
        received_at=NOW + timedelta(minutes=minutes),
        source_event_time=NOW,
    ), CanonicalTransaction(
        id=f"tx-{key}",
        source_record_id=f"sr-{key}",
        business_date=DAY,
        transaction_date=NOW.date(),
        event_timestamp=NOW,
        processing_timestamp=NOW,
        settlement_date=NOW.date(),
        transaction_type="Order",
        source_lineage=LINEAGE["Shopify"],
        signed_amount=Decimal(amount),
        store_id="S1",
        channel_id="web",
        currency="USD",
        payment_reference=reference,
    )


def _run(copy_reference, copy_amount="20", *, reopen=False):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for record, tx in (
                    _order("first", 0, "ORD-1"),
                    _order("second", 5, copy_reference, copy_amount),
                ):
                    db.add_all([record, tx])
                db.add(
                    Case(
                        id="c",
                        case_number="C-1",
                        case_type="Duplicate Sales",
                        business_date=DAY,
                        store_id="S1",
                    )
                )
                db.add(
                    DBException(
                        id="e",
                        case_id="c",
                        exception_type="SHOPIFY_DUPLICATE_ORDER",
                        exception_family="Duplicate",
                        source_system="Shopify",
                        detection_origin="rule",
                        detection_rule_id="r",
                        exception_amount=Decimal("20"),
                        estimated_exposure=Decimal("20"),
                        severity="Low",
                        confidence="High",
                        close_impact="None",
                    )
                )
                db.add(
                    AuditEvent(
                        id="a",
                        seq=1,
                        case_id="c",
                        event_type="CASE_CREATED",
                        actor="x",
                        object_type="Case",
                        object_id="c",
                        action_description="d",
                        after_state={"transaction_ids": ["tx-second"]},
                    )
                )
                await db.commit()
                resolved = await resolve_duplicate_cases(db, "S1", DAY, "agent")
                await db.commit()
                if reopen:
                    await reopen_auto_resolved(db, await db.get(Case, "c"), "finance", "wrongly merged")
                    await db.commit()
                case = await db.get(Case, "c")
                copy = await db.get(CanonicalTransaction, "tx-second")
                return resolved, case.investigation_status, copy.disposition
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_exact_shopify_duplicate_order_is_resolved_by_the_agent():
    assert _run("ORD-1") == (1, "Auto-Resolved", "Duplicate")


def test_shopify_order_with_a_different_order_id_is_left_for_a_person():
    resolved, status, disposition = _run("ORD-2")
    assert resolved == 0 and status != "Auto-Resolved" and disposition != "Duplicate"


def test_reopening_restores_the_excluded_sale_and_the_case():
    resolved, status, disposition = _run("ORD-1", reopen=True)
    assert resolved == 1 and status == "Pending" and disposition != "Duplicate"


def test_nothing_is_resolved_while_the_switch_is_off(monkeypatch):
    monkeypatch.setenv("NIMBUS_ACT_DUPLICATE", "0")
    assert _run("ORD-1")[0] == 0


def test_the_daily_cap_stops_the_agent(monkeypatch):
    monkeypatch.setenv("NIMBUS_ACT_DAILY_CAP", "0")
    assert _run("ORD-1")[0] == 0


def test_the_ontology_can_take_the_duplicate_class_off_act(monkeypatch):
    monkeypatch.setattr(
        "integrations.ontology.nimbus.agent_level",
        lambda code: "propose" if code == "SHOPIFY_DUPLICATE_ORDER" else "act",
    )
    assert _run("ORD-1")[0] == 0

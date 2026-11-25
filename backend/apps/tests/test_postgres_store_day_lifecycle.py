"""One store day, end to end on PostgreSQL: import, audit, correct, sign off, close, export, acknowledge.

Every step is the real route function against a real database with foreign keys, row locks and the
export outbox enforced, so a gap between stages shows up here rather than in production.
"""

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from models import (
    AuditEvent,
    Base,
    Case,
    ExportBatch,
    ExportDestination,
    StoreDay,
    TransactionAdjustment,
)
from models import Exception as DBException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.audit.error_routes import (
    ExceptionOverrideRequest,
    approve_exception_override,
    request_exception_override,
)
from apps.corrections.routes import approve_adjustment
from apps.exports import routes as exports
from apps.exports.schemas import AckIn, RunIn
from apps.ingest import reconciliation
from apps.ingest import service as ingest
from apps.store_days import closures
from apps.store_days.closures import (
    AuditSignoffBody,
    ClosureRequestBody,
    request_store_day_closure,
    sign_off_store_day_audit,
)
from apps.store_days.readiness import readiness
from apps.store_days.routes import get_store_day, open_store_day_record, retotal_store_day

POSTGRES_URL = os.getenv("ZETSA_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="ZETSA_TEST_POSTGRES_URL not configured")

STORE, DAY = "S1", "2026-10-01"
IT = {"user": "it.operator", "role": "it"}
FINANCE = {"user": "finance.lead", "role": "finance"}
AUDITOR = {"user": "sales.auditor", "role": "analyst"}
SECOND_FINANCE = {"user": "finance.approver", "role": "finance"}


def sale(record_id: str, amount: str) -> dict:
    return {
        "source_record_id": record_id,
        "record_type": "Sale",
        "event_time": f"{DAY}T10:00:00Z",
        "store_id": STORE,
        "register_id": "R1",
        "amount": amount,
        "currency": "USD",
        "lines": [
            {
                "item_code": "SKU-1",
                "description": "Shirt",
                "quantity": "1",
                "unit_price": amount,
                "gross_amount": amount,
                "tax_rate": "0",
                "tax_amount": "0",
            }
        ],
        "tenders": [{"tender_type": "Cash", "amount": amount}],
    }


@pytest.mark.parametrize("acknowledged", [True, False], ids=["acknowledged", "unacknowledged"])
def test_a_store_day_travels_from_import_to_export_and_reopens_only_before_acknowledgement(
    monkeypatch, tmp_path, acknowledged
):
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)

    async def run():
        schema = "zetsa_test_" + uuid.uuid4().hex
        admin = create_async_engine(POSTGRES_URL)
        engine = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)

            async with sessions() as db:
                db.add(
                    ExportDestination(
                        id="gl", name="ERP / GL", kind="file", format="json", config={}
                    )
                )
                # 1. Import: one sale, but the store's control total says two sales for 30.00.
                await ingest.ingest(
                    db,
                    "POS",
                    "delivery-1",
                    [sale("t1", "10.00")],  # the second sale never arrives
                )
                await ingest.ingest(
                    db,
                    "POSControl",
                    "control-1",
                    [
                        {
                            "source_record_id": "control",
                            "store_id": STORE,
                            "business_date": DAY,
                            "declared_count": 2,
                            "declared_total": "30.00",
                        }
                    ],
                )
                # The ledger posts the day's sales, so POS and GL agree.
                await ingest.ingest(
                    db,
                    "ERP",
                    "gl-1",
                    [
                        {
                            "source_record_id": "gl-posting",
                            "record_type": "Posting",
                            "event_time": f"{DAY}T23:00:00Z",
                            "store_id": STORE,
                            "amount": "30.00",
                            "currency": "USD",
                        }
                    ],
                )
                await db.commit()

                # 2. Audit: reconciliation sees fewer transactions than the control total.
                await reconciliation.reconcile(db, DAY)
                await db.commit()
                await open_store_day_record(db, STORE, DAY, IT["user"])
                await retotal_store_day(STORE, DAY, db, FINANCE)
                view = (await get_store_day(STORE, DAY, db=db, user=FINANCE))["readiness"]
                assert view["ingest"]["state"] == "Incomplete"
                assert view["audit"]["state"] == "Needs attention"
                assert not view["ready_to_close"]
                with pytest.raises(HTTPException) as early:
                    await sign_off_store_day_audit(
                        STORE, DAY, AuditSignoffBody(rationale="Looks fine to me"), db, AUDITOR
                    )
                assert early.value.status_code == 422

                # 3. Correct: Finance approves a repair for the missing sale, then the day is re-totaled.
                db.add(
                    TransactionAdjustment(
                        id="missing-sale",
                        adjustment_type="MissingTransaction",
                        status="Proposed",
                        proposed_values={
                            "business_date": DAY,
                            "store_id": STORE,
                            "register_id": "R1",
                            "amount": "20.00",
                            "transaction_type": "Sale",
                            "source_system": "POS",
                        },
                        rationale="Receipt found in the register journal",
                        proposed_by=AUDITOR["user"],
                    )
                )
                await db.commit()
                repaired = await approve_adjustment("missing-sale", db, FINANCE)
                assert repaired["transaction_id"]
                # The auditor closes the errors the repair answers (the feed gap and the GL difference); Finance approves.
                gap_errors = (
                    (await db.execute(select(DBException).where(DBException.status == "Open")))
                    .scalars()
                    .all()
                )
                assert {error.exception_type for error in gap_errors} == {
                    "INCOMPLETE_FEED",
                    "GL_VARIANCE",
                }
                for error in gap_errors:
                    await request_exception_override(
                        error.exception_type,
                        error.id,
                        ExceptionOverrideRequest(reason=f"Repaired by correction {repaired['id']}"),
                        db,
                        AUDITOR,
                    )
                    await approve_exception_override(error.exception_type, error.id, db, FINANCE)
                await retotal_store_day(STORE, DAY, db, FINANCE)
                view = (await get_store_day(STORE, DAY, db=db, user=FINANCE))["readiness"]
                assert view["ingest"]["state"] == "Complete", view["ingest"]
                open_now = (
                    await db.execute(
                        select(
                            Case.case_type,
                            Case.status,
                            DBException.exception_type,
                            DBException.status,
                        ).join(DBException, DBException.case_id == Case.id)
                    )
                ).all()
                assert view["audit"]["state"] == "Clean", (view["audit"]["detail"], open_now)
                assert not view["audit"]["signed_off"] and not view["ready_to_close"]

                # 4. Closing without sign-off is refused, and so is requesting it.
                with pytest.raises(HTTPException) as unsigned:
                    await request_store_day_closure(
                        STORE, DAY, ClosureRequestBody(evidence_basis="Reviewed"), db, IT
                    )
                assert unsigned.value.status_code == 422 and "sign-off" in unsigned.value.detail

                # 5. The auditor signs off the current totals; the day is ready to close.
                signed = await sign_off_store_day_audit(
                    STORE,
                    DAY,
                    AuditSignoffBody(rationale="Totals match the control total"),
                    db,
                    AUDITOR,
                )
                assert signed["signed_off"] and signed["signed_by"] == AUDITOR["user"]
                day = await db.get(StoreDay, (await _day_id(db)))
                assert (await readiness(db, [day]))[day.id]["ready_to_close"]

                # 6. Request, then a different finance user approves the closure.
                await request_store_day_closure(
                    STORE, DAY, ClosureRequestBody(evidence_basis="Totals reviewed"), db, IT
                )
                with pytest.raises(HTTPException) as same_user:
                    await closures.close_store_day(
                        STORE, DAY, db, {"user": IT["user"], "role": "finance"}
                    )
                assert same_user.value.status_code == 403
                closed = await closures.close_store_day(STORE, DAY, db, SECOND_FINANCE)
                assert closed["status"] == "Closed"

                # 7. A closed day takes no more deliveries into its totals without a reopen.
                detail = await get_store_day(STORE, DAY, db=db, user=FINANCE)
                assert detail["readiness"]["export"]["state"] == "Not exported"

                # 8. Export the audited totals and transactions, then acknowledge them.
                for dataset in ("audited_totals", "audited_transactions"):
                    sent = await exports.run_export(
                        RunIn(destination_id="gl", dataset=dataset), db, IT
                    )
                    assert sent["status"] in ("Sent", "Acknowledged")
                batches = (await db.execute(select(ExportBatch))).scalars().all()
                assert {b.dataset for b in batches} == {"audited_totals", "audited_transactions"}
                if acknowledged:
                    for batch in batches:
                        if batch.status == "Sent":
                            await exports.acknowledge(batch.id, AckIn(reference="ERP-1"), db, IT)
                await db.commit()
                db.expire_all()
                statuses = {
                    b.status for b in (await db.execute(select(ExportBatch))).scalars().all()
                }
                assert statuses == ({"Acknowledged"} if acknowledged else {"Sent"})
                assert any(tmp_path.iterdir())  # the files were really written

                detail = await get_store_day(STORE, DAY, db=db, user=FINANCE)
                assert detail["readiness"]["export"]["state"] == (
                    "Final" if acknowledged else "Exported"
                )

                # 9. Acknowledged exports are final and the day can only be corrected later; until
                # the destination acknowledges, Finance may still reopen it, and that is recorded.
                reopen = closures.reopen_store_day(
                    STORE,
                    DAY,
                    closures.ReopenRequest(rationale="Found another error"),
                    db,
                    FINANCE,
                )
                if acknowledged:
                    with pytest.raises(HTTPException) as reopened:
                        await reopen
                    assert reopened.value.status_code == 409
                    assert "exports are final" in reopened.value.detail
                    assert (await db.get(StoreDay, await _day_id(db))).status == "Closed"
                else:
                    assert (await reopen)["status"] == "Reopened"
                    kinds_now = {
                        e.event_type for e in (await db.execute(select(AuditEvent))).scalars().all()
                    }
                    assert "STORE_DAY_REOPENED" in kinds_now

                # The audit trail holds every stage, in order.
                events = (
                    (await db.execute(select(AuditEvent).order_by(AuditEvent.created_at)))
                    .scalars()
                    .all()
                )
                kinds = [event.event_type for event in events]
                for expected in (
                    "STORE_DAY_AUDIT_SIGNED_OFF",
                    "STORE_DAY_CLOSED",
                ):
                    assert expected in kinds
                assert kinds.index("STORE_DAY_AUDIT_SIGNED_OFF") < kinds.index("STORE_DAY_CLOSED")
        finally:
            if engine:
                await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin.dispose()

    asyncio.run(run())


async def _day_id(db) -> str:
    return (
        await db.execute(
            select(StoreDay.id).where(StoreDay.store_id == STORE, StoreDay.business_date == DAY)
        )
    ).scalar_one()

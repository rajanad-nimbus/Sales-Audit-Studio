"""Export back-posting and IT-initiated Store Days."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from apps.exports import routes as exports
from apps.ingest import service as ingest
from apps.store_days import closures
from apps.store_days import routes as sd
from fastapi import HTTPException
from models import (
    AuditTotal,
    Base,
    Case,
    EvidenceRequest,
    ExportBatch,
    ExportDestination,
    ExportItem,
    FoundationReference,
    StoreDay,
    StoreDayClosureRequest,
)
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def run(c):
    return asyncio.run(c)


async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class TestSession(AsyncSession):
        async def __aexit__(self, exc_type, exc, tb):
            try:
                return await super().__aexit__(exc_type, exc, tb)
            finally:
                await engine.dispose()

    return async_sessionmaker(engine, expire_on_commit=False, class_=TestSession)


# ---- classification -------------------------------------------------------------------------------------------
def test_classification_current_late_addition_and_after_business_date():
    prior = {("S1", "2026-10-01"): ["abcd1234"]}
    assert exports.classify_backpost("S1", "2026-10-05", "2026-10-05", prior) == (False, None, [])
    assert exports.classify_backpost("S1", "2026-10-01", "2026-10-01", prior) == (
        True,
        "Late addition to a business day already exported",
        ["abcd1234"],
    )
    assert exports.classify_backpost("S2", "2026-10-01", "2026-10-02", prior) == (
        True,
        "Resolved after the business date ended",
        [],
    )


def test_summary_and_scope_keep_records_and_items_aligned():
    recs = [
        {"business_date": "2026-10-01", "backpost": True},
        {"business_date": "2026-10-05", "backpost": False},
        {"business_date": "2026-10-02", "backpost": True},
    ]
    assert exports.summarize(recs) == {
        "current": 1,
        "backposted": 2,
        "business_dates": ["2026-10-01", "2026-10-02"],
    }


# ---- build() against a real (sqlite) schema -------------------------------------------------------------------
def test_build_flags_late_additions_and_late_closures_but_not_same_day_resolutions():
    async def go():
        Session = await session()
        today = datetime.now(UTC).date().isoformat()
        old = (datetime.now(UTC) - timedelta(days=3)).date().isoformat()
        async with Session() as db:
            dest = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
            db.add(dest)

            def make_case(case_id, store_id, business_date):
                return Case(
                    id=case_id,
                    case_number=case_id.upper(),
                    case_type="x",
                    business_date=business_date,
                    store_id=store_id,
                    status="Closed",
                )

            db.add_all(
                [
                    make_case("c-sent", "S1", old),
                    make_case("c-late", "S1", old),
                    make_case("c-old", "S2", old),
                    make_case("c-now", "S3", today),
                ]
            )
            db.add(
                ExportBatch(
                    id="batch-0001-xyz",
                    destination_id="d1",
                    dataset="resolutions",
                    status="Acknowledged",
                    idempotency_key="k1",
                    created_by="it",
                    record_count=1,
                )
            )
            await db.flush()
            db.add(
                ExportItem(
                    id=str(uuid.uuid4()),
                    batch_id="batch-0001-xyz",
                    destination_id="d1",
                    dataset="resolutions",
                    object_id="c-sent",
                    object_version="v1",
                    state="Delivered",
                )
            )
            await db.commit()

            records, items, *_ = await exports.build(db, dest, "resolutions")
            by = {r["case_number"]: r for r in records}
            assert set(by) == {"C-LATE", "C-OLD", "C-NOW"}  # c-sent was already delivered
            assert by["C-LATE"]["backpost"] and by["C-LATE"]["backpost_reason"].startswith(
                "Late addition"
            )
            assert by["C-LATE"]["prior_batches"] == "batch-00"
            assert (
                by["C-OLD"]["backpost"]
                and by["C-OLD"]["backpost_reason"] == "Resolved after the business date ended"
            )
            assert not by["C-NOW"]["backpost"] and by["C-NOW"]["backpost_reason"] is None
            assert all("posting_date" in r and "business_date" in r for r in records)
            assert exports.summarize(records)["backposted"] == 2

    run(go())


# ---- Store Days: opened by IT, calculated by re-total ---------------------------------------------------------
def test_open_store_day_validates_store_date_and_duplicates():
    async def go():
        Session = await session()
        async with Session() as db:
            db.add(
                FoundationReference(id="f1", category="Store", code="S1", name="One", active=True)
            )
            await db.commit()
            day = await sd.open_store_day_record(db, "S1", "2026-10-01", "it.admin")
            assert day.status == "Open" and day.audit_version == 0
            for args, code in (
                (("S1", "2026-10-01"), 409),
                (("S9", "2026-10-01"), 422),
                (("S1", "2999-01-01"), 422),
                (("S1", "01/10/2026"), 422),
            ):
                with pytest.raises(HTTPException) as e:
                    await sd.open_store_day_record(db, *args, "it.admin")
                assert e.value.status_code == code

    run(go())


def test_store_day_close_acquires_reconciliation_lock_before_loading_day(monkeypatch):
    calls = []

    async def lock_reconciliation(_db):
        calls.append("reconciliation lock")

    async def get_day(_db, _store_id, _business_date, *, lock=False):
        calls.append("store day")
        raise HTTPException(status_code=404, detail="Store day not found")

    monkeypatch.setattr(sd.reconciliation, "lock_reconciliation", lock_reconciliation)
    monkeypatch.setattr(closures, "get_day", get_day)

    async def go():
        with pytest.raises(HTTPException) as error:
            await sd.close_store_day(
                "S1", "2026-10-01", object(), {"user": "finance", "role": "finance"}
            )
        assert error.value.status_code == 404

    run(go())
    assert calls == ["reconciliation lock", "store day"]


def test_retotal_no_longer_creates_a_missing_store_day():
    async def go():
        Session = await session()
        async with Session() as db:
            with pytest.raises(HTTPException) as e:
                await sd.retotal_store_day("S1", "2026-10-01", db, {"user": "x", "role": "finance"})
            assert e.value.status_code == 404 and "IT administrator" in e.value.detail

    run(go())


def test_store_day_cannot_close_without_current_totals():
    async def go():
        Session = await session()
        async with Session() as db:
            db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01", status="Open"))
            await db.flush()
            db.add(
                StoreDayClosureRequest(
                    id="request", store_day_id="day", requested_by="it", evidence_basis="verified"
                )
            )
            await db.commit()
            user = {"user": "finance", "role": "finance"}
            with pytest.raises(HTTPException) as error:
                await sd.close_store_day("S1", "2026-10-01", db, user)
            assert error.value.status_code == 422
            assert "totals are missing" in error.value.detail
            await sd.retotal_store_day("S1", "2026-10-01", db, user)
            original_request = await db.get(StoreDayClosureRequest, "request")
            assert original_request.status == "Superseded"
            assert "version 1" in original_request.review_reason
            with pytest.raises(HTTPException) as error:
                await sd.close_store_day("S1", "2026-10-01", db, user)
            assert error.value.status_code == 422
            assert "pending closure request" in error.value.detail
            await sd.request_store_day_closure(
                "S1",
                "2026-10-01",
                sd.ClosureRequestBody(evidence_basis="new totals reviewed"),
                db,
                user,
            )
            result = await sd.close_store_day("S1", "2026-10-01", db, user)
            assert result["status"] == "Closed"
            with pytest.raises(HTTPException) as error:
                await sd.close_store_day("S1", "2026-10-01", db, user)
            assert error.value.status_code == 409

    run(go())


def test_store_day_detail_bounds_history_but_counts_all_open_cases():
    async def go():
        Session = await session()
        async with Session() as db:
            day = StoreDay(
                id="day",
                store_id="S1",
                business_date="2026-10-01",
                status="Open",
                audit_version=205,
            )
            db.add(day)
            cases = [
                Case(
                    id=f"case-{index:03}",
                    case_number=f"CASE-{index:03}",
                    case_type="test",
                    business_date=day.business_date,
                    store_id=day.store_id,
                    status="Open",
                )
                for index in range(sd.MAX_STORE_DAY_HISTORY_ITEMS + 5)
            ]
            db.add_all(cases)
            db.add_all(
                [
                    StoreDayClosureRequest(
                        id=f"request-{index:03}",
                        store_day_id=day.id,
                        requested_by="finance",
                        evidence_basis="evidence",
                    )
                    for index in range(sd.MAX_STORE_DAY_HISTORY_ITEMS + 5)
                ]
            )
            db.add_all(
                [
                    EvidenceRequest(
                        id=f"override-{index:03}",
                        case_id=case.id,
                        requirement="proof",
                        requested_from="store",
                        requested_by="finance",
                        status="Overridden",
                        override_reason="approved",
                    )
                    for index, case in enumerate(cases)
                ]
            )
            db.add_all(
                [
                    AuditTotal(
                        id=f"total-{version:03}",
                        store_day_id=day.id,
                        audit_version=version,
                        total_name=f"Total {version}",
                        calculated_amount=0,
                        currency="USD",
                    )
                    for version in range(1, sd.MAX_STORE_DAY_HISTORY_ITEMS + 6)
                ]
            )
            db.add_all(
                [
                    AuditTotal(
                        id="processor-bank",
                        store_day_id=day.id,
                        audit_version=day.audit_version,
                        total_name="Processor to Bank Variance",
                        level="Store",
                        calculated_amount=0,
                        declared_amount=0,
                        variance_amount=0,
                        currency="USD",
                    ),
                    AuditTotal(
                        id="pos-gl",
                        store_day_id=day.id,
                        audit_version=day.audit_version,
                        total_name="POS to GL Variance",
                        level="Store",
                        calculated_amount=0,
                        declared_amount=0,
                        variance_amount=0,
                        currency="USD",
                    ),
                ]
            )
            await db.flush()

            case_count_queries = []
            track_case_count = True

            @event.listens_for(db.get_bind(), "before_cursor_execute")
            def capture_case_count(
                _connection, _cursor, statement, _parameters, _context, _executemany
            ):
                if (
                    track_case_count
                    and "from cases" in statement.lower()
                    and "count(" in statement.lower()
                ):
                    case_count_queries.append(statement)

            result = await sd.get_store_day(
                "S1", day.business_date, None, db, {"user": "finance", "role": "finance"}
            )

            assert len(case_count_queries) == 1
            case_count_queries.clear()
            assert len(result["cases"]) == sd.MAX_STORE_DAY_HISTORY_ITEMS
            assert len(result["closure_requests"]) == sd.MAX_STORE_DAY_HISTORY_ITEMS
            assert len(result["evidence_overrides"]) == sd.MAX_STORE_DAY_HISTORY_ITEMS
            assert len(result["versions"]) == sd.MAX_STORE_DAY_HISTORY_ITEMS
            assert result["cases_truncated"] and result["closure_requests_truncated"]
            assert result["evidence_overrides_truncated"] and result["versions_truncated"]
            assert "205 open case(s)" in result["close_blockers"]

            with pytest.raises(HTTPException, match="205 case\\(s\\) remain open"):
                await sd.close_store_day(
                    "S1", day.business_date, db, {"user": "finance", "role": "finance"}
                )
            track_case_count = False
            assert case_count_queries

    run(go())


def test_other_store_manifest_does_not_create_declared_total():
    async def go():
        Session = await session()
        async with Session() as db:
            db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01", status="Open"))
            await ingest.ingest(
                db,
                "POSControl",
                "d1",
                [
                    {
                        "source_record_id": "other-store",
                        "store_id": "S2",
                        "business_date": "2026-10-01",
                        "declared_count": 0,
                        "declared_total": "0",
                    },
                    {
                        "source_record_id": "other-day",
                        "store_id": "S1",
                        "business_date": "2026-10-02",
                        "declared_count": 1,
                        "declared_total": "10",
                    },
                ],
            )
            await db.commit()
            user = {"user": "finance", "role": "finance"}
            result = await sd.retotal_store_day("S1", "2026-10-01", db, user)
            detail = await sd.get_store_day("S1", "2026-10-01", db=db, user=user)
            assert result["totals_created"] == 2
            assert "POS Control Declared" not in {total["name"] for total in detail["totals"]}

    run(go())


def test_retotal_pages_manifests_and_aggregates_only_the_requested_store(monkeypatch):
    async def go():
        Session = await session()
        async with Session() as db:
            db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01", status="Open"))
            await ingest.ingest(
                db,
                "POSControl",
                "d1",
                [
                    {
                        "source_record_id": f"manifest-{index}",
                        "store_id": store,
                        "business_date": "2026-10-01",
                        "declared_count": 0,
                        "declared_total": amount,
                    }
                    for index, (store, amount) in enumerate(
                        [
                            ("S1", "10"),
                            ("S2", "999"),
                            ("S1", "20"),
                            ("S3", "500"),
                        ]
                    )
                ],
            )
            await db.commit()
            monkeypatch.setattr("apps.ingest.manifests.MANIFEST_PAGE_SIZE", 1)

            await sd.retotal_store_day(
                "S1", "2026-10-01", db, {"user": "finance", "role": "finance"}
            )

            declared_total = (
                await db.execute(
                    select(AuditTotal).where(
                        AuditTotal.store_day_id == "day",
                        AuditTotal.total_name == "POS Control Declared",
                    )
                )
            ).scalar_one()
            assert declared_total.declared_amount == 30

    run(go())


def test_pos_control_variance_blocks_closure_and_is_displayed():
    async def go():
        Session = await session()
        async with Session() as db:
            db.add(StoreDay(id="day", store_id="S1", business_date="2026-10-01", status="Open"))
            await ingest.ingest(
                db,
                "POSControl",
                "d1",
                [
                    {
                        "source_record_id": "manifest",
                        "store_id": "S1",
                        "business_date": "2026-10-01",
                        "declared_count": 1,
                        "declared_total": "10",
                    }
                ],
            )
            await db.commit()
            user = {"user": "finance", "role": "finance"}
            await sd.retotal_store_day("S1", "2026-10-01", db, user)
            await sd.request_store_day_closure(
                "S1", "2026-10-01", sd.ClosureRequestBody(evidence_basis="reviewed"), db, user
            )
            detail = await sd.get_store_day("S1", "2026-10-01", db=db, user=user)
            assert "unresolved reconciliation variance" in detail["close_blockers"]
            with pytest.raises(HTTPException) as error:
                await sd.close_store_day("S1", "2026-10-01", db, user)
            assert error.value.status_code == 422
            assert "variance" in error.value.detail

    run(go())


# ---- Export tracking: what was exported, what is waiting, what is not eligible, and why ---------------------------
def _setup_dest(db, kind="file", url=None):
    cfg = {} if kind == "file" else {"url": url, "secret": "s" * 32}
    d = ExportDestination(id="d1", name="ERP", kind=kind, format="json", config=cfg)
    db.add(d)
    return d


async def _tracking(db, destination_id, dataset):
    summary, items, total = await exports.tracking_page(db, destination_id, dataset, "", "", 1, 200)
    assert total == len(items)
    return summary, items


def test_failed_batch_reserves_its_records_and_cancel_releases_them(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)

    async def go():
        Session = await session()
        user = {"user": "it.admin", "role": "it"}
        async with Session() as db:
            _setup_dest(
                db, "webhook", "http://127.0.0.1:9/never"
            )  # nothing listens here, so delivery fails
            db.add(
                Case(
                    id="c1",
                    case_number="CASE-1",
                    case_type="x",
                    business_date="2026-10-01",
                    store_id="S1",
                    status="Closed",
                )
            )
            db.add(
                Case(
                    id="c2",
                    case_number="CASE-2",
                    case_type="x",
                    business_date="2026-10-01",
                    store_id="S1",
                    status="Open",
                )
            )
            await db.commit()

            b = await exports.run_export(
                exports.RunIn(destination_id="d1", dataset="resolutions"), db, user
            )
            assert b["status"] == "Failed" and b["record_count"] == 1
            _, items = await _tracking(db, "d1", "resolutions")
            rows = {r["case_number"]: r for r in items}
            assert (
                rows["CASE-1"]["state"] == "in_batch" and rows["CASE-1"]["batch_status"] == "Failed"
            )
            assert rows["CASE-2"]["state"] == "not_eligible" and "Open" in rows["CASE-2"]["reason"]
            # Reserved records are not sent again by a second run.
            again = await exports.run_export(
                exports.RunIn(destination_id="d1", dataset="resolutions"), db, user
            )
            assert again["count"] == 0

            out = await exports.cancel_batch(
                b["id"],
                db,
                user,
                exports.FailedExportResolution(
                    outcome="not_received", evidence="Receiver confirmed no delivery"
                ),
            )
            assert out["status"] == "Cancelled" and out["released"] == 1
            _, items = await _tracking(db, "d1", "resolutions")
            assert items[0]["state"] in ("waiting", "not_eligible")
            assert {r["case_number"]: r["state"] for r in items}["CASE-1"] == "waiting"

    asyncio.run(go())


def test_successful_retry_records_what_it_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)

    async def go():
        Session = await session()
        user = {"user": "it.admin", "role": "it"}
        async with Session() as db:
            dest = _setup_dest(db, "webhook", "http://127.0.0.1:9/never")
            db.add(
                Case(
                    id="c1",
                    case_number="CASE-1",
                    case_type="x",
                    business_date="2026-10-01",
                    store_id="S1",
                    status="Closed",
                )
            )
            await db.commit()
            b = await exports.run_export(
                exports.RunIn(destination_id="d1", dataset="resolutions"), db, user
            )
            assert b["status"] == "Failed"
            dest.kind, dest.config = (
                "file",
                {},
            )  # the destination is fixed, then the batch is retried
            await db.commit()
            retried = await exports.retry(b["id"], db, user)
            assert retried["status"] == "Sent"
            _, items = await _tracking(db, "d1", "resolutions")
            row = items[0]
            assert (
                row["state"] == "delivered"
                and row["delivered_at"] is not None
                and row["record_ref"] == "CASE-1"
            )
            assert (
                await exports.run_export(
                    exports.RunIn(destination_id="d1", dataset="resolutions"), db, user
                )
            )["count"] == 0
            with pytest.raises(HTTPException) as e:
                await exports.cancel_batch(
                    b["id"], db, user
                )  # delivered batches cannot be cancelled
            assert e.value.status_code == 409

    asyncio.run(go())


def test_adjustment_ineligibility_reasons_are_specific():
    async def go():
        from decimal import Decimal

        from models import HumanDecision, Recommendation, ValidationObligation, Workflow

        Session = await session()
        async with Session() as db:
            _setup_dest(db)
            for cid in ("a", "b", "c", "d"):
                db.add(
                    Case(
                        id=cid,
                        case_number=cid.upper(),
                        case_type="x",
                        business_date="2026-10-01",
                        store_id="S1",
                        status="Closed",
                    )
                )
            await db.flush()

            def make_recommendation(case_id, impact):
                return Recommendation(
                    id="r" + case_id,
                    case_id=case_id,
                    investigation_id="i",
                    disposition_type="Correction",
                    action_class="x",
                    expected_workflow="w",
                    financial_impact=Decimal(impact),
                    confidence="High",
                    status="Approved",
                )

            db.add_all(
                [
                    make_recommendation("b", "0"),
                    make_recommendation("c", "50"),
                    make_recommendation("d", "50"),
                ]
            )
            db.add(
                HumanDecision(
                    id="h1",
                    case_id="d",
                    recommendation_id="rd",
                    decision_type="human",
                    decision="Approved",
                    actor="finance",
                    authority_check="Approved",
                )
            )
            db.add(
                Workflow(
                    id="w",
                    case_id="d",
                    recommendation_id="rd",
                    workflow_type="Correction",
                    state="Completed",
                    definition_version="1",
                    current_step="Closed",
                )
            )
            db.add(
                ValidationObligation(
                    id="v1",
                    case_id="d",
                    workflow_id="w",
                    status="Complete",
                    verification_result="Verified",
                    expected_observation="o",
                    due_window_hours=24,
                    verification_rule="r",
                )
            )
            await db.commit()
            summary, items = await _tracking(db, "d1", "adjustments")
            by = {r["case_number"]: r for r in items}
            assert "No approved recommendation" in by["A"]["reason"]
            assert "no financial impact" in by["B"]["reason"]
            assert "Validation is not complete" in by["C"]["reason"]
            assert by["D"]["state"] == "waiting" and by["D"]["record_ref"] == "NIM-D"
            assert (
                summary["waiting"] == 1
                and summary["not_eligible"] == 3
                and summary["delivered"] == 0
            )

    asyncio.run(go())

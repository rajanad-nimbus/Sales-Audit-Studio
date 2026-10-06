"""Export back-posting and IT-initiated Store Days."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import exports
import routes_store_days as sd
from models import Base, Case, ExportBatch, ExportDestination, ExportItem, FoundationReference


def run(c):
    return asyncio.run(c)


async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---- classification -------------------------------------------------------------------------------------------
def test_classification_current_late_addition_and_after_business_date():
    prior = {("S1", "2026-10-01"): ["abcd1234"]}
    assert exports.classify_backpost("S1", "2026-10-05", "2026-10-05", prior) == (False, None, [])
    assert exports.classify_backpost("S1", "2026-10-01", "2026-10-01", prior) == (True, "Late addition to a business day already exported", ["abcd1234"])
    assert exports.classify_backpost("S2", "2026-10-01", "2026-10-02", prior) == (True, "Resolved after the business date ended", [])


def test_summary_and_scope_keep_records_and_items_aligned():
    recs = [{"business_date": "2026-10-01", "backpost": True}, {"business_date": "2026-10-05", "backpost": False},
            {"business_date": "2026-10-02", "backpost": True}]
    items = [("a", "v1"), ("b", "v1"), ("c", "v1")]
    assert exports.summarize(recs) == {"current": 1, "backposted": 2, "business_dates": ["2026-10-01", "2026-10-02"]}
    assert exports.apply_scope(recs, items, "all") == (recs, items)
    r, i = exports.apply_scope(recs, items, "backposts")
    assert [x[0] for x in i] == ["a", "c"] and all(x["backpost"] for x in r)
    r, i = exports.apply_scope(recs, items, "current")
    assert [x[0] for x in i] == ["b"]


# ---- build() against a real (sqlite) schema -------------------------------------------------------------------
def test_build_flags_late_additions_and_late_closures_but_not_same_day_resolutions():
    async def go():
        Session = await session()
        today = datetime.now(timezone.utc).date().isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
        async with Session() as db:
            dest = ExportDestination(id="d1", name="ERP", kind="file", format="json", config={})
            db.add(dest)
            mk = lambda cid, store, bd: Case(id=cid, case_number=cid.upper(), case_type="x", business_date=bd, store_id=store, status="Closed")
            db.add_all([mk("c-sent", "S1", old), mk("c-late", "S1", old),      # same store/day: one already exported, one resolves later
                        mk("c-old", "S2", old),                                # never exported, closed after its business date
                        mk("c-now", "S3", today)])                             # resolved on its own business day
            db.add(ExportBatch(id="batch-0001-xyz", destination_id="d1", dataset="resolutions", status="Acknowledged",
                               idempotency_key="k1", created_by="it", record_count=1))
            await db.flush()
            db.add(ExportItem(id=str(uuid.uuid4()), batch_id="batch-0001-xyz", destination_id="d1", dataset="resolutions",
                              object_id="c-sent", object_version="v1", state="Delivered"))
            await db.commit()

            records, items, *_ = await exports.build(db, dest, "resolutions")
            by = {r["case_number"]: r for r in records}
            assert set(by) == {"C-LATE", "C-OLD", "C-NOW"}          # c-sent was already delivered
            assert by["C-LATE"]["backpost"] and by["C-LATE"]["backpost_reason"].startswith("Late addition")
            assert by["C-LATE"]["prior_batches"] == "batch-00"
            assert by["C-OLD"]["backpost"] and by["C-OLD"]["backpost_reason"] == "Resolved after the business date ended"
            assert not by["C-NOW"]["backpost"] and by["C-NOW"]["backpost_reason"] is None
            assert all("posting_date" in r and "business_date" in r for r in records)
            assert exports.summarize(records)["backposted"] == 2
    run(go())


# ---- Store Days: opened by IT, calculated by re-total ---------------------------------------------------------
def test_open_store_day_validates_store_date_and_duplicates():
    async def go():
        Session = await session()
        async with Session() as db:
            db.add(FoundationReference(id="f1", category="Store", code="S1", name="One", active=True))
            await db.commit()
            day = await sd.open_store_day_record(db, "S1", "2026-10-01", "it.admin")
            assert day.status == "Open" and day.audit_version == 0
            for args, code in ((("S1", "2026-10-01"), 409), (("S9", "2026-10-01"), 422), (("S1", "2999-01-01"), 422), (("S1", "01/10/2026"), 422)):
                with pytest.raises(HTTPException) as e:
                    await sd.open_store_day_record(db, *args, "it.admin")
                assert e.value.status_code == code
    run(go())


def test_retotal_no_longer_creates_a_missing_store_day():
    async def go():
        Session = await session()
        async with Session() as db:
            with pytest.raises(HTTPException) as e:
                await sd.retotal_store_day("S1", "2026-10-01", db, {"user": "x", "role": "finance"})
            assert e.value.status_code == 404 and "IT administrator" in e.value.detail
    run(go())


# ---- Export tracking: what was exported, what is waiting, what is not eligible, and why ---------------------------
def _setup_dest(db, kind="file", url=None):
    cfg = {} if kind == "file" else {"url": url, "secret": "s"}
    d = ExportDestination(id="d1", name="ERP", kind=kind, format="json", config=cfg)
    db.add(d)
    return d


def test_failed_batch_reserves_its_records_and_cancel_releases_them(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)

    async def go():
        Session = await session()
        user = {"user": "it.admin", "role": "it"}
        async with Session() as db:
            _setup_dest(db, "webhook", "http://127.0.0.1:9/never")   # nothing listens here, so delivery fails
            db.add(Case(id="c1", case_number="CASE-1", case_type="x", business_date="2026-10-01", store_id="S1", status="Closed"))
            db.add(Case(id="c2", case_number="CASE-2", case_type="x", business_date="2026-10-01", store_id="S1", status="Open"))
            await db.commit()

            b = await exports.run_export(exports.RunIn(destination_id="d1", dataset="resolutions"), db, user)
            assert b["status"] == "Failed" and b["record_count"] == 1
            rows = {r["case_number"]: r for r in await exports.ledger(db, "d1", "resolutions")}
            assert rows["CASE-1"]["state"] == "in_batch" and rows["CASE-1"]["batch_status"] == "Failed"
            assert rows["CASE-2"]["state"] == "not_eligible" and "Open" in rows["CASE-2"]["reason"]
            # Reserved records are not sent again by a second run.
            again = await exports.run_export(exports.RunIn(destination_id="d1", dataset="resolutions"), db, user)
            assert again["count"] == 0

            out = await exports.cancel_batch(b["id"], db, user)
            assert out["status"] == "Cancelled" and out["released"] == 1
            assert (await exports.ledger(db, "d1", "resolutions"))[0]["state"] in ("waiting", "not_eligible")
            assert {r["case_number"]: r["state"] for r in await exports.ledger(db, "d1", "resolutions")}["CASE-1"] == "waiting"
    asyncio.run(go())


def test_successful_retry_records_what_it_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)

    async def go():
        Session = await session()
        user = {"user": "it.admin", "role": "it"}
        async with Session() as db:
            dest = _setup_dest(db, "webhook", "http://127.0.0.1:9/never")
            db.add(Case(id="c1", case_number="CASE-1", case_type="x", business_date="2026-10-01", store_id="S1", status="Closed"))
            await db.commit()
            b = await exports.run_export(exports.RunIn(destination_id="d1", dataset="resolutions"), db, user)
            assert b["status"] == "Failed"
            dest.kind, dest.config = "file", {}             # the destination is fixed, then the batch is retried
            await db.commit()
            retried = await exports.retry(b["id"], db, user)
            assert retried["status"] == "Sent"
            row = (await exports.ledger(db, "d1", "resolutions"))[0]
            assert row["state"] == "delivered" and row["delivered_at"] is not None and row["record_ref"] == "CASE-1"
            assert (await exports.run_export(exports.RunIn(destination_id="d1", dataset="resolutions"), db, user))["count"] == 0
            with pytest.raises(HTTPException) as e:
                await exports.cancel_batch(b["id"], db, user)       # delivered batches cannot be cancelled
            assert e.value.status_code == 409
    asyncio.run(go())


def test_adjustment_ineligibility_reasons_are_specific():
    async def go():
        from decimal import Decimal
        from models import Recommendation, ValidationObligation
        Session = await session()
        async with Session() as db:
            _setup_dest(db)
            for cid in ("a", "b", "c", "d"):
                db.add(Case(id=cid, case_number=cid.upper(), case_type="x", business_date="2026-10-01", store_id="S1", status="Closed"))
            await db.flush()
            rec = lambda cid, impact: Recommendation(id="r" + cid, case_id=cid, investigation_id="i", disposition_type="Correction", action_class="x",
                                                     expected_workflow="w", financial_impact=Decimal(impact), confidence="High", status="Approved")
            db.add_all([rec("b", "0"), rec("c", "50"), rec("d", "50")])
            db.add(ValidationObligation(id="v1", case_id="d", workflow_id="w", status="Complete", expected_observation="o", due_window_hours=24, verification_rule="r"))
            await db.commit()
            by = {r["case_number"]: r for r in await exports.ledger(db, "d1", "adjustments")}
            assert "No approved recommendation" in by["A"]["reason"]
            assert "no financial impact" in by["B"]["reason"]
            assert "Validation is not complete" in by["C"]["reason"]
            assert by["D"]["state"] == "waiting" and by["D"]["record_ref"] == "NIM-D"
            s = exports.ledger_summary(list(by.values()))
            assert s["waiting"] == 1 and s["not_eligible"] == 3 and s["delivered"] == 0
    asyncio.run(go())

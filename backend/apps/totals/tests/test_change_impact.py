"""Drafts stay out of audit results until their previewed impact is activated."""

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from models import (
    AuditEvent,
    AuditTotalDefinition,
    Base,
    ConfiguredAuditRule,
    FoundationReference,
    StoreDay,
    StoreDayClosureRequest,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.ingest.service import ingest
from apps.totals.calculation import effective_definitions
from apps.totals.routes import (
    activate_definition,
    create_definition,
    impact_definition,
    return_definition,
    submit_definition,
    update_definition,
)
from apps.totals.schemas import ActivationBody, ReturnBody, TotalDefinitionBody

FINANCE = {"user": "finance.user"}
APPROVER = {"user": "approver.user"}
DATE = "2026-10-01"


def run_with_db(scenario):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(run())


def sale(record_id: str, amount: str, store: str = "S1") -> dict:
    return {
        "source_record_id": record_id,
        "record_type": "Sale",
        "event_time": f"{DATE}T10:00:00Z",
        "store_id": store,
        "amount": amount,
    }


async def calculated_day(db, *, store: str = "S1", closure: bool = True) -> None:
    """Two sales (10 + 5) on a calculated store day with a pending closure request."""
    db.add_all(
        [
            FoundationReference(
                id=f"ref-{store}", category="Store", code=store, name=store, active=True
            ),
            StoreDay(id=f"day-{store}", store_id=store, business_date=DATE),
            AuditTotalDefinition(id="live", name="Sales", status="Active", active=True),
        ]
    )
    await db.flush()
    await ingest(
        db,
        "POS",
        f"delivery-{store}",
        [sale(f"a-{store}", "10", store), sale(f"b-{store}", "5", store)],
    )
    day = await db.get(StoreDay, f"day-{store}")
    day.audit_version, day.totals_stale = 1, False
    if closure:
        db.add(
            StoreDayClosureRequest(
                id=f"req-{store}", store_day_id=day.id, requested_by="auditor", evidence_basis="ok"
            )
        )
    await db.commit()


async def count_draft(db) -> dict:
    return await update_definition(
        "live", TotalDefinitionBody(name="Sales", aggregation="Count"), db, FINANCE
    )


def test_saving_a_draft_changes_no_audit_result():
    async def scenario(db):
        await calculated_day(db)
        draft = await count_draft(db)

        db.expire_all()
        assert draft["status"] == "Worksheet"
        assert not (await db.get(StoreDay, "day-S1")).totals_stale
        assert (await db.get(StoreDayClosureRequest, "req-S1")).status == "Pending"
        effective = await effective_definitions(db, DATE, "S1")
        assert [row.id for row in effective] == ["live"]

    run_with_db(scenario)


def test_a_brand_new_total_is_a_draft_until_activated():
    async def scenario(db):
        created = await create_definition(TotalDefinitionBody(name="New"), db, FINANCE)
        assert created["status"] == "Worksheet"
        assert await effective_definitions(db, DATE, "S1") == []

    run_with_db(scenario)


def test_saves_cannot_make_a_revision_live_directly():
    async def scenario(db):
        with pytest.raises(HTTPException) as error:
            await create_definition(TotalDefinitionBody(name="New", status="Active"), db, FINANCE)
        assert error.value.status_code == 422
        assert "activate" in error.value.detail

    run_with_db(scenario)


def test_impact_compares_current_and_proposed_values_per_store_day():
    async def scenario(db):
        await calculated_day(db)
        draft = await count_draft(db)

        report = await impact_definition(draft["id"], db, FINANCE)

        assert report["summary"] == {
            "store_days": 1,
            "changed": 1,
            "net_change": Decimal("-13"),
            "absolute_change": Decimal("13"),
            "closed": 0,
            "would_go_stale": 1,
            "closure_requests_superseded": 1,
            "errors": 0,
            "rules_newly_breached": 0,
            "rules_cleared": 0,
        }
        assert report["truncated"] is False
        [row] = report["rows"]
        assert row["store_id"] == "S1" and row["business_date"] == DATE
        assert row["current"] == Decimal("15") and row["proposed"] == Decimal("2")
        assert row["change"] == Decimal("-13")
        assert len(report["fingerprint"]) == 64

    run_with_db(scenario)


def test_impact_shows_combined_totals_the_change_would_break():
    async def scenario(db):
        await calculated_day(db, closure=False)
        db.add_all(
            [
                AuditTotalDefinition(id="other", name="Other", status="Active", active=True),
                AuditTotalDefinition(
                    id="pinned",
                    name="Pinned",
                    status="Active",
                    active=True,
                    calculation_basis="Combined",
                    combined_total_ids=["live", "other"],
                    combined_operator="+",
                ),
            ]
        )
        await db.commit()
        draft = await count_draft(db)

        report = await impact_definition(draft["id"], db, FINANCE)

        [row] = report["rows"]
        assert row["current_error"] is None
        assert "Pinned" in row["proposed_error"]
        assert report["summary"]["errors"] == 1

        with pytest.raises(HTTPException) as broken:
            await submit_definition(
                draft["id"], ActivationBody(impact_fingerprint=report["fingerprint"]), db, FINANCE
            )
        assert broken.value.status_code == 422
        assert "break calculation" in broken.value.detail

    run_with_db(scenario)


def test_impact_reports_other_totals_whose_values_change():
    async def scenario(db):
        await calculated_day(db, closure=False)
        db.add_all(
            [
                AuditTotalDefinition(id="other", name="Other", status="Active", active=True),
                AuditTotalDefinition(
                    id="draft-combined",
                    name="Pinned",
                    status="Worksheet",
                    active=True,
                    calculation_basis="Combined",
                    combined_total_ids=["other", "other-2"],
                    combined_operator="+",
                ),
            ]
        )
        await db.commit()
        draft = await count_draft(db)
        report = await impact_definition(draft["id"], db, FINANCE)
        # Drafts of other totals are never part of either side of the comparison.
        assert report["rows"][0]["changed_totals"] == []
        assert report["rows"][0]["proposed_error"] is None

    run_with_db(scenario)


def test_submission_requires_the_fingerprint_of_a_current_preview():
    async def scenario(db):
        await calculated_day(db)
        draft = await count_draft(db)

        with pytest.raises(HTTPException) as unseen:
            await submit_definition(
                draft["id"], ActivationBody(impact_fingerprint="0" * 64), db, FINANCE
            )
        assert unseen.value.status_code == 409

        report = await impact_definition(draft["id"], db, FINANCE)
        await ingest(db, "POS", "late", [sale("c-S1", "7")])
        await db.commit()
        with pytest.raises(HTTPException) as moved:
            await submit_definition(
                draft["id"], ActivationBody(impact_fingerprint=report["fingerprint"]), db, FINANCE
            )
        assert moved.value.status_code == 409
        assert "preview" in moved.value.detail

    run_with_db(scenario)


async def submitted(db) -> tuple[dict, ActivationBody]:
    draft = await count_draft(db)
    report = await impact_definition(draft["id"], db, FINANCE)
    body = ActivationBody(impact_fingerprint=report["fingerprint"])
    await submit_definition(draft["id"], body, db, FINANCE)
    return draft, body


def test_a_submitted_revision_is_not_live_until_a_second_user_approves_it():
    async def scenario(db):
        await calculated_day(db)
        draft, body = await submitted(db)

        db.expire_all()
        assert (await db.get(AuditTotalDefinition, draft["id"])).status == "Pending Approval"
        assert [row.id for row in await effective_definitions(db, DATE, "S1")] == ["live"]
        assert not (await db.get(StoreDay, "day-S1")).totals_stale

        with pytest.raises(HTTPException) as same_person:
            await activate_definition(draft["id"], body, db, FINANCE)
        assert same_person.value.status_code == 403

    run_with_db(scenario)


def test_the_approver_must_see_the_impact_that_was_submitted():
    async def scenario(db):
        await calculated_day(db)
        draft, body = await submitted(db)
        await ingest(db, "POS", "late", [sale("c-S1", "7")])
        await db.commit()
        with pytest.raises(HTTPException) as moved:
            await activate_definition(draft["id"], body, db, APPROVER)
        assert moved.value.status_code == 409
        assert "since it was submitted" in moved.value.detail

    run_with_db(scenario)


def test_a_reviewer_can_return_a_submission_to_worksheet():
    async def scenario(db):
        await calculated_day(db)
        draft, body = await submitted(db)
        returned = await return_definition(
            draft["id"], ReturnBody(rationale="Count is the wrong measure"), db, APPROVER
        )
        assert returned["status"] == "Worksheet" and returned["submitted_by"] is None
        with pytest.raises(HTTPException) as not_pending:
            await activate_definition(draft["id"], body, db, APPROVER)
        assert not_pending.value.status_code == 409
        event = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.event_type == "TOTAL_DEFINITION_RETURNED")
            )
        ).scalar_one()
        assert event.actor == "approver.user"

    run_with_db(scenario)


def test_only_the_latest_draft_can_be_activated():
    async def scenario(db):
        await calculated_day(db)
        first = await count_draft(db)
        await update_definition(
            first["id"], TotalDefinitionBody(name="Sales", description="newer"), db, FINANCE
        )
        report = await impact_definition(first["id"], db, FINANCE)
        with pytest.raises(HTTPException) as older:
            await submit_definition(
                first["id"], ActivationBody(impact_fingerprint=report["fingerprint"]), db, FINANCE
            )
        assert older.value.status_code == 409

        with pytest.raises(HTTPException) as live:
            await impact_definition("live", db, FINANCE)
        assert live.value.status_code == 409

    run_with_db(scenario)


def test_store_scoped_users_see_only_their_stores_and_cannot_activate():
    async def scenario(db):
        await calculated_day(db)
        db.add_all(
            [
                FoundationReference(
                    id="ref-S2", category="Store", code="S2", name="S2", active=True
                ),
                StoreDay(id="day-S2", store_id="S2", business_date=DATE, audit_version=1),
            ]
        )
        await db.commit()
        draft = await count_draft(db)
        manager = {"user": "manager", "store_scoped": True, "stores": ["S2"]}

        report = await impact_definition(draft["id"], db, manager)
        assert [row["store_id"] for row in report["rows"]] == ["S2"]

        with pytest.raises(HTTPException) as forbidden:
            await submit_definition(
                draft["id"], ActivationBody(impact_fingerprint=report["fingerprint"]), db, manager
            )
        assert forbidden.value.status_code == 403

    run_with_db(scenario)


def test_wide_changes_are_truncated_not_hidden(monkeypatch):
    async def scenario(db):
        await calculated_day(db, closure=False)
        db.add_all(
            [
                FoundationReference(
                    id="ref-S2", category="Store", code="S2", name="S2", active=True
                ),
                StoreDay(id="day-S2", store_id="S2", business_date=DATE, audit_version=1),
            ]
        )
        await db.commit()
        monkeypatch.setattr("apps.totals.impact.IMPACT_STORE_DAY_LIMIT", 1)
        draft = await count_draft(db)
        report = await impact_definition(draft["id"], db, FINANCE)
        assert report["truncated"] is True
        assert report["summary"]["store_days"] == 1

    run_with_db(scenario)


def test_impact_reports_audit_rules_that_would_newly_breach_or_clear():
    async def scenario(db):
        await calculated_day(db, closure=False)
        db.add_all(
            [
                ConfiguredAuditRule(
                    id="low",
                    name="Sales under 10",
                    total_name="Configured: Sales",
                    threshold=Decimal("10"),
                    threshold_currency="USD",
                    enabled=True,
                ),
                ConfiguredAuditRule(
                    id="tiny",
                    name="More than one sale",
                    total_name="Configured: Sales",
                    threshold=Decimal("1"),
                    threshold_currency="USD",
                    enabled=True,
                ),
            ]
        )
        await db.commit()
        # Sales goes from 15 (the sum) to 2 (the count).
        draft = await count_draft(db)
        report = await impact_definition(draft["id"], db, FINANCE)

        [row] = report["rows"]
        assert [(c["rule"], c["effect"]) for c in row["rule_changes"]] == [
            ("Sales under 10", "Clears")
        ]
        assert report["summary"]["rules_cleared"] == 1
        assert report["summary"]["rules_newly_breached"] == 0

    run_with_db(scenario)

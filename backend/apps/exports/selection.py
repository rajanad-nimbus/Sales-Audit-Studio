"""Select eligible export records and build their immutable data representation."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_, select, tuple_
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.models import (
    Case,
    HumanDecision,
    Recommendation,
    ValidationObligation,
)
from apps.exports.eligibility import completed_case_evidence, verified_adjustment_evidence
from apps.exports.models import ExportBatch, ExportDestination, ExportItem

MAX_BATCH = 5000
SQLITE_BIND_BATCH_SIZE = 300
POSTGRES_BIND_PARAMETER_BUDGET = 30_000
PRIOR_DAY_KEY_COLUMNS = 2
SCOPES = ("all", "current", "backposts")


async def head_hash(db) -> str | None:
    return (
        await db.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))
    ).scalar()


def classify_backpost(
    store_id: str,
    business_date: str,
    closed_date: str,
    prior: dict,
) -> tuple[bool, str | None, list[str]]:
    """Identify whether a record belongs to an already ended or exported business day."""
    batches = prior.get((store_id, business_date), [])
    if batches:
        return True, "Late addition to a business day already exported", batches
    if closed_date > business_date:
        return True, "Resolved after the business date ended", []
    return False, None, []


async def prior_exported_days(
    db,
    destination_id: str,
    dataset: str,
    cases: list[Case] | None = None,
) -> dict:
    """Map each exported store day to the earlier batches that covered it."""
    query = (
        select(Case.store_id, Case.business_date, ExportBatch.id)
        .select_from(ExportItem)
        .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .join(Case, Case.id == ExportItem.object_id)
        .where(
            ExportItem.destination_id == destination_id,
            ExportItem.dataset == dataset,
            ExportItem.state == "Delivered",
            ExportBatch.status.in_(["Sent", "Acknowledged"]),
        )
        .distinct()
    )
    keys = (
        sorted({(case.store_id, case.business_date) for case in cases})
        if cases is not None
        else None
    )
    rows = []
    if keys is None:
        rows = (await db.execute(query)).all()
    else:
        parameter_budget = (
            POSTGRES_BIND_PARAMETER_BUDGET
            if db.get_bind().dialect.name == "postgresql"
            else SQLITE_BIND_BATCH_SIZE * PRIOR_DAY_KEY_COLUMNS
        )
        batch_size = max(1, parameter_budget // PRIOR_DAY_KEY_COLUMNS)
        for offset in range(0, len(keys), batch_size):
            key_batch = keys[offset : offset + batch_size]
            rows.extend(
                (
                    await db.execute(
                        query.where(tuple_(Case.store_id, Case.business_date).in_(key_batch))
                    )
                ).all()
            )
    result: dict[tuple[str, str], list[str]] = {}
    for store_id, business_date, batch_id in rows:
        result.setdefault((store_id, business_date), []).append(batch_id[:8])
    return {key: sorted(batch_ids) for key, batch_ids in result.items()}


def summarize(records: list[dict]) -> dict:
    backposted = [record for record in records if record.get("backpost")]
    return {
        "current": len(records) - len(backposted),
        "backposted": len(backposted),
        "business_dates": sorted(
            {record["business_date"] for record in backposted if record.get("business_date")}
        ),
    }


async def closed_case_context(
    db,
    cases: list[Case],
    positive_recommendations: bool = False,
) -> dict:
    """Load the latest approval, validation, and audit reference for each case."""
    ids = [case.id for case in cases]
    if not ids:
        return {"recs": {}, "decs": {}, "vals": {}, "refs": {}}

    batch_size = (
        POSTGRES_BIND_PARAMETER_BUDGET
        if db.get_bind().dialect.name == "postgresql"
        else SQLITE_BIND_BATCH_SIZE
    )
    evidence_query = (
        verified_adjustment_evidence() if positive_recommendations else completed_case_evidence()
    )
    recs, decisions, validations, references = {}, {}, {}, {}
    for offset in range(0, len(ids), batch_size):
        case_ids = ids[offset : offset + batch_size]
        ranked_evidence = (
            evidence_query.where(Recommendation.case_id.in_(case_ids))
            .add_columns(
                func.row_number()
                .over(
                    partition_by=Recommendation.case_id,
                    order_by=(
                        Recommendation.created_at.desc(),
                        Recommendation.id.desc(),
                        HumanDecision.created_at.desc(),
                        HumanDecision.id.desc(),
                        ValidationObligation.created_at.desc(),
                        ValidationObligation.id.desc(),
                    ),
                )
                .label("case_rank")
            )
            .subquery()
        )
        recommendation = aliased(Recommendation, ranked_evidence)
        decision = aliased(HumanDecision, ranked_evidence)
        obligation = aliased(ValidationObligation, ranked_evidence)
        evidence = (
            await db.execute(
                select(recommendation, decision, obligation).where(ranked_evidence.c.case_rank == 1)
            )
        ).all()
        recs.update(
            {
                recommendation_row.case_id: recommendation_row
                for recommendation_row, _, _ in evidence
            }
        )
        decisions.update(
            {
                recommendation_row.case_id: decision_row
                for recommendation_row, decision_row, _ in evidence
            }
        )
        validations.update(
            {
                recommendation_row.case_id: validation_row
                for recommendation_row, _, validation_row in evidence
            }
        )

        latest_event_ids = (
            select(
                AuditEvent.id.label("id"),
                func.row_number()
                .over(
                    partition_by=AuditEvent.case_id,
                    order_by=(AuditEvent.seq.desc(), AuditEvent.id.desc()),
                )
                .label("case_rank"),
            )
            .where(AuditEvent.case_id.in_(case_ids))
            .subquery()
        )
        latest_events = (
            (
                await db.execute(
                    select(AuditEvent)
                    .join(latest_event_ids, AuditEvent.id == latest_event_ids.c.id)
                    .where(latest_event_ids.c.case_rank == 1)
                )
            )
            .scalars()
            .all()
        )
        references.update({event.case_id: event.hash for event in latest_events})

    return {
        "recs": recs,
        "decs": decisions,
        "vals": validations,
        "refs": references,
    }


async def build(
    db,
    destination: ExportDestination,
    dataset: str,
    scope: str = "all",
    *,
    lock_cases: bool = False,
) -> tuple[list[dict], list[tuple[str, str, dict[str, Any]]], int | None, int | None]:
    """Build export records and item reservations for a bounded batch."""
    if dataset == "audit":
        last = (
            await db.execute(
                select(func.max(ExportBatch.range_to)).where(
                    ExportBatch.destination_id == destination.id,
                    ExportBatch.dataset == "audit",
                    ExportBatch.status.in_(["Sent", "Acknowledged"]),
                )
            )
        ).scalar() or 0
        rows = (
            (
                await db.execute(
                    select(AuditEvent)
                    .where(AuditEvent.seq > last)
                    .order_by(AuditEvent.seq)
                    .limit(MAX_BATCH)
                )
            )
            .scalars()
            .all()
        )
        records = [
            {
                "seq": event.seq,
                "timestamp": event.created_at.isoformat(),
                "event_type": event.event_type,
                "actor": event.actor,
                "object_type": event.object_type,
                "object_id": event.object_id,
                "case_id": event.case_id,
                "description": event.action_description,
                "prev_hash": event.prev_hash,
                "hash": event.hash,
            }
            for event in rows
        ]
        return records, [], (rows[0].seq if rows else None), (rows[-1].seq if rows else None)

    reserved = (
        select(ExportItem.id)
        .where(
            ExportItem.destination_id == destination.id,
            ExportItem.dataset == dataset,
            ExportItem.object_id == Case.id,
        )
        .exists()
    )
    query = select(Case).where(
        Case.status == "Closed",
        Case.amount_needs_review.is_(False),
        ~reserved,
    )
    if scope != "all":
        exported_case = aliased(Case)
        previously_exported = (
            select(ExportItem.id)
            .select_from(ExportItem)
            .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
            .join(exported_case, exported_case.id == ExportItem.object_id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.state == "Delivered",
                ExportBatch.status.in_(["Sent", "Acknowledged"]),
                exported_case.store_id == Case.store_id,
                exported_case.business_date == Case.business_date,
            )
            .exists()
        )
        backpost = or_(
            cast(func.date(Case.updated_at), String) > Case.business_date,
            previously_exported,
        )
        query = query.where(backpost if scope == "backposts" else ~backpost)
    if dataset == "adjustments":
        verified = verified_adjustment_evidence().where(Recommendation.case_id == Case.id).exists()
        query = query.where(verified)
    if lock_cases:
        query = query.with_for_update(of=Case, skip_locked=True).execution_options(
            populate_existing=True
        )

    cases = (
        (await db.execute(query.order_by(Case.updated_at, Case.id).limit(MAX_BATCH)))
        .scalars()
        .all()
    )
    context = await closed_case_context(
        db, cases, positive_recommendations=dataset == "adjustments"
    )
    prior = await prior_exported_days(db, destination.id, dataset, cases)
    records, items = [], []
    for case in cases:
        recommendation = context["recs"].get(case.id)
        decision = context["decs"].get(case.id)
        validation = context["vals"].get(case.id)
        closed_date = case.updated_at.date().isoformat()
        is_backpost, reason, prior_batches = classify_backpost(
            case.store_id, case.business_date, closed_date, prior
        )
        flags = {
            "backpost": is_backpost,
            "backpost_reason": reason,
            "prior_batches": ";".join(prior_batches) or None,
        }
        if dataset == "resolutions":
            record = {
                "case_number": case.case_number,
                "business_date": case.business_date,
                "store_id": case.store_id,
                "case_type": case.case_type,
                "exception_amount": str(case.total_exception_amount),
                "estimated_exposure": str(case.total_exposure),
                "currency": case.currency,
                "disposition": recommendation.disposition_type if recommendation else None,
                "action_class": recommendation.action_class if recommendation else None,
                "decided_by": decision.actor if decision else None,
                "decided_at": decision.created_at.isoformat() if decision else None,
                "validation_result": validation.verification_result if validation else None,
                "closed_at": case.updated_at.isoformat(),
                "posting_date": closed_date,
                "audit_reference": context["refs"].get(case.id),
                **flags,
            }
        else:
            if not recommendation or not validation or recommendation.financial_impact <= 0:
                continue
            if not case.currency:
                raise HTTPException(
                    status_code=422,
                    detail=f"Case {case.case_number} has no currency; financial export cannot proceed",
                )
            record = {
                "journal_id": f"NIM-{case.case_number}",
                "posting_date": closed_date,
                "business_date": case.business_date,
                "store_id": case.store_id,
                "currency": case.currency,
                "amount": str(recommendation.financial_impact),
                "adjustment_type": recommendation.disposition_type,
                "reason": case.case_type,
                "reference": case.case_number,
                "approved_by": decision.actor if decision else None,
                "validated": True,
                "audit_reference": context["refs"].get(case.id),
                **flags,
            }
        records.append(record)
        record_ref = record.get("case_number") or record.get("journal_id")
        items.append(
            (
                case.id,
                "v1",
                {
                    "record_ref": record_ref,
                    "store_id": case.store_id,
                    "business_date": case.business_date,
                    "backpost": is_backpost,
                },
            )
        )
    return records, items, None, None

"""Select eligible export records and build their immutable data representation."""

import hashlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import String, and_, cast, false, func, literal, not_, or_, select, true, tuple_
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.models import (
    Case,
    HumanDecision,
    Recommendation,
    ValidationObligation,
)
from apps.corrections.models import TransactionAdjustment
from apps.exports.eligibility import (
    completed_case_evidence,
    store_day_export_invalid,
    transaction_export_invalid,
    verified_adjustment_evidence,
)
from apps.exports.models import ExportBatch, ExportDestination, ExportItem
from apps.store_days.models import AuditTotal, StoreDay
from apps.system.options import export_unit_of_work
from apps.totals.models import AuditTotalDefinition
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)

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
    store_ids: list[str] | set[str] | None = None,
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

    if dataset == "transaction_corrections":
        reserved = (
            select(ExportItem.id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.object_id == TransactionAdjustment.id,
                ExportItem.object_version == "v1",
            )
            .exists()
        )
        previously_exported = (
            select(ExportItem.id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.state == "Delivered",
                ExportItem.store_id == CanonicalTransaction.store_id,
                ExportItem.business_date == CanonicalTransaction.business_date,
            )
            .exists()
        )
        query = (
            select(
                TransactionAdjustment, CanonicalTransaction, previously_exported.label("backpost")
            )
            .join(
                CanonicalTransaction,
                CanonicalTransaction.id == TransactionAdjustment.transaction_id,
            )
            .where(
                TransactionAdjustment.status == "Approved",
                not_(reserved),
            )
        )
        if store_ids is not None:
            query = query.where(
                CanonicalTransaction.store_id.in_(store_ids) if store_ids else false()
            )
        if scope == "backposts":
            query = query.where(previously_exported)
        elif scope == "current":
            query = query.where(not_(previously_exported))
        rows = (
            await db.execute(
                query.order_by(
                    CanonicalTransaction.business_date,
                    CanonicalTransaction.store_id,
                    TransactionAdjustment.reviewed_at,
                    TransactionAdjustment.id,
                ).limit(MAX_BATCH)
            )
        ).all()
        records, items = [], []
        for adjustment, transaction, is_backpost in rows:
            record_ref = f"{transaction.transaction_number or transaction.id}:{adjustment.id[:8]}"
            records.append(
                {
                    "adjustment_id": adjustment.id,
                    "transaction_id": transaction.id,
                    "transaction_number": transaction.transaction_number,
                    "store_id": transaction.store_id,
                    "business_date": transaction.business_date,
                    "transaction_type": transaction.transaction_type,
                    "original_amount": str(transaction.signed_amount),
                    "currency": transaction.currency,
                    "adjustment_type": adjustment.adjustment_type,
                    "proposed_values": adjustment.proposed_values,
                    "rationale": adjustment.rationale,
                    "proposed_by": adjustment.proposed_by,
                    "approved_by": adjustment.reviewed_by,
                    "approved_at": adjustment.reviewed_at.isoformat()
                    if adjustment.reviewed_at
                    else None,
                    "backpost": is_backpost,
                }
            )
            items.append(
                (
                    adjustment.id,
                    "v1",
                    {
                        "record_ref": record_ref,
                        "store_id": transaction.store_id,
                        "business_date": transaction.business_date,
                        "backpost": is_backpost,
                    },
                )
            )
        return records, items, None, None

    if dataset == "audited_totals":
        configured_usages = (
            await db.execute(
                select(AuditTotalDefinition.id, AuditTotalDefinition.usages).where(
                    AuditTotalDefinition.usages.is_not(None)
                )
            )
        ).all()
        restricted_definitions = []
        allowed_definitions = []
        for definition_id, usages in configured_usages:
            if usages is None or usages == []:
                continue
            restricted_definitions.append(definition_id)
            if not isinstance(usages, list):
                continue
            if any(
                isinstance(usage, dict) and usage.get("destination_id") == destination.id
                for usage in usages
            ):
                allowed_definitions.append(definition_id)

        def chunks(values: list[str]):
            return [values[offset : offset + 400] for offset in range(0, len(values), 400)]

        usage_conditions = []
        if restricted_definitions:
            usage_conditions.append(AuditTotal.definition_id.is_(None))
            usage_conditions.append(
                and_(
                    *[
                        not_(AuditTotal.definition_id.in_(part))
                        for part in chunks(restricted_definitions)
                    ]
                )
            )
            usage_conditions.extend(
                AuditTotal.definition_id.in_(part) for part in chunks(allowed_definitions)
            )
        usage_filter = or_(*usage_conditions) if usage_conditions else true()
        version = "v" + cast(StoreDay.audit_version, String)
        reserved = (
            select(ExportItem.id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.object_id == AuditTotal.id,
                ExportItem.object_version == version,
            )
            .exists()
        )
        previously_exported = (
            select(ExportItem.id)
            .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.state == "Delivered",
                ExportBatch.status.in_(("Sent", "Acknowledged")),
                ExportItem.store_id == StoreDay.store_id,
                ExportItem.business_date == StoreDay.business_date,
            )
            .exists()
        )
        day_query = (
            select(StoreDay, AuditTotal, previously_exported.label("is_backpost"))
            .join(
                AuditTotal,
                (AuditTotal.store_day_id == StoreDay.id)
                & (AuditTotal.audit_version == StoreDay.audit_version),
            )
            .where(
                StoreDay.status == "Closed",
                StoreDay.totals_stale.is_(False),
                not_(reserved),
                usage_filter,
            )
        )
        day_query = day_query.where(
            not_(
                store_day_export_invalid(
                    db, destination.name, StoreDay.store_id, StoreDay.business_date
                )
            )
        )
        if scope == "backposts":
            day_query = day_query.where(previously_exported)
        elif scope == "current":
            day_query = day_query.where(not_(previously_exported))
        if lock_cases:
            day_query = day_query.with_for_update(of=StoreDay, skip_locked=True)
        rows = (
            await db.execute(
                day_query.order_by(StoreDay.business_date, StoreDay.store_id, AuditTotal.id).limit(
                    MAX_BATCH
                )
            )
        ).all()
        records, items = [], []
        for day, total, is_backpost in rows:
            version = f"v{day.audit_version}"
            record = {
                "store_id": day.store_id,
                "business_date": day.business_date,
                "audit_version": day.audit_version,
                "total_name": total.total_name,
                "level": total.level,
                "dimension": total.dimension_key,
                "rollup_key": total.rollup_key,
                "calculated_amount": str(total.calculated_amount),
                "declared_amount": str(total.declared_amount)
                if total.declared_amount is not None
                else None,
                "variance_amount": str(total.variance_amount)
                if total.variance_amount is not None
                else None,
                "currency": total.currency,
                "backpost": is_backpost,
                "backpost_reason": "A newer totals version or late addition for an exported store day"
                if is_backpost
                else None,
            }
            records.append(record)
            items.append(
                (
                    total.id,
                    version,
                    {
                        "record_ref": f"{day.store_id}/{day.business_date}/{total.total_name}/{total.dimension_key or 'store'}/{hashlib.sha256(total.rollup_key.encode()).hexdigest()[:12] if total.rollup_key else 'all'}",
                        "store_id": day.store_id,
                        "business_date": day.business_date,
                        "backpost": is_backpost,
                    },
                )
            )
        return records, items, None, None

    if dataset == "audited_transactions":
        unit_of_work = await export_unit_of_work(db)
        reserved = (
            select(ExportItem.id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.object_id == CanonicalTransaction.id,
                ExportItem.object_version == "v1",
            )
            .exists()
        )
        previously_exported = (
            select(ExportItem.id)
            .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
            .where(
                ExportItem.destination_id == destination.id,
                ExportItem.dataset == dataset,
                ExportItem.state == "Delivered",
                ExportBatch.status.in_(("Sent", "Acknowledged")),
                ExportItem.store_id == CanonicalTransaction.store_id,
                ExportItem.business_date == CanonicalTransaction.business_date,
            )
            .exists()
        )
        transaction_query = select(
            CanonicalTransaction,
            literal(None) if unit_of_work == "Transaction" else StoreDay,
            previously_exported.label("is_backpost"),
        ).where(not_(reserved))
        if unit_of_work == "Transaction":
            transaction_query = transaction_query.where(
                not_(transaction_export_invalid(db, destination.name, CanonicalTransaction)),
            )
        else:
            transaction_query = transaction_query.join(
                StoreDay,
                (StoreDay.store_id == CanonicalTransaction.store_id)
                & (StoreDay.business_date == CanonicalTransaction.business_date),
            ).where(
                StoreDay.status == "Closed",
                StoreDay.totals_stale.is_(False),
                not_(
                    store_day_export_invalid(
                        db,
                        destination.name,
                        CanonicalTransaction.store_id,
                        CanonicalTransaction.business_date,
                    )
                ),
            )
        if store_ids is not None:
            transaction_query = transaction_query.where(
                CanonicalTransaction.store_id.in_(store_ids) if store_ids else false()
            )
        if scope == "backposts":
            transaction_query = transaction_query.where(previously_exported)
        elif scope == "current":
            transaction_query = transaction_query.where(not_(previously_exported))
        if lock_cases:
            transaction_query = transaction_query.with_for_update(
                of=CanonicalTransaction if unit_of_work == "Transaction" else StoreDay,
                skip_locked=True,
            )
        selected = (
            await db.execute(
                transaction_query.order_by(
                    CanonicalTransaction.business_date,
                    CanonicalTransaction.store_id,
                    CanonicalTransaction.id,
                ).limit(MAX_BATCH)
            )
        ).all()
        transactions = [row[0] for row in selected]
        day_by_transaction = {row[0].id: (row[1], row[2]) for row in selected}
        ids = [transaction.id for transaction in transactions]
        lines: dict[str, list] = {transaction_id: [] for transaction_id in ids}
        taxes: dict[str, list] = {transaction_id: [] for transaction_id in ids}
        discounts: dict[str, list] = {transaction_id: [] for transaction_id in ids}
        tenders: dict[str, list] = {transaction_id: [] for transaction_id in ids}
        for offset in range(0, len(ids), SQLITE_BIND_BATCH_SIZE):
            id_batch = ids[offset : offset + SQLITE_BIND_BATCH_SIZE]
            for row in (
                await db.execute(
                    select(TransactionLine)
                    .where(TransactionLine.transaction_id.in_(id_batch))
                    .order_by(TransactionLine.transaction_id, TransactionLine.line_no)
                )
            ).scalars():
                lines[row.transaction_id].append(
                    {
                        "line_no": row.line_no,
                        "item_code": row.item_code,
                        "description": row.description,
                        "quantity": str(row.quantity),
                        "unit_price": str(row.unit_price),
                        "original_unit_price": (
                            str(row.original_unit_price)
                            if row.original_unit_price is not None
                            else None
                        ),
                        "price_override_reason": row.price_override_reason,
                        "source_attributes": row.source_attributes,
                        "gross_amount": str(row.gross_amount),
                        "tax_code": row.tax_code,
                        "tax_rate": str(row.tax_rate),
                        "tax_amount": str(row.tax_amount),
                        "return_reason": row.return_reason,
                    }
                )
            for row in (
                await db.execute(
                    select(TransactionTax)
                    .where(TransactionTax.transaction_id.in_(id_batch))
                    .order_by(TransactionTax.transaction_id, TransactionTax.tax_code)
                )
            ).scalars():
                taxes[row.transaction_id].append(
                    {
                        "code": row.tax_code,
                        "name": row.tax_name,
                        "rate": str(row.rate),
                        "taxable_amount": str(row.taxable_amount),
                        "tax_amount": str(row.tax_amount),
                    }
                )
            for row in (
                await db.execute(
                    select(TransactionDiscount)
                    .where(TransactionDiscount.transaction_id.in_(id_batch))
                    .order_by(TransactionDiscount.transaction_id, TransactionDiscount.id)
                )
            ).scalars():
                discounts[row.transaction_id].append(
                    {
                        "line_no": row.line_no,
                        "kind": row.kind,
                        "code": row.code,
                        "description": row.description,
                        "amount": str(row.amount),
                    }
                )
            for row in (
                await db.execute(
                    select(TransactionTender)
                    .where(TransactionTender.transaction_id.in_(id_batch))
                    .order_by(TransactionTender.transaction_id, TransactionTender.id)
                )
            ).scalars():
                tenders[row.transaction_id].append(
                    {
                        "type": row.tender_type,
                        "amount": str(row.amount),
                        "reference": row.reference,
                        "authorization": row.authorization,
                        "source_attributes": row.source_attributes,
                    }
                )
        records, items = [], []
        for transaction in transactions:
            _, is_backpost = day_by_transaction[transaction.id]
            records.append(
                {
                    "transaction_id": transaction.id,
                    "transaction_number": transaction.transaction_number,
                    "business_date": transaction.business_date,
                    "event_timestamp": transaction.event_timestamp.isoformat(),
                    "store_id": transaction.store_id,
                    "register_id": transaction.register_id,
                    "cashier_id": transaction.cashier_id,
                    "salesperson_id": transaction.salesperson_id,
                    "transaction_type": transaction.transaction_type,
                    "transaction_subtype": transaction.transaction_subtype,
                    "amount": str(transaction.signed_amount),
                    "currency": transaction.currency,
                    "quantity": transaction.quantity,
                    "channel_id": transaction.channel_id,
                    "customer_id": transaction.customer_id,
                    "user_references": transaction.user_references,
                    "tender_type": transaction.tender_type,
                    "reconciliation_status": transaction.reconciliation_status,
                    "detail_status": transaction.detail_status,
                    "disposition": transaction.disposition,
                    "original_reference": transaction.original_reference,
                    "return_reason": transaction.return_reason,
                    "items": lines[transaction.id],
                    "taxes": taxes[transaction.id],
                    "discounts": discounts[transaction.id],
                    "tenders": tenders[transaction.id],
                    "backpost": is_backpost,
                    "backpost_reason": "Late addition to a business day already exported"
                    if is_backpost
                    else None,
                }
            )
            items.append(
                (
                    transaction.id,
                    "v1",
                    {
                        "record_ref": transaction.transaction_number or transaction.id,
                        "store_id": transaction.store_id,
                        "business_date": transaction.business_date,
                        "backpost": is_backpost,
                    },
                )
            )
        return records, items, None, None

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

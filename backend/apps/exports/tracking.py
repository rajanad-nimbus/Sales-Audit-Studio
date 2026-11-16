"""Database projection and pagination for export delivery tracking."""

from sqlalchemy import String, and_, cast, func, literal, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.cases.models import Case, Recommendation
from apps.corrections.models import TransactionAdjustment
from apps.exports.eligibility import (
    store_day_export_invalid,
    transaction_export_invalid,
    verified_adjustment_evidence,
)
from apps.exports.models import ExportBatch, ExportDestination, ExportItem
from apps.store_days.models import AuditTotal, StoreDay
from apps.system.options import export_unit_of_work
from apps.transactions.models import CanonicalTransaction

TRACK_STATES = ("delivered", "in_batch", "waiting", "not_eligible")


def transaction_ineligibility_reason(row) -> str:
    """Explain the first transaction-level rule keeping a record out of export."""
    if row["detail_status"] == "Unbalanced":
        return "Transaction item, tax, discount, or tender details do not balance."
    if row["reconciliation_status"] not in ("Matched", "Manually Matched", "Exception"):
        return f"Reconciliation is {row['reconciliation_status']}; match the transaction before export."
    return "An unresolved audit error blocks this destination."


async def audited_transactions_tracking_page(
    db, destination_id, state_filter, search, page, page_size
):
    """Track delivery state for canonical transactions in their latest immutable snapshot."""
    unit_of_work = await export_unit_of_work(db)
    destination_name = (
        await db.execute(
            select(ExportDestination.name).where(ExportDestination.id == destination_id)
        )
    ).scalar_one_or_none()
    prior = (
        select(ExportItem.id)
        .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .where(
            ExportItem.destination_id == destination_id,
            ExportItem.dataset == "audited_transactions",
            ExportItem.state == "Delivered",
            ExportBatch.status.in_(("Sent", "Acknowledged")),
            ExportItem.store_id == CanonicalTransaction.store_id,
            ExportItem.business_date == CanonicalTransaction.business_date,
        )
        .exists()
    )
    transaction_invalid = (
        transaction_export_invalid(db, destination_name or "", CanonicalTransaction)
        if destination_name
        else CanonicalTransaction.reconciliation_status.not_in(
            ("Matched", "Manually Matched", "Exception")
        )
        | (CanonicalTransaction.detail_status == "Unbalanced")
    )
    state = sql_case(
        (ExportItem.id.is_not(None) & (ExportItem.state == "Delivered"), "delivered"),
        (ExportItem.id.is_not(None), "in_batch"),
        (
            transaction_invalid
            if unit_of_work == "Transaction"
            else (
                (StoreDay.id.is_(None))
                | (StoreDay.status != "Closed")
                | StoreDay.totals_stale.is_(True)
                | (
                    store_day_export_invalid(
                        db,
                        destination_name,
                        CanonicalTransaction.store_id,
                        CanonicalTransaction.business_date,
                    )
                    if destination_name
                    else False
                )
            ),
            "not_eligible",
        ),
        else_="waiting",
    )
    base = (
        select(
            CanonicalTransaction.id.label("object_id"),
            CanonicalTransaction.transaction_number,
            CanonicalTransaction.store_id,
            CanonicalTransaction.business_date,
            CanonicalTransaction.reconciliation_status,
            CanonicalTransaction.detail_status,
            state.label("state"),
            ExportItem.record_ref,
            ExportItem.backpost.label("item_backpost"),
            ExportItem.delivered_at,
            ExportBatch.id.label("batch_id"),
            ExportBatch.status.label("batch_status"),
            (prior & (ExportItem.id.is_(None))).label("waiting_backpost"),
            StoreDay.status.label("day_status"),
            StoreDay.totals_stale,
        )
        .select_from(CanonicalTransaction)
        .outerjoin(
            StoreDay,
            (StoreDay.store_id == CanonicalTransaction.store_id)
            & (StoreDay.business_date == CanonicalTransaction.business_date),
        )
        .outerjoin(
            ExportItem,
            (ExportItem.object_id == CanonicalTransaction.id)
            & (ExportItem.object_version == "v1")
            & (ExportItem.destination_id == destination_id)
            & (ExportItem.dataset == "audited_transactions"),
        )
        .outerjoin(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .cte("audited_transactions_tracking")
    )
    summary_rows = (
        await db.execute(
            select(
                base.c.state,
                func.count(),
                func.sum(sql_case((base.c.waiting_backpost, 1), else_=0)),
            ).group_by(base.c.state)
        )
    ).all()
    summary = {name: 0 for name in TRACK_STATES}
    summary["waiting_backposts"] = 0
    for row_state, count, backposts in summary_rows:
        summary[row_state] = count
        summary["waiting_backposts"] += backposts or 0
    conditions = []
    if state_filter:
        conditions.append(base.c.state == state_filter)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                base.c.record_ref.ilike(pattern),
                base.c.transaction_number.ilike(pattern),
                base.c.store_id.ilike(pattern),
                base.c.business_date.ilike(pattern),
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(base).where(*conditions))
    ).scalar_one()
    selected = (
        (
            await db.execute(
                select(base)
                .where(*conditions)
                .order_by(
                    base.c.state,
                    base.c.business_date,
                    base.c.store_id,
                    base.c.transaction_number,
                    base.c.object_id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    items = []
    for row in selected:
        ref = row["record_ref"] or row["transaction_number"] or row["object_id"]
        items.append(
            {
                "object_id": row["object_id"],
                "record_ref": ref,
                "case_number": ref,
                "store_id": row["store_id"],
                "business_date": row["business_date"],
                "case_status": (
                    f"{row['reconciliation_status']} / {row['detail_status']}"
                    if unit_of_work == "Transaction"
                    else row["day_status"] or "No store day"
                ),
                "state": row["state"],
                "reason": (
                    (
                        transaction_ineligibility_reason(row)
                        if unit_of_work == "Transaction"
                        else "Store day is not closed or totals are stale"
                    )
                    if row["state"] == "not_eligible"
                    else "The batch failed. Retry it, or cancel it to release these transactions."
                    if row["state"] == "in_batch" and row["batch_status"] == "Failed"
                    else "Batch is being delivered."
                    if row["state"] == "in_batch"
                    else None
                ),
                "batch_id": row["batch_id"],
                "batch_status": row["batch_status"],
                "delivered_at": row["delivered_at"],
                "backpost": bool(
                    row["item_backpost"] if row["batch_id"] else row["waiting_backpost"]
                ),
                "backpost_reason": None
                if row["batch_id"]
                else (
                    "Late addition to a business day already exported"
                    if row["waiting_backpost"]
                    else None
                ),
            }
        )
    return summary, items, total


async def audited_totals_tracking_page(db, destination_id, state_filter, search, page, page_size):
    """Track current totals versions independently of case-based export datasets."""
    destination_name = (
        await db.execute(
            select(ExportDestination.name).where(ExportDestination.id == destination_id)
        )
    ).scalar_one_or_none()
    version = "v" + cast(StoreDay.audit_version, String)
    item_join = (
        (ExportItem.object_id == AuditTotal.id)
        & (ExportItem.object_version == version)
        & (ExportItem.destination_id == destination_id)
        & (ExportItem.dataset == "audited_totals")
    )
    prior = (
        select(ExportItem.id)
        .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .where(
            ExportItem.destination_id == destination_id,
            ExportItem.dataset == "audited_totals",
            ExportItem.state == "Delivered",
            ExportBatch.status.in_(("Sent", "Acknowledged")),
            ExportItem.store_id == StoreDay.store_id,
            ExportItem.business_date == StoreDay.business_date,
        )
        .exists()
    )
    state = sql_case(
        (ExportItem.id.is_not(None) & (ExportItem.state == "Delivered"), "delivered"),
        (ExportItem.id.is_not(None), "in_batch"),
        (
            (StoreDay.status != "Closed")
            | StoreDay.totals_stale.is_(True)
            | (
                store_day_export_invalid(
                    db,
                    destination_name,
                    StoreDay.store_id,
                    StoreDay.business_date,
                )
                if destination_name
                else False
            ),
            "not_eligible",
        ),
        else_="waiting",
    )
    base = (
        select(
            AuditTotal.id.label("object_id"),
            StoreDay.store_id,
            StoreDay.business_date,
            AuditTotal.total_name,
            AuditTotal.level,
            AuditTotal.dimension_key,
            AuditTotal.rollup_key,
            state.label("state"),
            ExportItem.record_ref,
            ExportItem.backpost.label("item_backpost"),
            ExportItem.delivered_at,
            ExportBatch.id.label("batch_id"),
            ExportBatch.status.label("batch_status"),
            (prior & (ExportItem.id.is_(None))).label("waiting_backpost"),
            StoreDay.status.label("day_status"),
            StoreDay.totals_stale,
        )
        .select_from(AuditTotal)
        .join(StoreDay, StoreDay.id == AuditTotal.store_day_id)
        .outerjoin(ExportItem, item_join)
        .outerjoin(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .where(AuditTotal.audit_version == StoreDay.audit_version)
        .cte("audited_totals_tracking")
    )
    summary_rows = (
        await db.execute(
            select(
                base.c.state,
                func.count(),
                func.sum(sql_case((base.c.waiting_backpost, 1), else_=0)),
            ).group_by(base.c.state)
        )
    ).all()
    summary = {name: 0 for name in TRACK_STATES}
    summary["waiting_backposts"] = 0
    for row_state, count, backposts in summary_rows:
        summary[row_state] = count
        summary["waiting_backposts"] += backposts or 0
    conditions = []
    if state_filter:
        conditions.append(base.c.state == state_filter)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                base.c.record_ref.ilike(pattern),
                base.c.store_id.ilike(pattern),
                base.c.business_date.ilike(pattern),
                base.c.total_name.ilike(pattern),
                base.c.dimension_key.ilike(pattern),
                base.c.rollup_key.ilike(pattern),
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(base).where(*conditions))
    ).scalar_one()
    selected = (
        (
            await db.execute(
                select(base)
                .where(*conditions)
                .order_by(
                    base.c.state,
                    base.c.business_date,
                    base.c.store_id,
                    base.c.total_name,
                    base.c.dimension_key,
                    base.c.rollup_key,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    items = []
    for row in selected:
        ref = (
            row["record_ref"]
            or f"{row['store_id']}/{row['business_date']}/{row['total_name']}/{row['dimension_key'] or 'store'}/{row['rollup_key'] or 'all'}"
        )
        items.append(
            {
                "object_id": row["object_id"],
                "record_ref": ref,
                "case_number": ref,
                "store_id": row["store_id"],
                "business_date": row["business_date"],
                "dimension": row["dimension_key"],
                "rollup_key": row["rollup_key"],
                "case_status": row["day_status"],
                "state": row["state"],
                "reason": (
                    "Store day is not closed or totals are stale"
                    if row["state"] == "not_eligible"
                    else "The batch failed. Retry it, or cancel it to release these totals."
                    if row["state"] == "in_batch" and row["batch_status"] == "Failed"
                    else "Batch is being delivered."
                    if row["state"] == "in_batch"
                    else None
                ),
                "batch_id": row["batch_id"],
                "batch_status": row["batch_status"],
                "delivered_at": row["delivered_at"],
                "backpost": bool(
                    row["item_backpost"] if row["batch_id"] else row["waiting_backpost"]
                ),
                "backpost_reason": None
                if row["batch_id"]
                else (
                    "Late addition or newer totals version for a previously exported store day"
                    if row["waiting_backpost"]
                    else None
                ),
            }
        )
    return summary, items, total


async def transaction_corrections_tracking_page(
    db, destination_id, state_filter, search, page, page_size
):
    """Track approved transaction corrections independently from case-resolution batches."""
    prior = (
        select(ExportItem.id)
        .where(
            ExportItem.destination_id == destination_id,
            ExportItem.dataset == "transaction_corrections",
            ExportItem.state == "Delivered",
            ExportItem.store_id == CanonicalTransaction.store_id,
            ExportItem.business_date == CanonicalTransaction.business_date,
        )
        .exists()
    )
    state = sql_case(
        (ExportItem.id.is_not(None) & (ExportItem.state == "Delivered"), "delivered"),
        (ExportItem.id.is_not(None), "in_batch"),
        else_="waiting",
    )
    record_ref = func.coalesce(CanonicalTransaction.transaction_number, CanonicalTransaction.id) + (
        ":" + func.substr(TransactionAdjustment.id, 1, 8)
    )
    base = (
        select(
            TransactionAdjustment.id.label("object_id"),
            record_ref.label("record_ref"),
            CanonicalTransaction.store_id,
            CanonicalTransaction.business_date,
            CanonicalTransaction.transaction_number,
            TransactionAdjustment.adjustment_type,
            state.label("state"),
            ExportItem.backpost.label("item_backpost"),
            ExportItem.delivered_at,
            ExportBatch.id.label("batch_id"),
            ExportBatch.status.label("batch_status"),
            (prior & ExportItem.id.is_(None)).label("waiting_backpost"),
        )
        .select_from(TransactionAdjustment)
        .join(
            CanonicalTransaction,
            CanonicalTransaction.id == TransactionAdjustment.transaction_id,
        )
        .outerjoin(
            ExportItem,
            (ExportItem.object_id == TransactionAdjustment.id)
            & (ExportItem.object_version == "v1")
            & (ExportItem.destination_id == destination_id)
            & (ExportItem.dataset == "transaction_corrections"),
        )
        .outerjoin(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .where(TransactionAdjustment.status == "Approved")
        .cte("transaction_corrections_tracking")
    )
    summary_rows = (
        await db.execute(
            select(
                base.c.state,
                func.count(),
                func.sum(sql_case((base.c.waiting_backpost, 1), else_=0)),
            ).group_by(base.c.state)
        )
    ).all()
    summary = {name: 0 for name in TRACK_STATES}
    summary["waiting_backposts"] = 0
    for row_state, count, backposts in summary_rows:
        summary[row_state] = count
        summary["waiting_backposts"] += backposts or 0
    conditions = []
    if state_filter:
        conditions.append(base.c.state == state_filter)
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                base.c.record_ref.ilike(pattern),
                base.c.transaction_number.ilike(pattern),
                base.c.store_id.ilike(pattern),
                base.c.business_date.ilike(pattern),
                base.c.adjustment_type.ilike(pattern),
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(base).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(base)
                .where(*conditions)
                .order_by(base.c.state, base.c.business_date, base.c.store_id, base.c.record_ref)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    items = []
    for row in rows:
        items.append(
            {
                "object_id": row["object_id"],
                "record_ref": row["record_ref"],
                "case_number": row["record_ref"],
                "store_id": row["store_id"],
                "business_date": row["business_date"],
                "case_status": f"Approved {row['adjustment_type']} correction",
                "state": row["state"],
                "reason": (
                    "The batch failed. Retry it, or cancel it to release this correction."
                    if row["state"] == "in_batch" and row["batch_status"] == "Failed"
                    else "Batch is being delivered."
                    if row["state"] == "in_batch"
                    else None
                ),
                "batch_id": row["batch_id"],
                "batch_status": row["batch_status"],
                "delivered_at": row["delivered_at"],
                "backpost": bool(
                    row["item_backpost"] if row["batch_id"] else row["waiting_backpost"]
                ),
                "backpost_reason": (
                    "Correction belongs to a business day previously exported"
                    if not row["batch_id"] and row["waiting_backpost"]
                    else None
                ),
            }
        )
    return summary, items, total


def tracking_projection(destination_id: str, dataset: str):
    """Build the SQL projection used by the paginated tracking API."""
    ranked_items = (
        select(
            ExportItem.object_id.label("object_id"),
            ExportItem.state.label("item_state"),
            ExportItem.record_ref.label("record_ref"),
            ExportItem.backpost.label("item_backpost"),
            ExportItem.delivered_at.label("delivered_at"),
            ExportBatch.id.label("batch_id"),
            ExportBatch.status.label("batch_status"),
            func.row_number()
            .over(
                partition_by=ExportItem.object_id,
                order_by=(ExportBatch.created_at.desc(), ExportBatch.id.desc()),
            )
            .label("row_number"),
        )
        .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .where(
            ExportItem.destination_id == destination_id,
            ExportItem.dataset == dataset,
        )
    ).cte("ranked_export_items")
    items = select(ranked_items).where(ranked_items.c.row_number == 1).cte("latest_export_items")

    approved = (
        select(Recommendation.id)
        .where(
            Recommendation.case_id == Case.id,
            Recommendation.status == "Approved",
        )
        .exists()
    )
    positive_approved = (
        select(Recommendation.id)
        .where(
            Recommendation.case_id == Case.id,
            Recommendation.status == "Approved",
            Recommendation.financial_impact > 0,
        )
        .exists()
    )
    verified = (
        verified_adjustment_evidence()
        .with_only_columns(Recommendation.case_id)
        .where(
            Recommendation.case_id == Case.id,
        )
        .exists()
    )
    prior_case = aliased(Case)
    previously_exported = (
        select(ExportItem.id)
        .select_from(ExportItem)
        .join(
            ExportBatch,
            ExportBatch.id == ExportItem.batch_id,
        )
        .join(prior_case, prior_case.id == ExportItem.object_id)
        .where(
            ExportItem.destination_id == destination_id,
            ExportItem.dataset == dataset,
            ExportItem.state == "Delivered",
            ExportBatch.status.in_(("Sent", "Acknowledged")),
            prior_case.store_id == Case.store_id,
            prior_case.business_date == Case.business_date,
        )
        .exists()
    )

    ready = (
        select(
            Case.id.label("object_id"),
            Case.case_number,
            Case.store_id,
            Case.business_date,
            Case.status.label("case_status"),
            Case.updated_at,
            Case.amount_needs_review,
            items.c.object_id.label("item_object_id"),
            items.c.item_state,
            items.c.record_ref.label("item_record_ref"),
            items.c.item_backpost,
            items.c.delivered_at,
            items.c.batch_id,
            items.c.batch_status,
            approved.label("has_approved"),
            positive_approved.label("has_positive_approval"),
            verified.label("is_validated"),
            previously_exported.label("previously_exported"),
        )
        .select_from(Case)
        .outerjoin(items, items.c.object_id == Case.id)
        .cte("tracking_readiness")
    )

    has_item = ready.c.item_object_id.is_not(None)
    state = sql_case(
        (and_(has_item, ready.c.item_state == "Delivered"), "delivered"),
        (has_item, "in_batch"),
        (ready.c.case_status != "Closed", "not_eligible"),
        (ready.c.amount_needs_review.is_(True), "not_eligible"),
        *(
            [
                (~ready.c.has_approved, "not_eligible"),
                (~ready.c.has_positive_approval, "not_eligible"),
                (~ready.c.is_validated, "not_eligible"),
            ]
            if dataset == "adjustments"
            else []
        ),
        else_="waiting",
    )
    waiting_backpost = and_(
        state == "waiting",
        or_(
            cast(func.date(ready.c.updated_at), String) > ready.c.business_date,
            ready.c.previously_exported,
        ),
    )
    record_ref = func.coalesce(
        func.nullif(ready.c.item_record_ref, ""),
        ready.c.case_number if dataset == "resolutions" else literal("NIM-") + ready.c.case_number,
    )
    reason = sql_case(
        (
            and_(has_item, ready.c.item_state != "Delivered"),
            sql_case(
                (
                    ready.c.batch_status == "Failed",
                    "The batch failed. Retry it, or cancel it to release these records.",
                ),
                else_="Batch is being delivered.",
            ),
        ),
        (
            ready.c.case_status != "Closed",
            literal("Case is ") + ready.c.case_status + "; only closed cases are exported.",
        ),
        (
            ready.c.amount_needs_review.is_(True),
            "Case amounts are stale after a correction; open a follow-up case for finance review.",
        ),
        *(
            [
                (
                    ~ready.c.has_approved,
                    "No approved recommendation, so there is no adjustment to post.",
                ),
                (
                    ~ready.c.has_positive_approval,
                    "Approved with no financial impact, so there is no journal line.",
                ),
                (~ready.c.is_validated, "Validation is not complete for an approved workflow."),
            ]
            if dataset == "adjustments"
            else []
        ),
        else_=None,
    )
    backpost_reason = sql_case(
        (
            and_(waiting_backpost, ready.c.previously_exported),
            "Late addition to a business day already exported",
        ),
        (waiting_backpost, "Resolved after the business date ended"),
        else_=None,
    )
    return (
        select(
            ready.c.object_id,
            ready.c.case_number,
            ready.c.store_id,
            ready.c.business_date,
            ready.c.case_status,
            state.label("state"),
            reason.label("reason"),
            record_ref.label("record_ref"),
            ready.c.batch_id,
            ready.c.batch_status,
            ready.c.delivered_at,
            ready.c.item_backpost,
            waiting_backpost.label("waiting_backpost"),
            backpost_reason.label("backpost_reason"),
        )
        .select_from(ready)
        .cte("tracking_rows")
    )


async def tracking_page(
    db: AsyncSession,
    destination_id: str,
    dataset: str,
    state_filter: str,
    search: str,
    page: int,
    page_size: int,
) -> tuple[dict, list[dict], int]:
    rows = tracking_projection(destination_id, dataset)
    state_counts = (
        await db.execute(
            select(
                rows.c.state,
                func.count(),
                func.sum(sql_case((rows.c.waiting_backpost, 1), else_=0)),
            ).group_by(rows.c.state)
        )
    ).all()
    summary = {name: 0 for name in TRACK_STATES}
    summary["waiting_backposts"] = 0
    for row_state, count, backposts in state_counts:
        summary[row_state] = count
        summary["waiting_backposts"] += backposts or 0

    conditions = []
    if state_filter:
        conditions.append(rows.c.state == state_filter)
    if search:
        needle = search.strip()
        conditions.append(
            or_(
                rows.c.record_ref.icontains(needle, autoescape=True),
                rows.c.case_number.icontains(needle, autoescape=True),
                rows.c.store_id.icontains(needle, autoescape=True),
                rows.c.business_date.icontains(needle, autoescape=True),
            )
        )
    total = (
        await db.execute(select(func.count()).select_from(rows).where(*conditions))
    ).scalar_one()
    state_order = sql_case(
        (rows.c.state == "in_batch", 0),
        (rows.c.state == "waiting", 1),
        (rows.c.state == "not_eligible", 2),
        else_=3,
    )
    selected = (
        (
            await db.execute(
                select(rows)
                .where(*conditions)
                .order_by(
                    state_order,
                    rows.c.business_date,
                    rows.c.record_ref,
                    rows.c.object_id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    items = []
    for row in selected:
        item_exists = row["batch_id"] is not None
        is_backpost = bool(row["item_backpost"] if item_exists else row["waiting_backpost"])
        items.append(
            {
                "object_id": row["object_id"],
                "record_ref": row["record_ref"],
                "case_number": row["case_number"],
                "store_id": row["store_id"],
                "business_date": row["business_date"],
                "case_status": row["case_status"],
                "state": row["state"],
                "reason": row["reason"],
                "batch_id": row["batch_id"],
                "batch_status": row["batch_status"],
                "delivered_at": row["delivered_at"],
                "backpost": is_backpost,
                "backpost_reason": None if item_exists else row["backpost_reason"],
            }
        )
    return summary, items, total

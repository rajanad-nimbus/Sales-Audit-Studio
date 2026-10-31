"""Database projection and pagination for export delivery tracking."""

from sqlalchemy import String, and_, case as sql_case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.cases.models import Case, Recommendation
from apps.exports.eligibility import verified_adjustment_evidence
from apps.exports.models import ExportBatch, ExportItem


TRACK_STATES = ("delivered", "in_batch", "waiting", "not_eligible")


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

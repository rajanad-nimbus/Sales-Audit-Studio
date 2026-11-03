"""Store-day record lookup and stable API view construction."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.manifests import validate_business_date
from apps.store_days.models import AuditTotal, StoreDay

REQUIRED_COMPARISON_TOTALS = frozenset({"Processor to Bank Variance", "POS to GL Variance"})
OPTIONAL_COMPARISON_TOTALS = frozenset({"POS Control Declared"})


def comparison_blockers(
    day: StoreDay, totals: list[AuditTotal], unresolved_ids: set[str] | None = None
) -> list[str]:
    """Return the conditions that prevent a store day from closing.

    ``unresolved_ids`` names the totals whose variance is neither within tolerance nor
    explained; without it, any non-zero comparison variance blocks.
    """
    comparisons = [
        total
        for total in totals
        if total.level == "Store"
        and total.total_name in REQUIRED_COMPARISON_TOTALS | OPTIONAL_COMPARISON_TOTALS
    ]
    present = {total.total_name for total in comparisons}
    blockers = []
    if day.totals_stale:
        blockers.append("totals are stale; re-total the store day")
    if (
        day.audit_version < 1
        or not REQUIRED_COMPARISON_TOTALS <= present
        or any(total.variance_amount is None for total in comparisons)
    ):
        blockers.append("current reconciliation totals are missing")
    if any(
        total.variance_amount is not None
        and total.variance_amount != 0
        and (unresolved_ids is None or total.id in unresolved_ids)
        for total in comparisons
    ):
        blockers.append("unresolved reconciliation variance")
    return blockers


def total_view(total: AuditTotal) -> dict:
    """Serialize one persisted total without coupling the ORM model to API schemas."""
    return {
        "id": total.id,
        "version": total.audit_version,
        "name": total.total_name,
        "definition_id": total.definition_id,
        "definition_revision": total.definition_revision,
        "level": total.level,
        "dimension": total.dimension_key,
        "calculated_amount": total.calculated_amount,
        "declared_amount": total.declared_amount,
        "variance_amount": total.variance_amount,
        "currency": total.currency,
    }


def store_day_view(day: StoreDay) -> dict:
    """Serialize the public store-day fields shared across lifecycle endpoints."""
    return {
        "id": day.id,
        "store_id": day.store_id,
        "business_date": day.business_date,
        "status": day.status,
        "audit_version": day.audit_version,
        "closed_at": day.closed_at,
        "totals_stale": day.totals_stale,
        "closed_by": day.closed_by,
        "reopened_by": day.reopened_by,
        "reopened_reason": day.reopened_reason,
        "updated_at": day.updated_at,
    }


async def get_day(
    db: AsyncSession, store_id: str, business_date: str, *, lock: bool = False
) -> StoreDay:
    """Validate and load a store day, optionally locking it for a state transition."""
    if not store_id or len(store_id) > 50 or store_id != store_id.strip():
        raise HTTPException(
            status_code=422,
            detail="store_id must contain 1 to 50 characters without surrounding whitespace",
        )
    try:
        validate_business_date(business_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    query = select(StoreDay).where(
        StoreDay.store_id == store_id,
        StoreDay.business_date == business_date,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    day = (await db.execute(query)).scalar_one_or_none()
    if day is None:
        raise HTTPException(
            status_code=404,
            detail="Store day not found; an IT administrator must open it first",
        )
    return day

"""Optional, scoped reconciliation immediately after a standalone source delivery."""

import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest import reconciliation
from apps.ingest.service import prepare_record
from apps.system.options import automatic_audit_after_import_enabled

logger = logging.getLogger(__name__)


async def reconcile_store_scope(
    db: AsyncSession,
    business_date: str,
    store_ids: set[str] | None,
    *,
    request=None,
) -> dict:
    """Reconcile and complete the configured store-day handoff for one date/scope."""
    result = await reconciliation.reconcile(db, business_date, store_ids=store_ids)
    await db.commit()

    from apps.store_days.automation import run_store_day_agent

    result["store_days"] = await run_store_day_agent(db, business_date, store_ids=store_ids)
    from apps.exports.routes import auto_export_valid_transactions

    result["automatic_exports"] = await auto_export_valid_transactions(
        db, request, store_ids=store_ids
    )
    await db.commit()
    return result


async def reconcile_after_import(
    db: AsyncSession,
    source: str,
    records: list[dict],
    *,
    request=None,
    extra_scopes: set[tuple[str, str]] | None = None,
) -> dict | None:
    """Reconcile each affected store/date after ingestion has committed; batch runs still reconcile once at end."""
    if not await automatic_audit_after_import_enabled(db):
        return None

    stores_by_date: dict[str, set[str]] = defaultdict(set)
    for record in records:
        prepared = prepare_record(source, record)
        if not prepared.parsed:
            continue
        store_id = record.get("store_id")
        if not isinstance(store_id, str) or not store_id:
            continue
        parsed = prepared.parsed
        business_date = parsed.get("business_date") or parsed["ts"].date().isoformat()
        stores_by_date[business_date].add(store_id)
    for business_date, store_id in extra_scopes or ():
        stores_by_date[business_date].add(store_id)

    runs = []
    for business_date, store_ids in sorted(stores_by_date.items()):
        try:
            result = await reconcile_store_scope(db, business_date, store_ids, request=request)
            runs.append(
                {
                    "business_date": business_date,
                    "stores": len(store_ids),
                    "store_ids": sorted(store_ids),
                    **result,
                }
            )
        except Exception as exc:
            await db.rollback()
            logger.exception("Automatic audit failed after %s import for %s", source, business_date)
            runs.append(
                {
                    "business_date": business_date,
                    "stores": len(store_ids),
                    "store_ids": sorted(store_ids),
                    "error": str(exc)
                    if isinstance(exc, reconciliation.ReconciliationCapacityError)
                    else f"Automatic audit failed ({type(exc).__name__})",
                }
            )
    return {"enabled": True, "runs": runs}

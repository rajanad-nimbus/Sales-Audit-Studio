"""Which sources are on, and how each one may bring data in.

Three switches per source, kept in the database so IT can change them without a restart:
  enabled         master switch; off means nothing from this source is accepted, by any route
  in_daily_batch  the scheduled batch pulls this source
  manual_sync     a person may sync or upload it by hand
A source with no row behaves as it always did.
"""
import os

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.common import VALID_TYPES
from apps.ingest.models import SourceSetting

SOURCES = ("POS", "POSControl", "Processor", "Bank", "ERP", "Shopify")
PULLED = {"Shopify"}   # sources Nimbus fetches itself; the rest are delivered to it


async def lock_source_settings(db: AsyncSession, source: str, *, shared: bool = False) -> SourceSetting | None:
    """Serialize setting changes and ingestion admission, including when no row exists yet."""
    if db.get_bind().dialect.name == "postgresql":
        lock_function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        await db.execute(
            text(f"SELECT {lock_function}(hashtextextended(:key, 0))"),
            {"key": f"source-setting:{source}"},
        )
    query = select(SourceSetting).where(SourceSetting.source_system == source).with_for_update(read=shared)
    return (await db.execute(query.execution_options(populate_existing=True))).scalar_one_or_none()


def shopify_configured() -> bool:
    """Match the adapter's minimum credentials before advertising Shopify as ready to pull."""
    has_store = bool(os.getenv("SHOPIFY_STORE_URL", "").strip())
    has_token = bool(os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip())
    has_legacy_credentials = bool(os.getenv("SHOPIFY_API_KEY", "").strip()) and bool(
        os.getenv("SHOPIFY_API_PASSWORD", "").strip()
    )
    return has_store and (has_token or has_legacy_credentials)


def effective(source: str, row: SourceSetting | None) -> dict:
    if row is not None:
        return {"enabled": row.enabled, "in_daily_batch": row.in_daily_batch, "manual_sync": row.manual_sync,
                "sync_frequency": row.sync_frequency, "sync_time": row.sync_time,
                "sync_weekday": row.sync_weekday, "sync_timezone": row.sync_timezone, "last_scheduled_run_at": row.last_scheduled_run_at,
                "last_scheduled_status": row.last_scheduled_status, "last_scheduled_message": row.last_scheduled_message}
    return {"enabled": True, "in_daily_batch": source != "Shopify" or shopify_configured(), "manual_sync": True,
            "sync_frequency": None, "sync_time": None, "sync_weekday": None, "sync_timezone": None,
            "last_scheduled_run_at": None, "last_scheduled_status": None,
            "last_scheduled_message": None}


async def load(db: AsyncSession) -> dict[str, dict]:
    rows = {r.source_system: r for r in (await db.execute(select(SourceSetting))).scalars().all()}
    return {s: effective(s, rows.get(s)) for s in SOURCES}


async def batch_sources(db: AsyncSession) -> set[str]:
    return {s for s, v in (await load(db)).items() if v["enabled"] and v["in_daily_batch"]}


async def require_allowed(db: AsyncSession, source: str, *, manual: bool = False) -> None:
    """Refuse a route that would bring in a source that is switched off (or, for a manual route, not open to manual sync)."""
    if source not in VALID_TYPES:
        return   # the route reports its own unknown-source error
    state = (await load(db)).get(source) or effective(source, None)
    if not state["enabled"]:
        raise HTTPException(status_code=409, detail=f"{source} is switched off. Turn it on in Data Pipeline, Sources.")
    if manual and not state["manual_sync"]:
        raise HTTPException(status_code=409, detail=f"Manual sync is switched off for {source}. Turn it on in Data Pipeline, Sources.")

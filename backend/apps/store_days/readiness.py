"""Store-day readiness, worked out from facts already on file.

``StoreDay.status`` records the lifecycle (Open, Auditing, Closed, Reopened). It cannot
also say whether the data is all here, whether the audit is clean and whether the day
was exported, so those three are derived here every time they are read. Nothing is
stored, so they cannot drift from the records they describe.
"""

import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.cases.models import Exception as ExceptionRow
from apps.controls.foundation.models import FoundationReference
from apps.exports.models import ExportBatch, ExportItem
from apps.ingest.common import LINEAGE
from apps.ingest.models import RegisterEvent, SourceRecord
from apps.ingest.pos_formats.standard import DAY_CLOSE
from apps.ingest import source_settings
from apps.ingest.sequence import find_gaps, undismissed_gaps
from apps.security import scope
from apps.store_days.models import AuditTotal, StoreDay
from apps.store_days.variances import evaluate_days, unresolved
from apps.transactions.models import CanonicalTransaction

Key = tuple[str, str]  # (store_id, business_date)
CHUNK = 250
GAP_ERRORS = ("INCOMPLETE_FEED", "MISSING_TRAN_BIG_GAP", "SHOPIFY_INCOMPLETE_FEED")
SALES_LINEAGE = (LINEAGE["POS"], LINEAGE["Shopify"])

# Ingest
COMPLETE = "Complete"
INCOMPLETE = "Incomplete"
NOT_RECONCILED = "Not reconciled"
NO_CONTROL = "No control total"
NO_DATA = "No data"
# Audit
NOT_TOTALED = "Not totaled"
OUT_OF_DATE = "Out of date"
NEEDS_ATTENTION = "Needs attention"
CLEAN = "Clean"
# Export
NOT_EXPORTED = "Not exported"
EXPORTING = "In progress"
EXPORTED = "Exported"
FINAL = "Final"


def chunks(keys: list[Key]):
    for offset in range(0, len(keys), CHUNK):
        yield keys[offset : offset + CHUNK]


async def manifests_by_key(db: AsyncSession, keys: list[Key]) -> dict[Key, set[str]]:
    """Statuses of the control totals on file for each store day: POS manifests and Shopify day totals."""
    if not keys:
        return {}
    wanted = set(keys)
    dates = sorted({business_date for _store, business_date in keys})
    start = datetime.combine(date.fromisoformat(dates[0]), time.min, tzinfo=UTC)
    end = datetime.combine(date.fromisoformat(dates[-1]), time.min, tzinfo=UTC) + timedelta(days=1)
    found: dict[Key, set[str]] = defaultdict(set)
    rows = await db.execute(
        select(SourceRecord.payload, SourceRecord.status).where(
            SourceRecord.source_system == "POSControl",
            SourceRecord.status.in_(("Archived", "Processed")),
            SourceRecord.source_event_time >= start,
            SourceRecord.source_event_time < end,
        )
    )
    # Shopify reports a day total instead of a manifest; it is stamped at the day's first instant.
    midnights = [datetime.combine(date.fromisoformat(day), time.min, tzinfo=UTC) for day in dates]
    day_totals = []
    for offset in range(0, len(midnights), CHUNK):
        day_totals.extend(
            await db.execute(
                select(SourceRecord.payload, SourceRecord.status).where(
                    SourceRecord.source_system == "Shopify",
                    SourceRecord.status.in_(("Archived", "Processed")),
                    SourceRecord.source_event_time.in_(midnights[offset : offset + CHUNK]),
                )
            )
        )
    for payload, status in [*rows, *day_totals]:
        try:
            content = json.loads(payload)
            key = (content["store_id"], str(content["business_date"])[:10])
        except (ValueError, KeyError, TypeError):
            continue
        if content.get("record_type", "DayTotal") != "DayTotal":
            continue
        if key in wanted:
            found[key].add(status)
    return found


async def transaction_counts(db: AsyncSession, keys: list[Key]) -> dict[Key, int]:
    counts: dict[Key, int] = {}
    for batch in chunks(keys):
        rows = await db.execute(
            select(
                CanonicalTransaction.store_id,
                CanonicalTransaction.business_date,
                func.count(),
            )
            .where(
                CanonicalTransaction.source_lineage.in_(SALES_LINEAGE),
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.business_date).in_(
                    batch
                ),
            )
            .group_by(CanonicalTransaction.store_id, CanonicalTransaction.business_date)
        )
        counts.update({(store, day): count for store, day, count in rows})
    return counts


async def open_case_counts(db: AsyncSession, keys: list[Key]) -> dict[Key, int]:
    counts: dict[Key, int] = {}
    for batch in chunks(keys):
        rows = await db.execute(
            select(Case.store_id, Case.business_date, func.count())
            .where(tuple_(Case.store_id, Case.business_date).in_(batch), Case.status != "Closed")
            .group_by(Case.store_id, Case.business_date)
        )
        counts.update({(store, day): count for store, day, count in rows})
    return counts


async def missing_data_errors(db: AsyncSession, keys: list[Key]) -> set[Key]:
    """Store days with an open error saying transactions are missing."""
    gaps: set[Key] = set()
    for batch in chunks(keys):
        rows = await db.execute(
            select(Case.store_id, Case.business_date)
            .join(ExceptionRow, ExceptionRow.case_id == Case.id)
            .where(
                tuple_(Case.store_id, Case.business_date).in_(batch),
                Case.status != "Closed",
                ExceptionRow.exception_type.in_(GAP_ERRORS),
            )
            .distinct()
        )
        gaps.update((store, day) for store, day in rows)
    return gaps


async def export_states(db: AsyncSession, keys: list[Key]) -> dict[Key, dict]:
    """How far each store day's records have gone through export."""
    result: dict[Key, dict] = {}
    for batch in chunks(keys):
        rows = await db.execute(
            select(
                ExportItem.store_id,
                ExportItem.business_date,
                ExportItem.state,
                ExportBatch.status,
                func.count(),
            )
            .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
            .where(tuple_(ExportItem.store_id, ExportItem.business_date).in_(batch))
            .group_by(
                ExportItem.store_id, ExportItem.business_date, ExportItem.state, ExportBatch.status
            )
        )
        tally: dict[Key, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for store, day, item_state, batch_status, count in rows:
            counts = tally[(store, day)]
            counts["records"] += count
            if item_state != "Delivered":
                counts["pending"] += count
            elif batch_status == "Acknowledged":
                counts["acknowledged"] += count
        for key, counts in tally.items():
            records = counts["records"]
            if counts["pending"]:
                state = EXPORTING
            elif counts["acknowledged"] == records:
                state = FINAL
            else:
                state = EXPORTED
            result[key] = {
                "state": state,
                "records": records,
                "pending": counts["pending"],
                "acknowledged": counts["acknowledged"],
            }
    return result


async def unresolved_sequence_gap_counts(db: AsyncSession, days: list[StoreDay]) -> dict[Key, int]:
    """Count current register-number gaps, excluding the latest audited dismissals."""
    if not days:
        return {}
    day_by_key = {(day.store_id, day.business_date): day for day in days}
    gaps: dict[Key, dict[str, list[int]]] = {}
    for batch in chunks(list(day_by_key)):
        raw = await find_gaps(db, day_keys=set(batch))
        gaps.update(await undismissed_gaps(db, raw))
    counts = {key: 0 for key in day_by_key}
    for key, registers in gaps.items():
        if key not in day_by_key:
            continue
        counts[key] = sum(len(numbers) for numbers in registers.values())
    return counts


def ingest_view(transactions: int, manifest_statuses: set[str], gap_open: bool) -> dict:
    if gap_open:
        state, detail = INCOMPLETE, "Fewer transactions arrived than the store's control total"
    elif "Processed" in manifest_statuses:
        state, detail = COMPLETE, "Transactions match the store's control total"
    elif manifest_statuses:
        state, detail = NOT_RECONCILED, "Control total received; reconciliation has not run"
    elif transactions:
        state, detail = NO_CONTROL, "Transactions arrived but no control total to check them"
    else:
        state, detail = NO_DATA, "No transactions or control total yet"
    return {"state": state, "transactions": transactions, "detail": detail}


async def day_close_markers(db: AsyncSession, keys: list[Key]) -> dict[Key, dict[str, int]]:
    """Count POS register events and explicit Day Close markers for the selected store days."""
    result: dict[Key, dict[str, int]] = {}
    for batch in chunks(keys):
        rows = await db.execute(
            select(
                RegisterEvent.store_id,
                RegisterEvent.business_date,
                func.count(),
                func.sum(case((RegisterEvent.event_type == DAY_CLOSE, 1), else_=0)),
            )
            .where(tuple_(RegisterEvent.store_id, RegisterEvent.business_date).in_(batch))
            .group_by(RegisterEvent.store_id, RegisterEvent.business_date)
        )
        result.update(
            {
                (store, business_date): {"events": event_count, "markers": marker_count or 0}
                for store, business_date, event_count, marker_count in rows
            }
        )
    return result


def with_day_close(
    ingest: dict, counts: dict[str, int] | None, *, required: bool = False
) -> dict:
    """Expose the POS close marker separately from transaction/control-total completeness."""
    events = counts["events"] if counts else 0
    markers = counts["markers"] if counts else 0
    state = "Not provided" if events == 0 else "Received" if markers else "Not received"
    result = {
        **ingest,
        "day_close": {
            "state": state,
            "events": events,
            "markers": markers,
            "required": required,
        },
    }
    if required and not markers:
        result["state"] = INCOMPLETE
        missing = "Required POS Day Close marker has not been received"
        result["detail"] = f'{ingest["detail"]}; {missing}' if ingest["detail"] else missing
    return result


async def ingest_states(db: AsyncSession, keys: list[Key]) -> dict[Key, dict]:
    manifests = await manifests_by_key(db, keys)
    counts = await transaction_counts(db, keys)
    gaps = await missing_data_errors(db, keys)
    markers = await day_close_markers(db, keys)
    require_day_close = (await source_settings.load(db))["POS"]["require_day_close"]
    return {
        key: with_day_close(
            ingest_view(counts.get(key, 0), manifests.get(key, set()), key in gaps),
            markers.get(key),
            required=require_day_close,
        )
        for key in keys
    }


async def current_totals(db: AsyncSession, days: list[StoreDay]) -> dict[str, list[AuditTotal]]:
    totals: dict[str, list[AuditTotal]] = defaultdict(list)
    versions = [(day.id, day.audit_version) for day in days if day.audit_version > 0]
    for offset in range(0, len(versions), CHUNK):
        rows = await db.execute(
            select(AuditTotal).where(
                tuple_(AuditTotal.store_day_id, AuditTotal.audit_version).in_(
                    versions[offset : offset + CHUNK]
                ),
                AuditTotal.variance_amount.is_not(None),
            )
        )
        for total in rows.scalars():
            totals[total.store_day_id].append(total)
    return totals


async def readiness(
    db: AsyncSession, days: list[StoreDay], case_counts: dict[Key, int] | None = None
) -> dict[str, dict]:
    """Data, audit and export readiness for each store day, keyed by store-day id.

    Pass ``case_counts`` when the caller has already counted the open cases.
    """
    keys = [(day.store_id, day.business_date) for day in days]
    manifests = await manifests_by_key(db, keys)
    counts = await transaction_counts(db, keys)
    cases = case_counts if case_counts is not None else await open_case_counts(db, keys)
    gaps = await missing_data_errors(db, keys)
    markers = await day_close_markers(db, keys)
    exports = await export_states(db, keys)
    sequence_gaps = await unresolved_sequence_gap_counts(db, days)
    evaluations = await evaluate_days(db, days, await current_totals(db, days))
    require_day_close = (await source_settings.load(db))["POS"]["require_day_close"]
    result = {}
    for day, key in zip(days, keys, strict=True):
        ingest = with_day_close(
            ingest_view(counts.get(key, 0), manifests.get(key, set()), key in gaps),
            markers.get(key),
            required=require_day_close,
        )
        variances = len(unresolved(evaluations.get(day.id, {})))
        open_case_count = cases.get(key, 0)
        missing_transactions = sequence_gaps.get(key, 0)
        if day.audit_version < 1:
            audit_state, audit_detail = NOT_TOTALED, "Re-total the store day to calculate it"
        elif day.totals_stale:
            audit_state, audit_detail = OUT_OF_DATE, "New data or configuration since the totals"
        elif open_case_count or variances or missing_transactions:
            audit_state = NEEDS_ATTENTION
            audit_detail = ", ".join(
                part
                for part in (
                    f"{open_case_count} open case(s)" if open_case_count else "",
                    f"{variances} unexplained variance(s)" if variances else "",
                    f"{missing_transactions} unresolved transaction number gap(s)"
                    if missing_transactions
                    else "",
                )
                if part
            )
        else:
            audit_state, audit_detail = CLEAN, "No open errors or unexplained variances"
        signed_off = (
            day.audit_signed_version == day.audit_version
            and bool(day.audit_signed_by)
            and day.audit_signed_at is not None
            and ingest["state"] == COMPLETE
            and audit_state == CLEAN
        )
        if audit_state == CLEAN:
            audit_detail = (
                f"Audited by {day.audit_signed_by}"
                if signed_off
                else "Totals are clean; auditor sign-off is required"
            )
        export = exports.get(
            key, {"state": NOT_EXPORTED, "records": 0, "pending": 0, "acknowledged": 0}
        )
        audit_status = {
            NOT_TOTALED: "Re-Totalling/Auditing Required",
            OUT_OF_DATE: "Re-Totalling/Auditing Required",
            NEEDS_ATTENTION: "Store Errors Pending",
            CLEAN: "Audited" if signed_off else "Totaled",
        }[audit_state]
        overall_status = (
            "Sales Audit Complete"
            if ingest["state"] == COMPLETE and audit_state == CLEAN and signed_off
            else "Sales Audit In Progress"
        )
        result[day.id] = {
            "ingest": ingest,
            "audit": {
                "state": audit_state,
                "resa_status": audit_status,
                "overall_status": overall_status,
                "open_cases": open_case_count,
                "unexplained_variances": variances,
                "missing_transactions": missing_transactions,
                "detail": audit_detail,
                "signed_off": signed_off,
                "signed_version": day.audit_signed_version,
                "signed_by": day.audit_signed_by,
                "signed_at": day.audit_signed_at,
                "signoff_rationale": day.audit_signoff_rationale,
            },
            "export": export,
            "ready_to_close": day.status != "Closed"
            and ingest["state"] == COMPLETE
            and audit_state == CLEAN
            and signed_off,
        }
    return result


def holding_back(view: dict) -> list[str]:
    """Plain reasons a store day is not moving, in the order they need fixing."""
    reasons = []
    if view["ingest"]["state"] != COMPLETE:
        reasons.append(f"Data: {view['ingest']['state']}")
    if view["audit"]["state"] != CLEAN:
        reasons.append(f"Audit: {view['audit']['detail'] or view['audit']['state']}")
    elif not view["audit"].get("signed_off"):
        reasons.append("Audit: Auditor sign-off required for the current totals")
    if view["export"]["state"] in (EXPORTING,):
        reasons.append(f"Export: {view['export']['pending']} record(s) awaiting delivery")
    return reasons


def data_quality(day: StoreDay, ingest_state: str, version: int) -> dict:
    """Whether the totals of one version can be treated as final."""
    if version != day.audit_version:
        return {"state": "Superseded", "detail": f"Version {day.audit_version} replaces it"}
    if day.totals_stale:
        return {"state": "Stale", "detail": "Data or configuration changed after calculation"}
    if ingest_state == COMPLETE:
        return {"state": "Final", "detail": "Built from data that matches the control total"}
    if ingest_state == INCOMPLETE:
        return {"state": "Partial", "detail": "Built from fewer transactions than expected"}
    return {"state": "Provisional", "detail": f"Data completeness unconfirmed ({ingest_state})"}


async def late_stores(db: AsyncSession, user: dict, business_date: str) -> tuple[int, list[dict]]:
    """Active stores whose data for a business date is not complete.

    Returns how many stores were expected, and one entry for each that is late.
    """
    stores = (
        (
            await db.execute(
                scope.restrict(
                    select(FoundationReference.code).where(
                        FoundationReference.category == "Store",
                        FoundationReference.active.is_(True),
                    ),
                    user,
                    FoundationReference.code,
                ).order_by(FoundationReference.code)
            )
        )
        .scalars()
        .all()
    )
    keys = [(store, business_date) for store in stores]
    ingest = await ingest_states(db, keys)
    opened = set(
        (
            await db.execute(
                select(StoreDay.store_id).where(
                    StoreDay.business_date == business_date, StoreDay.store_id.in_(stores)
                )
            )
        )
        .scalars()
        .all()
    )
    late = [
        {
            "store_id": store,
            "business_date": business_date,
            "store_day_open": store in opened,
            **ingest[(store, business_date)],
        }
        for store in stores
        if ingest[(store, business_date)]["state"] != COMPLETE
    ]
    return len(stores), late

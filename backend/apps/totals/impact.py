"""What activating a draft total-definition revision would change.

Both sides of the comparison are recalculated from the same canonical
transactions, so a difference is caused by the definition change alone and not
by data that arrived after the store day was last re-totaled.
"""

import hashlib
import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.rules.models import ConfiguredAuditRule
from apps.security import scope
from apps.store_days.models import StoreDay, StoreDayClosureRequest
from apps.totals.calculation import NOT_LIVE_STATUSES, calculate_store_day, effective_definitions
from apps.totals.currency import single_currency
from apps.totals.models import AuditTotalDefinition

IMPACT_STORE_DAY_LIMIT = 500


async def live_revision(
    db: AsyncSession, draft: AuditTotalDefinition
) -> AuditTotalDefinition | None:
    """The newest non-draft revision of the same total, which activation replaces."""
    return (
        await db.execute(
            select(AuditTotalDefinition)
            .where(
                AuditTotalDefinition.name == draft.name,
                AuditTotalDefinition.status.not_in(NOT_LIVE_STATUSES),
                AuditTotalDefinition.revision < draft.revision,
            )
            .order_by(AuditTotalDefinition.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def affected_dates(
    draft: AuditTotalDefinition, live: AuditTotalDefinition | None
) -> tuple[str | None, str | None]:
    """The widest range in which the effective revision can differ; None is unbounded."""
    revisions = [draft] if live is None else [draft, live]
    starts = [row.start_date for row in revisions]
    ends = [row.end_date for row in revisions]
    return (
        None if None in starts else min(starts),
        None if None in ends else max(ends),
    )


async def _side(db, store_id, business_date, draft):
    """Configured totals by name for one side of the comparison, or the error it raises.

    Also returns the Store-level value of each total, which is what audit rules read,
    and the store day's currency.
    """
    try:
        definitions = await effective_definitions(
            db, business_date, store_id, validate_all_combined=True, draft=draft
        )
        calculation = await calculate_store_day(db, store_id, business_date, definitions)
    except ValueError as exc:
        return None, str(exc), {}, None
    try:
        currency = single_currency(calculation.currencies)
    except ValueError:
        currency = None
    by_id = {row.id: row for row in definitions}
    values = {
        by_id[identifier].name: sum(dimensions.values(), Decimal("0"))
        for identifier, dimensions in calculation.configured_totals.items()
    }
    # A defined Store-level total that matched nothing is zero to a rule, as in re-totaling.
    store_level = {
        row.name: values.get(row.name, Decimal("0")) for row in definitions if row.level == "Store"
    }
    return values, None, store_level, currency


def rule_changes(rules, current: dict, proposed: dict, currency: str | None) -> list[dict]:
    """Audit rules whose pass or fail result differs between the two sides."""
    changes = []
    for rule in rules:
        if rule.threshold_currency is not None and rule.threshold_currency != currency:
            continue
        name = rule.total_name.removeprefix("Configured: ")
        before, after = current.get(name), proposed.get(name)
        breached_before = before is not None and abs(before) > rule.threshold
        breached_after = after is not None and abs(after) > rule.threshold
        if breached_before != breached_after:
            changes.append(
                {
                    "rule_id": rule.id,
                    "rule": rule.name,
                    "total": name,
                    "threshold": rule.threshold,
                    "current": before,
                    "proposed": after,
                    "effect": "Newly breaches" if breached_after else "Clears",
                }
            )
    return changes


async def change_impact(db: AsyncSession, draft: AuditTotalDefinition, user: dict) -> dict:
    """Compare live and proposed totals for every store day the draft could affect."""
    live = await live_revision(db, draft)
    start, end = affected_dates(draft, live)
    query = select(StoreDay).order_by(StoreDay.business_date, StoreDay.store_id)
    if start is not None:
        query = query.where(StoreDay.business_date >= start)
    if end is not None:
        query = query.where(StoreDay.business_date <= end)
    query = scope.restrict(query, user, StoreDay.store_id)
    days = list((await db.execute(query.limit(IMPACT_STORE_DAY_LIMIT + 1))).scalars())
    truncated = len(days) > IMPACT_STORE_DAY_LIMIT
    days = days[:IMPACT_STORE_DAY_LIMIT]

    pending = dict(
        (
            await db.execute(
                select(StoreDayClosureRequest.store_day_id, func.count())
                .where(
                    StoreDayClosureRequest.store_day_id.in_([day.id for day in days]),
                    StoreDayClosureRequest.status == "Pending",
                )
                .group_by(StoreDayClosureRequest.store_day_id)
            )
        ).all()
    )

    # Rules read configured totals by their "Configured: <name>" total name.
    rules = list(
        (
            await db.execute(
                select(ConfiguredAuditRule)
                .where(
                    ConfiguredAuditRule.enabled.is_(True),
                    ConfiguredAuditRule.total_name.like("Configured: %"),
                )
                .order_by(ConfiguredAuditRule.id)
            )
        ).scalars()
    )

    rows = []
    for day in days:
        current, current_error, current_store, currency = await _side(
            db, day.store_id, day.business_date, None
        )
        proposed, proposed_error, proposed_store, proposed_currency = await _side(
            db, day.store_id, day.business_date, draft
        )
        rules_changed = (
            rule_changes(rules, current_store, proposed_store, currency or proposed_currency)
            if current is not None and proposed is not None
            else []
        )
        before = (current or {}).get(draft.name)
        after = (proposed or {}).get(draft.name)
        other_names = sorted(((current or {}).keys() | (proposed or {}).keys()) - {draft.name})
        changed_totals = [
            {
                "name": name,
                "current": (current or {}).get(name),
                "proposed": (proposed or {}).get(name),
            }
            for name in other_names
            if current is not None
            and proposed is not None
            and current.get(name) != proposed.get(name)
        ]
        rows.append(
            {
                "store_id": day.store_id,
                "business_date": day.business_date,
                "store_day_status": day.status,
                "current": before,
                "proposed": after,
                "change": (after or Decimal("0")) - (before or Decimal("0")),
                "changed_totals": changed_totals,
                "current_error": current_error,
                "proposed_error": proposed_error,
                "rule_changes": rules_changed,
                "would_go_stale": day.audit_version > 0 and not day.totals_stale,
                "closure_requests_superseded": pending.get(day.id, 0),
            }
        )

    changed = [
        row
        for row in rows
        if row["change"]
        or row["changed_totals"]
        or row["rule_changes"]
        or row["proposed_error"] != row["current_error"]
    ]
    summary = {
        "store_days": len(rows),
        "changed": len(changed),
        "net_change": sum((row["change"] for row in rows), Decimal("0")),
        "absolute_change": sum((abs(row["change"]) for row in rows), Decimal("0")),
        "closed": sum(1 for row in changed if row["store_day_status"] == "Closed"),
        "would_go_stale": sum(1 for row in rows if row["would_go_stale"]),
        "closure_requests_superseded": sum(row["closure_requests_superseded"] for row in rows),
        "errors": sum(1 for row in rows if row["proposed_error"]),
        "rules_newly_breached": sum(
            1 for row in rows for item in row["rule_changes"] if item["effect"] == "Newly breaches"
        ),
        "rules_cleared": sum(
            1 for row in rows for item in row["rule_changes"] if item["effect"] == "Clears"
        ),
    }
    report = {
        "definition_id": draft.id,
        "name": draft.name,
        "revision": draft.revision,
        "replaces_definition_id": live.id if live else None,
        "replaces_revision": live.revision if live else None,
        "date_range": {"start": start, "end": end},
        "limit": IMPACT_STORE_DAY_LIMIT,
        "truncated": truncated,
        "summary": summary,
        "rows": rows,
    }
    report["fingerprint"] = fingerprint(report)
    return report


def fingerprint(report: dict) -> str:
    """Hash everything the reviewer saw, so activation can prove nothing moved since."""
    content = {key: value for key, value in report.items() if key != "fingerprint"}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

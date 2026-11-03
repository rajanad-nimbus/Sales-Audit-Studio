"""System health: one list of checks, each ok, warning or critical, with what it found."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.models import Case
from apps.diagnostics.feeds import feed_status
from apps.exports.models import ExportBatch
from apps.operations.batch_routes import status as batch_status
from apps.security.authentication import AUTH_ENABLED
from apps.store_days.models import StoreDay
from apps.transactions.models import CanonicalTransaction

OK, WARNING, CRITICAL = "ok", "warning", "critical"
SEVERITY = {OK: 0, WARNING: 1, CRITICAL: 2}
SLOW_QUERY_MS = 500
UNRECONCILED_AFTER_DAYS = 2
UNACKNOWLEDGED_AFTER = timedelta(hours=24)
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def check(name: str, status: str, detail: str, value=None) -> dict:
    return {"name": name, "status": status, "detail": detail, "value": value}


def worst(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=SEVERITY.__getitem__, default=OK)


def expected_revision() -> str | None:
    """The migration this code expects, or None when the migration scripts are not shipped."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


async def database_checks(db: AsyncSession) -> list[dict]:
    started = time.perf_counter()
    await db.execute(select(1))
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    checks = [
        check(
            "Database",
            OK if elapsed <= SLOW_QUERY_MS else WARNING,
            f"Answered a query in {elapsed} ms",
            elapsed,
        )
    ]
    try:
        current = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception:
        await db.rollback()
        current = None
    expected = expected_revision()
    if current is None or expected is None:
        checks.append(check("Database schema", WARNING, "The schema revision could not be read"))
    elif current == expected:
        checks.append(
            check("Database schema", OK, f"At revision {current}, as the code expects", current)
        )
    else:
        checks.append(
            check(
                "Database schema",
                CRITICAL,
                f"The database is at revision {current} but this version expects {expected}",
                current,
            )
        )
    return checks


async def pipeline_checks(db: AsyncSession, now: datetime) -> list[dict]:
    checks = []
    batch = await batch_status(db)
    if batch.get("last_failed"):
        checks.append(check("Daily batch", CRITICAL, "The most recent batch run failed", "failed"))
    elif batch["freshness"] == "never":
        checks.append(check("Daily batch", WARNING, "The daily batch has never succeeded", "never"))
    elif batch["freshness"] == "stale":
        checks.append(
            check(
                "Daily batch",
                WARNING,
                f"The last successful batch finished {batch['hours_since_success']} hours ago",
                "stale",
            )
        )
    else:
        checks.append(
            check(
                "Daily batch",
                OK,
                f"Last succeeded {batch['hours_since_success']} hours ago; data through {batch['data_as_of']}",
                "fresh",
            )
        )

    feeds = await feed_status(db, now)
    silent = [f["source_system"] for f in feeds["feeds"] if f["state"] != "Current"]
    checks.append(
        check(
            "Data feeds",
            WARNING if silent else OK,
            f"No recent data from {', '.join(silent)}"
            if silent
            else "Every feed has sent data recently",
            len(silent),
        )
    )
    held = sum(f["quarantined_24h"] for f in feeds["feeds"])
    checks.append(
        check(
            "Quarantined records",
            WARNING if held else OK,
            f"{held} record(s) set aside in the last 24 hours"
            if held
            else "Nothing set aside in the last 24 hours",
            held,
        )
    )
    cutoff = (now - timedelta(days=UNRECONCILED_AFTER_DAYS)).date().isoformat()
    backlog = (
        await db.execute(
            select(func.count())
            .select_from(CanonicalTransaction)
            .where(
                CanonicalTransaction.reconciliation_status == "Unmatched",
                CanonicalTransaction.business_date < cutoff,
            )
        )
    ).scalar_one()
    checks.append(
        check(
            "Reconciliation backlog",
            WARNING if backlog else OK,
            f"{backlog} transaction(s) from before {cutoff} are still unmatched"
            if backlog
            else "No transaction is waiting more than two days to be matched",
            backlog,
        )
    )
    return checks


async def audit_work_checks(db: AsyncSession, now: datetime) -> list[dict]:
    stale = (
        await db.execute(select(func.count()).select_from(StoreDay).where(StoreDay.totals_stale))
    ).scalar_one()
    late = (
        await db.execute(
            select(func.count())
            .select_from(Case)
            .where(Case.status != "Closed", Case.sla_due_at < now)
        )
    ).scalar_one()
    failed = (
        await db.execute(
            select(func.count()).select_from(ExportBatch).where(ExportBatch.status == "Failed")
        )
    ).scalar_one()
    unacknowledged = (
        await db.execute(
            select(func.count())
            .select_from(ExportBatch)
            .where(ExportBatch.status == "Sent", ExportBatch.sent_at < now - UNACKNOWLEDGED_AFTER)
        )
    ).scalar_one()
    return [
        check(
            "Out-of-date totals",
            WARNING if stale else OK,
            f"{stale} store day(s) need re-totaling"
            if stale
            else "Every store day's totals are current",
            stale,
        ),
        check(
            "Cases past their due time",
            WARNING if late else OK,
            f"{late} open case(s) are past their due time" if late else "No open case is overdue",
            late,
        ),
        check(
            "Failed exports",
            CRITICAL if failed else OK,
            f"{failed} export batch(es) failed and need a retry or cancel"
            if failed
            else "No export has failed",
            failed,
        ),
        check(
            "Unacknowledged exports",
            WARNING if unacknowledged else OK,
            f"{unacknowledged} batch(es) were sent over a day ago and never acknowledged"
            if unacknowledged
            else "Every sent export has been acknowledged or is recent",
            unacknowledged,
        ),
    ]


async def system_health(db: AsyncSession, now: datetime | None = None) -> dict:
    from integrations.ontology.ontology_integration import integration

    now = now or datetime.now(UTC)
    checks = await database_checks(db)
    ontology = integration.status()
    checks += [
        check(
            "User authentication",
            OK if AUTH_ENABLED else WARNING,
            "Users sign in through Ontology Studio"
            if AUTH_ENABLED
            else "Ontology authentication is not configured",
        ),
        check(
            "Approved ontology",
            OK if ontology["ready"] else WARNING,
            "An approved Sales Audit ontology release is in use"
            if ontology["ready"]
            else "No approved ontology release; built-in definitions are in use",
        ),
    ]
    checks += await pipeline_checks(db, now)
    checks += await audit_work_checks(db, now)
    return {"status": worst(checks), "checked_at": now, "checks": checks}

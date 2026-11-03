"""Who has been doing what: audit activity by person and kind, and the changes that matter most.

Read over the newest events only (by sequence number, which is indexed) so it stays quick on a
trail with millions of events.
"""

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent

DEFAULT_WINDOW = 5000
MAX_WINDOW = 100000
MAX_SENSITIVE = 50

# Events that change how money is calculated, who may do what, or whether a day can still change.
SENSITIVE_EVENTS = frozenset(
    {
        "TOTAL_DEFINITION_CREATED",
        "TOTAL_DEFINITION_REVISED",
        "TOTAL_DEFINITION_SUBMITTED",
        "TOTAL_DEFINITION_RETURNED",
        "TOTAL_DEFINITION_ACTIVATED",
        "VARIANCE_TOLERANCE_CREATED",
        "VARIANCE_TOLERANCE_UPDATED",
        "VARIANCE_TOLERANCE_REMOVED",
        "VARIANCE_EXPLAINED",
        "TENDER_TYPE_CREATED",
        "TENDER_TYPE_UPDATED",
        "PERIOD_CLOSED",
        "PERIOD_REOPENED",
        "STORE_DAY_CLOSED",
        "STORE_DAY_REOPENED",
        "STORE_DAY_CLOSURE_REJECTED",
        "STORE_DAY_CONFIGURATION_CHANGED",
        "VOUCHERS_ESCHEATED",
        "TRANSACTION_ADJUSTMENT_APPROVED",
        "TRANSACTION_ADJUSTMENT_REJECTED",
        "MANUAL_MATCH_APPROVED",
        "MANUAL_MATCH_REJECTED",
        "EVIDENCE_OVERRIDE_REQUESTED",
        "EVIDENCE_OVERRIDE_APPROVED",
        "EXPORT_DESTINATION_CREATED",
        "EXPORT_DESTINATION_TOGGLED",
        "RETENTION_POLICY_CREATED",
        "RETENTION_POLICY_UPDATED",
        "RETENTION_RUN",
        "SOURCE_SETTING_CHANGED",
        "AGENT_SETTING_CHANGED",
    }
)


async def audit_activity(db: AsyncSession, events: int) -> dict:
    head = (await db.execute(select(func.max(AuditEvent.seq)))).scalar()
    if head is None:
        return {"window": {"events": 0}, "by_actor": [], "by_type": [], "sensitive": []}
    floor = head - events
    in_window = AuditEvent.seq > floor
    sensitive = AuditEvent.event_type.in_(SENSITIVE_EVENTS)

    span = (
        await db.execute(
            select(
                func.count(),
                func.min(AuditEvent.seq),
                func.min(AuditEvent.created_at),
                func.max(AuditEvent.created_at),
            ).where(in_window)
        )
    ).one()
    by_actor = (
        await db.execute(
            select(
                AuditEvent.actor,
                func.count(),
                func.sum(case((sensitive, 1), else_=0)),
                func.max(AuditEvent.created_at),
            )
            .where(in_window)
            .group_by(AuditEvent.actor)
            .order_by(func.count().desc(), AuditEvent.actor)
            .limit(25)
        )
    ).all()
    by_type = (
        await db.execute(
            select(AuditEvent.event_type, func.count())
            .where(in_window)
            .group_by(AuditEvent.event_type)
            .order_by(func.count().desc(), AuditEvent.event_type)
            .limit(15)
        )
    ).all()
    recent = (
        (
            await db.execute(
                select(AuditEvent)
                .where(in_window, sensitive)
                .order_by(AuditEvent.seq.desc())
                .limit(MAX_SENSITIVE)
            )
        )
        .scalars()
        .all()
    )
    return {
        "window": {
            "events": span[0],
            "from_seq": span[1],
            "to_seq": head,
            "from_time": span[2],
            "to_time": span[3],
        },
        "by_actor": [
            {
                "actor": actor,
                "events": count,
                "sensitive": int(changes or 0),
                "last_at": last,
            }
            for actor, count, changes, last in by_actor
        ],
        "by_type": [{"event_type": kind, "events": count} for kind, count in by_type],
        "sensitive": [
            {
                "seq": e.seq,
                "created_at": e.created_at,
                "actor": e.actor,
                "event_type": e.event_type,
                "description": e.action_description,
                "object_type": e.object_type,
                "object_id": e.object_id,
            }
            for e in recent
        ],
    }

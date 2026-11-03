"""Verify the audit trail's hash chain a bounded window at a time.

Each audit event stores the hash of the one before it, computed by the database when the event is
written. Re-deriving those hashes proves no event was edited or removed. The check runs in the
database (the hash depends on how it renders JSON) and only over a window of events, so it stays
quick on a large trail and a full verification is a series of windows rather than one long scan.
"""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_WINDOW = 5000
MAX_WINDOW = 50000
NOTE = (
    "Record the head hash with an external system (for example the ERP or a ledger) to detect "
    "wholesale replacement of the trail."
)

WINDOW_SQL = text("""
    WITH bounds AS (
        SELECT (SELECT max(seq) FROM audit_events WHERE seq < :from_seq) AS lo,
               (SELECT max(seq) FROM (SELECT seq FROM audit_events WHERE seq >= :from_seq
                                      ORDER BY seq LIMIT :size) t) AS hi
    ), w AS (
        SELECT a.seq, a.hash, a.prev_hash,
               lag(a.hash) OVER (ORDER BY a.seq) AS exp_prev,
               audit_event_hash(lag(a.hash) OVER (ORDER BY a.seq), a) AS exp_hash
        FROM audit_events a, bounds b
        WHERE a.seq >= coalesce(b.lo, :from_seq) AND a.seq <= coalesce(b.hi, :from_seq - 1)
    )
    SELECT count(*) FILTER (WHERE seq >= :from_seq) AS checked,
           count(*) FILTER (WHERE seq >= :from_seq AND
                (hash IS DISTINCT FROM exp_hash OR prev_hash IS DISTINCT FROM exp_prev)) AS bad,
           min(seq) FILTER (WHERE seq >= :from_seq AND
                (hash IS DISTINCT FROM exp_hash OR prev_hash IS DISTINCT FROM exp_prev)) AS first_bad,
           max(seq) AS last_seq
    FROM w
""")
HEAD_SQL = text("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1")
FIRST_SQL = text("SELECT min(seq) FROM audit_events")
LATEST_START_SQL = text(
    "SELECT min(seq) FROM (SELECT seq FROM audit_events ORDER BY seq DESC LIMIT :size) t"
)


async def verify_window(db: AsyncSession, from_seq: int | None, size: int) -> dict:
    """Verify ``size`` events from ``from_seq`` (the newest ``size`` when it is not given)."""
    if db.get_bind().dialect.name != "postgresql":
        return {
            "supported": False,
            "reason": "The audit hash chain is computed by PostgreSQL, so it can only be verified there.",
        }
    head = (await db.execute(HEAD_SQL)).first()
    first_seq = (await db.execute(FIRST_SQL)).scalar()
    base = {
        "supported": True,
        "head_seq": None,
        "head_hash": None,
        "first_seq": first_seq,
        "note": NOTE,
    }
    if head is None:
        return {
            **base,
            "from_seq": 0,
            "checked": 0,
            "intact": True,
            "broken_events": 0,
            "first_broken_seq": None,
            "next_seq": None,
            "done": True,
            "verified_at": datetime.now(UTC),
        }
    head_seq, head_hash = head
    if from_seq is None:
        from_seq = (await db.execute(LATEST_START_SQL, {"size": size})).scalar() or 0
    checked, bad, first_bad, last_seq = (
        await db.execute(WINDOW_SQL, {"from_seq": from_seq, "size": size})
    ).one()
    done = last_seq is None or last_seq >= head_seq
    return {
        **base,
        "head_seq": head_seq,
        "head_hash": head_hash,
        "from_seq": from_seq,
        "checked": checked,
        "intact": bad == 0,
        "broken_events": bad,
        "first_broken_seq": first_bad,
        "next_seq": None if done else last_seq + 1,
        "done": done,
        "verified_at": datetime.now(UTC),
    }

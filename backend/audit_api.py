"""Audit trail: search, integrity verification and export. The trail itself is append-only and hash-chained in the database."""
import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
from models import AuditEvent

router = APIRouter(prefix="/api/audit")

VERIFY_SQL = text("""
    WITH chain AS (
        SELECT seq, hash, prev_hash, lag(hash) OVER (ORDER BY seq) AS exp_prev,
               audit_event_hash(lag(hash) OVER (ORDER BY seq), a) AS exp_hash
        FROM audit_events a)
    SELECT count(*) AS checked,
           count(*) FILTER (WHERE hash IS DISTINCT FROM exp_hash OR prev_hash IS DISTINCT FROM exp_prev) AS bad,
           min(seq) FILTER (WHERE hash IS DISTINCT FROM exp_hash OR prev_hash IS DISTINCT FROM exp_prev) AS first_bad,
           (SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1) AS head,
           (SELECT max(seq) FROM audit_events) AS head_seq
    FROM chain
""")


def event(e: AuditEvent) -> dict:
    return {"seq": e.seq, "id": e.id, "created_at": e.created_at, "event_type": e.event_type, "actor": e.actor,
            "object_type": e.object_type, "object_id": e.object_id, "case_id": e.case_id,
            "description": e.action_description, "before": e.before_state, "after": e.after_state,
            "prev_hash": e.prev_hash, "hash": e.hash}


def conditions(event_type, actor, case_id, date_from, date_to, q):
    conds = []
    if event_type: conds.append(AuditEvent.event_type.in_([x.strip() for x in event_type.split(",")]))
    if actor: conds.append(AuditEvent.actor.ilike(f"%{actor}%"))
    if case_id: conds.append(AuditEvent.case_id == case_id)
    if date_from: conds.append(AuditEvent.created_at >= datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc))
    if date_to: conds.append(AuditEvent.created_at < datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc).replace(hour=23, minute=59, second=59))
    if q: conds.append(AuditEvent.action_description.ilike(f"%{q}%"))
    return conds


@router.get("/page")
async def page(event_type: str | None = None, actor: str | None = None, case_id: str | None = None,
               date_from: str | None = None, date_to: str | None = None, q: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    conds = conditions(event_type, actor, case_id, date_from, date_to, q)
    total = (await db.execute(select(func.count()).select_from(AuditEvent).where(*conds))).scalar() or 0
    rows = (await db.execute(select(AuditEvent).where(*conds).order_by(AuditEvent.seq.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    types = (await db.execute(select(AuditEvent.event_type).distinct().order_by(AuditEvent.event_type))).scalars().all()
    return {"items": [event(e) for e in rows], "total": total, "page": page,
            "pages": max(1, -(-total // page_size)), "event_types": types}


@router.get("/verify")
async def verify(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    checked, bad, first_bad, head, head_seq = (await db.execute(VERIFY_SQL)).one()
    return {"checked": checked, "intact": bad == 0, "broken_events": bad, "first_broken_seq": first_bad,
            "head_hash": head, "head_seq": head_seq, "verified_at": datetime.now(timezone.utc),
            "note": "Record the head hash with an external system (for example the ERP or a ledger) to detect wholesale replacement of the trail."}


@router.get("/export")
async def export(format: str = "csv", event_type: str | None = None, actor: str | None = None,
                 case_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
                 q: str | None = None, db: AsyncSession = Depends(get_db),
                 user: dict = Depends(require_role("finance", "it"))):
    conds = conditions(event_type, actor, case_id, date_from, date_to, q)
    rows = (await db.execute(select(AuditEvent).where(*conds).order_by(AuditEvent.seq).limit(100000))).scalars().all()
    head = (await db.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))).scalar()
    data = [event(e) for e in rows]
    if format == "json":
        body = json.dumps({"exported_at": datetime.now(timezone.utc), "head_hash": head, "count": len(data), "events": data},
                          default=str, indent=1)
        return Response(body, media_type="application/json", headers={"Content-Disposition": "attachment; filename=audit-trail.json"})
    buf = io.StringIO()
    cols = ["seq", "created_at", "event_type", "actor", "object_type", "object_id", "case_id", "description", "prev_hash", "hash"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in data:
        w.writerow([r[c] for c in cols])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit-trail.csv"})

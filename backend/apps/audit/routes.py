"""Audit trail: search, integrity verification and export. The trail itself is append-only and hash-chained in the database."""
import csv
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security.authentication import require_role
from apps.search import contains_literal
from database import get_db
from apps.audit.models import AuditEvent
from apps.audit.schemas import AuditEventSchema

router = APIRouter(prefix="/api/audit")
MAX_EXPORT_EVENTS = 100000
EXPORT_PAGE_SIZE = 500
MAX_EVENT_TYPE_FILTERS = 50

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


async def export_event_pages(db: AsyncSession, conds: list, head_seq: int):
    """Fetch keyset pages and release the database connection between client writes."""
    last_seq = 0
    while True:
        rows = (await db.execute(
            select(AuditEvent).where(*conds, AuditEvent.seq > last_seq, AuditEvent.seq <= head_seq)
            .order_by(AuditEvent.seq).limit(EXPORT_PAGE_SIZE)
        )).scalars().all()
        records = [event(row) for row in rows]
        if rows:
            last_seq = rows[-1].seq
        await db.commit()
        if not records:
            return
        yield records


async def prepare_export(db: AsyncSession, conds: list, format: str) -> tuple[int, str | None, int]:
    """Validate format and pin the bounded export to the current audit-chain head."""
    if format not in {"csv", "json"}:
        raise HTTPException(status_code=422, detail="format must be csv or json")
    head_event = (
        await db.execute(
            select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
        )
    ).first()
    head_seq, head_hash = head_event if head_event else (0, None)
    count = (
        await db.execute(
            select(func.count()).select_from(AuditEvent).where(*conds, AuditEvent.seq <= head_seq)
        )
    ).scalar() or 0
    if count > MAX_EXPORT_EVENTS:
        raise HTTPException(
            status_code=413,
            detail=f"Export exceeds {MAX_EXPORT_EVENTS} events; narrow the filters",
        )
    await db.commit()
    return head_seq, head_hash, count


def csv_line(values: list) -> str:
    """Encode a row with the CSV writer's quoting and escaping rules."""
    buffer = io.StringIO()
    csv.writer(buffer).writerow(values)
    return buffer.getvalue()


async def generate_csv(db: AsyncSession, conds: list, head_seq: int):
    """Stream a stable audit export as CSV pages."""
    columns = [
        "seq", "created_at", "event_type", "actor", "object_type", "object_id",
        "case_id", "description", "prev_hash", "hash",
    ]
    yield csv_line(columns)
    async for records in export_event_pages(db, conds, head_seq):
        for record in records:
            yield csv_line([record[column] for column in columns])


async def generate_json(db: AsyncSession, conds: list, head_seq: int, head_hash: str | None, count: int):
    """Stream JSON metadata and paginated audit records without buffering the full export."""
    exported_at = datetime.now(timezone.utc)
    yield json.dumps({"exported_at": exported_at, "head_hash": head_hash, "count": count}, default=str)[:-1]
    yield ',"events":['
    first = True
    async for records in export_event_pages(db, conds, head_seq):
        for record in records:
            if not first:
                yield ","
            yield json.dumps(record, default=str)
            first = False
    yield "]}"


def export_response(
    db: AsyncSession, conds: list, format: str, head_seq: int, head_hash: str | None, count: int
) -> StreamingResponse:
    """Build a streaming response for the requested audit export format."""
    is_json = format == "json"
    generator = (
        generate_json(db, conds, head_seq, head_hash, count)
        if is_json
        else generate_csv(db, conds, head_seq)
    )
    filename = "audit-trail.json" if is_json else "audit-trail.csv"
    media_type = "application/json" if is_json else "text/csv"
    return StreamingResponse(
        generator,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("", response_model=list[AuditEventSchema])
async def list_recent_events(
    case_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    """Compatibility endpoint for the dashboard's latest-event view."""
    query = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit)
    if case_id:
        query = query.where(AuditEvent.case_id == case_id)
    rows = (await db.execute(query)).scalars().all()
    return rows


def conditions(event_type, actor, case_id, date_from, date_to, q):
    conds = []
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if event_type:
        event_types = list(dict.fromkeys(value.strip() for value in event_type.split(",")))
        if len(event_types) > MAX_EVENT_TYPE_FILTERS or any(not value or len(value) > 100 for value in event_types):
            raise HTTPException(status_code=422, detail="event_type must contain at most 50 non-empty values of 100 characters or fewer")
        conds.append(AuditEvent.event_type.in_(event_types))
    if actor: conds.append(contains_literal(AuditEvent.actor, actor))
    if case_id: conds.append(AuditEvent.case_id == case_id)
    if date_from:
        conds.append(AuditEvent.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        if date_to == date.max:
            conds.append(AuditEvent.created_at <= datetime.max.replace(tzinfo=timezone.utc))
        else:
            conds.append(AuditEvent.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    if q: conds.append(contains_literal(AuditEvent.action_description, q))
    return conds


@router.get("/page")
async def page(event_type: Annotated[str | None, Query(max_length=5100)] = None,
               actor: Annotated[str | None, Query(max_length=255)] = None,
               case_id: Annotated[str | None, Query(max_length=36)] = None,
               date_from: date | None = None, date_to: date | None = None,
               q: Annotated[str | None, Query(max_length=500)] = None,
               page: int = Query(1, ge=1, le=10000), page_size: int = Query(50, ge=1, le=200),
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
async def export(format: str = "csv", event_type: Annotated[str | None, Query(max_length=5100)] = None,
                 actor: Annotated[str | None, Query(max_length=255)] = None,
                 case_id: Annotated[str | None, Query(max_length=36)] = None,
                 date_from: date | None = None, date_to: date | None = None,
                 q: Annotated[str | None, Query(max_length=500)] = None, db: AsyncSession = Depends(get_db),
                 user: dict = Depends(require_role("finance", "it"))):
    conds = conditions(event_type, actor, case_id, date_from, date_to, q)
    head_seq, head_hash, count = await prepare_export(db, conds, format)
    return export_response(db, conds, format, head_seq, head_hash, count)

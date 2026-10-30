"""Downstream export: destinations, incremental idempotent batches, signed delivery, acknowledgment tracking."""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
from models import (AuditEvent, Case, ExportBatch, ExportDestination, ExportItem, HumanDecision, Recommendation,
                    ValidationObligation, utcnow)
from nimbus import audit

router = APIRouter(prefix="/api/exports")
EXPORT_DIR = Path(os.getenv("NIMBUS_EXPORT_DIR", "/app/exports"))
MAX_BATCH = 5000
DATASETS = {
    "resolutions": "Closed, validated cases with decision, disposition and validation result",
    "adjustments": "Approved financial adjustments from validated cases (journal lines for ERP/GL)",
    "audit": "Audit trail events since the last delivery (with hashes)",
}
SINK_SECRET = os.getenv("NIMBUS_SINK_SECRET", "demo-secret")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def dest_view(d: ExportDestination) -> dict:
    cfg = dict(d.config or {})
    if "secret" in cfg:
        cfg["secret"] = "••••••" if cfg["secret"] else ""
    return {"id": d.id, "name": d.name, "kind": d.kind, "config": cfg, "format": d.format, "enabled": d.enabled}


def batch_view(b: ExportBatch, dest_name: str | None = None) -> dict:
    return {"id": b.id, "destination_id": b.destination_id, "destination": dest_name, "dataset": b.dataset,
            "status": b.status, "record_count": b.record_count, "payload_hash": b.payload_hash,
            "audit_head_hash": b.audit_head_hash, "created_by": b.created_by, "created_at": b.created_at,
            "sent_at": b.sent_at, "acked_at": b.acked_at, "response_code": b.response_code,
            "response_ref": b.response_ref, "error": b.error, "retries": b.retries,
            "range_from": b.range_from, "range_to": b.range_to, "backposted_count": b.backposted_count or 0}


async def head_hash(db) -> str | None:
    return (await db.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))).scalar()


async def exported_ids(db, dest_id: str, dataset: str) -> set[str]:
    rows = await db.execute(select(ExportItem.object_id).where(ExportItem.destination_id == dest_id, ExportItem.dataset == dataset))
    return set(rows.scalars().all())


SCOPES = ("all", "current", "backposts")


def classify_backpost(store_id: str, business_date: str, closed_date: str, prior: dict) -> tuple[bool, str | None, list[str]]:
    """Is this record a back-post, and why?

    A back-post belongs to a business day that has already ended or already been exported to this destination, so
    the receiver must post it to that original day or period and not to the day it arrived.
    """
    batches = prior.get((store_id, business_date), [])
    if batches:
        return True, "Late addition to a business day already exported", batches
    if closed_date > business_date:
        return True, "Resolved after the business date ended", []
    return False, None, []


async def prior_exported_days(db, dest_id: str, dataset: str) -> dict:
    """(store_id, business_date) -> short ids of earlier delivered batches that already covered that day."""
    rows = (await db.execute(
        select(Case.store_id, Case.business_date, ExportBatch.id)
        .select_from(ExportItem)
        .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .join(Case, Case.id == ExportItem.object_id)
        .where(ExportItem.destination_id == dest_id, ExportItem.dataset == dataset,
               ExportItem.state == "Delivered", ExportBatch.status.in_(["Sent", "Acknowledged"])).distinct())).all()
    out: dict = {}
    for store_id, business_date, batch_id in rows:
        out.setdefault((store_id, business_date), []).append(batch_id[:8])
    return {k: sorted(v) for k, v in out.items()}


def summarize(records: list[dict]) -> dict:
    back = [r for r in records if r.get("backpost")]
    return {"current": len(records) - len(back), "backposted": len(back),
            "business_dates": sorted({r["business_date"] for r in back if r.get("business_date")})}


def apply_scope(records: list[dict], items: list, scope: str) -> tuple[list[dict], list]:
    if scope == "all" or not records or not items:
        return records, items
    keep = [i for i, r in enumerate(records) if bool(r.get("backpost")) == (scope == "backposts")]
    return [records[i] for i in keep], [items[i] for i in keep]


async def mark_delivered(db, batch_id: str) -> None:
    await db.execute(update(ExportItem).where(ExportItem.batch_id == batch_id, ExportItem.state == "Pending")
                     .values(state="Delivered", delivered_at=utcnow()))


async def closed_case_context(db, cases: list[Case]) -> dict:
    ids = [c.id for c in cases]
    recs = {r.case_id: r for r in (await db.execute(select(Recommendation).where(
        Recommendation.case_id.in_(ids), Recommendation.status == "Approved"))).scalars().all()}
    decs = {d.case_id: d for d in (await db.execute(select(HumanDecision).where(
        HumanDecision.case_id.in_(ids), HumanDecision.decision == "Approved"))).scalars().all()}
    vals = {v.case_id: v for v in (await db.execute(select(ValidationObligation).where(
        ValidationObligation.case_id.in_(ids), ValidationObligation.status == "Complete"))).scalars().all()}
    refs = dict((await db.execute(
        select(AuditEvent.case_id, AuditEvent.hash).where(AuditEvent.case_id.in_(ids)).order_by(AuditEvent.seq))).all())
    return {"recs": recs, "decs": decs, "vals": vals, "refs": refs}


async def build(db, dest: ExportDestination, dataset: str) -> tuple[list[dict], list[tuple[str, str]], int | None, int | None]:
    """Returns (records, [(object_id, version)], range_from, range_to)."""
    if dataset == "audit":
        last = (await db.execute(select(func.max(ExportBatch.range_to)).where(
            ExportBatch.destination_id == dest.id, ExportBatch.dataset == "audit",
            ExportBatch.status.in_(["Sent", "Acknowledged"])))).scalar() or 0
        rows = (await db.execute(select(AuditEvent).where(AuditEvent.seq > last).order_by(AuditEvent.seq).limit(MAX_BATCH))).scalars().all()
        recs = [{"seq": e.seq, "timestamp": e.created_at.isoformat(), "event_type": e.event_type, "actor": e.actor,
                 "object_type": e.object_type, "object_id": e.object_id, "case_id": e.case_id,
                 "description": e.action_description, "prev_hash": e.prev_hash, "hash": e.hash} for e in rows]
        return recs, [], (rows[0].seq if rows else None), (rows[-1].seq if rows else None)

    done = await exported_ids(db, dest.id, dataset)
    cases = (await db.execute(select(Case).where(Case.status == "Closed").order_by(Case.updated_at).limit(MAX_BATCH + len(done) if len(done) < 100000 else MAX_BATCH))).scalars().all()
    cases = [c for c in cases if c.id not in done][:MAX_BATCH]
    ctx = await closed_case_context(db, cases)
    prior = await prior_exported_days(db, dest.id, dataset)
    recs, items = [], []
    for c in cases:
        rec, dec, val = ctx["recs"].get(c.id), ctx["decs"].get(c.id), ctx["vals"].get(c.id)
        closed_date = c.updated_at.date().isoformat()
        is_back, why, prior_batches = classify_backpost(c.store_id, c.business_date, closed_date, prior)
        flags = {"backpost": is_back, "backpost_reason": why, "prior_batches": ";".join(prior_batches) or None}
        if dataset == "resolutions":
            recs.append({"case_number": c.case_number, "business_date": c.business_date, "store_id": c.store_id,
                         "case_type": c.case_type, "exception_amount": str(c.total_exception_amount),
                         "estimated_exposure": str(c.total_exposure), "currency": "USD",
                         "disposition": rec.disposition_type if rec else None, "action_class": rec.action_class if rec else None,
                         "decided_by": dec.actor if dec else None, "decided_at": dec.created_at.isoformat() if dec else None,
                         "validation_result": val.verification_result if val else None,
                         "closed_at": c.updated_at.isoformat(), "posting_date": closed_date,
                         "audit_reference": ctx["refs"].get(c.id), **flags})
        else:
            if not rec or not val or rec.financial_impact <= 0:
                continue
            recs.append({"journal_id": f"NIM-{c.case_number}", "posting_date": c.updated_at.date().isoformat(),
                         "business_date": c.business_date, "store_id": c.store_id, "currency": "USD",
                         "amount": str(rec.financial_impact), "adjustment_type": rec.disposition_type,
                         "reason": c.case_type, "reference": c.case_number, "approved_by": dec.actor if dec else None,
                         "validated": True, "audit_reference": ctx["refs"].get(c.id), **flags})
        items.append((c.id, "v1", {"record_ref": recs[-1].get("case_number") or recs[-1].get("journal_id"),
                                   "store_id": c.store_id, "business_date": c.business_date, "backpost": is_back}))
    return recs, items, None, None


def render(dest: ExportDestination, dataset: str, records: list[dict], head: str | None, key: str, summary: dict | None = None) -> bytes:
    if dest.format == "csv":
        buf = io.StringIO()
        if records:
            w = csv.DictWriter(buf, fieldnames=list(records[0].keys()))
            w.writeheader()
            w.writerows(records)
        return buf.getvalue().encode()
    return json.dumps({"schema_version": "1.0", "dataset": dataset, "destination": dest.name, "idempotency_key": key,
                       "generated_at": utcnow().isoformat(), "audit_head_hash": head, "count": len(records),
                       **({"summary": summary} if summary else {}), "records": records}, default=str, indent=1).encode()


async def deliver(dest: ExportDestination, b: ExportBatch) -> None:
    body = (b.payload or "").encode()
    cfg = dest.config or {}
    try:
        if dest.kind == "webhook":
            headers = {"Content-Type": "text/csv" if dest.format == "csv" else "application/json",
                       "X-Nimbus-Idempotency-Key": b.idempotency_key, "X-Nimbus-Dataset": b.dataset,
                       "X-Nimbus-Signature": sign(cfg.get("secret", ""), body)}
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(cfg["url"], content=body, headers=headers)
            b.response_code = r.status_code
            if r.status_code in (200, 201):
                b.status, b.acked_at = "Acknowledged", utcnow()
                try: b.response_ref = str(r.json().get("reference", ""))[:255] or None
                except Exception: pass
            elif r.status_code == 202:
                b.status = "Sent"
            else:
                b.status, b.error = "Failed", f"HTTP {r.status_code}: {r.text[:200]}"
        else:
            folder = EXPORT_DIR / re.sub(r"[^A-Za-z0-9_.-]", "_", dest.name)
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{b.dataset}-{utcnow().strftime('%Y%m%dT%H%M%S')}-{b.id[:6]}.{dest.format}"
            (folder / name).write_bytes(body)
            (folder / (name + ".sha256")).write_text(f"{b.payload_hash}  {name}\n")
            b.status, b.response_ref = "Sent", str(folder / name)
        if b.status in ("Sent", "Acknowledged"):
            b.sent_at, b.error = utcnow(), None
    except Exception as e:
        b.status, b.error = "Failed", f"{type(e).__name__}: {str(e)[:200]}"


class DestIn(BaseModel):
    name: str
    kind: str
    format: str = "json"
    url: str | None = None
    secret: str | None = None
    directory: str | None = None


@router.get("/datasets")
async def datasets(_: dict = Depends(require_role("finance", "it"))):
    return [{"key": k, "description": v} for k, v in DATASETS.items()]


async def ensure_defaults(db):
    if (await db.execute(select(func.count()).select_from(ExportDestination))).scalar():
        return
    db.add(ExportDestination(id=str(uuid.uuid4()), name="Test receiver (webhook)", kind="webhook", format="json",
                             config={"url": "http://localhost:8000/api/exports/_sink", "secret": SINK_SECRET}))
    db.add(ExportDestination(id=str(uuid.uuid4()), name="ERP/GL file drop", kind="file", format="csv", config={}))
    await db.commit()


@router.get("/destinations")
async def list_destinations(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    await ensure_defaults(db)
    return [dest_view(d) for d in (await db.execute(select(ExportDestination).order_by(ExportDestination.name))).scalars().all()]


@router.post("/destinations")
async def create_destination(body: DestIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    if body.kind not in ("webhook", "file") or body.format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="kind must be webhook|file and format json|csv")
    if body.kind == "webhook" and not (body.url and re.match(r"^https?://", body.url)):
        raise HTTPException(status_code=400, detail="A webhook needs an http(s) URL")
    cfg = {"url": body.url, "secret": body.secret or ""} if body.kind == "webhook" else {}
    d = ExportDestination(id=str(uuid.uuid4()), name=body.name.strip(), kind=body.kind, format=body.format, config=cfg)
    db.add(d)
    audit(db, "EXPORT_DESTINATION_CREATED", None, "ExportDestination", d.id, f"Destination '{d.name}' ({d.kind}) created",
          actor=user["user"] or "demo.user")
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A destination with that name exists")
    return dest_view(d)


@router.post("/destinations/{dest_id}/toggle")
async def toggle_destination(dest_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    d = (await db.execute(select(ExportDestination).where(ExportDestination.id == dest_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Destination not found")
    d.enabled = not d.enabled
    audit(db, "EXPORT_DESTINATION_TOGGLED", None, "ExportDestination", d.id, f"'{d.name}' {'enabled' if d.enabled else 'disabled'}",
          actor=user["user"] or "demo.user")
    await db.commit()
    return dest_view(d)


class RunIn(BaseModel):
    destination_id: str
    dataset: str
    dry_run: bool = False
    scope: str = "all"   # all | current | backposts. Back-posts are records for an earlier or already-exported business day.


@router.post("/run")
async def run_export(body: RunIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    if body.dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    dest = (await db.execute(select(ExportDestination).where(ExportDestination.id == body.destination_id))).scalar_one_or_none()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")
    if not dest.enabled:
        raise HTTPException(status_code=409, detail="Destination is disabled")
    actor = user["user"] or "demo.user"
    if body.scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {SCOPES}")
    records, items, r_from, r_to = await build(db, dest, body.dataset)
    everything = summarize(records)   # what is waiting, whichever scope is chosen
    records, items = apply_scope(records, items, body.scope)
    if body.dry_run:
        return {"dry_run": True, "count": len(records), "sample": records[:3], "capped_at": MAX_BATCH,
                "waiting": everything, "scope": body.scope}
    if not records:
        return {"dry_run": False, "count": 0, "message": "Nothing new to export." if body.scope == "all" else f"Nothing waiting in the '{body.scope}' scope."}
    summary = summarize(records)

    head = await head_hash(db)
    key = hashlib.sha256(json.dumps([dest.id, body.dataset, sorted(i[0] + i[1] for i in items), r_from, r_to]).encode()).hexdigest()
    existing = (await db.execute(select(ExportBatch).where(ExportBatch.idempotency_key == key))).scalar_one_or_none()
    if existing and existing.status in ("Sent", "Acknowledged", "Pending"):
        return {**batch_view(existing, dest.name), "message": "Already exported (idempotent)."}
    payload = render(dest, body.dataset, records, head, key, summary)
    b = existing or ExportBatch(id=str(uuid.uuid4()), destination_id=dest.id, dataset=body.dataset, idempotency_key=key,
                                created_by=actor, range_from=r_from, range_to=r_to)
    b.payload, b.payload_hash, b.record_count, b.audit_head_hash, b.status = payload.decode(), hashlib.sha256(payload).hexdigest(), len(records), head, "Pending"
    b.backposted_count = summary["backposted"]
    db.add(b)
    await db.flush()
    # Reserve every record for this batch before anything is sent: one record, one batch, no double delivery.
    try:
        for oid, ver, meta in items:
            db.add(ExportItem(id=str(uuid.uuid4()), batch_id=b.id, destination_id=dest.id, dataset=body.dataset, object_id=oid,
                              object_version=ver, state="Pending", **meta))
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Some of these records already belong to another batch. Refresh and try again.")
    audit(db, "EXPORT_CREATED", None, "ExportBatch", b.id, f"Export of {len(records)} {body.dataset} record(s) to '{dest.name}' created"
          + (f" ({summary['backposted']} back-posted)" if summary["backposted"] else ""),
          actor=actor, after={"payload_hash": b.payload_hash, "idempotency_key": key, "scope": body.scope, **summary})
    await db.commit()

    await deliver(dest, b)
    if b.status in ("Sent", "Acknowledged"):
        await mark_delivered(db, b.id)
    audit(db, {"Acknowledged": "EXPORT_ACKNOWLEDGED", "Sent": "EXPORT_SENT"}.get(b.status, "EXPORT_FAILED"), None, "ExportBatch", b.id,
          f"Export to '{dest.name}' {b.status.lower()}" + (f": {b.error}" if b.error else ""), actor="export-service")
    await db.commit()
    return batch_view(b, dest.name)


@router.get("/batches")
async def batches(limit: int = 50, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    rows = (await db.execute(select(ExportBatch, ExportDestination.name).join(
        ExportDestination, ExportDestination.id == ExportBatch.destination_id).order_by(ExportBatch.created_at.desc()).limit(min(limit, 200)))).all()
    return [batch_view(b, n) for b, n in rows]


@router.get("/batches/{batch_id}/payload")
async def payload(batch_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    from fastapi.responses import Response
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not b or b.payload is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    d = (await db.execute(select(ExportDestination).where(ExportDestination.id == b.destination_id))).scalar_one()
    return Response(b.payload, media_type="text/csv" if d.format == "csv" else "application/json",
                    headers={"Content-Disposition": f"attachment; filename={b.dataset}-{b.id[:8]}.{d.format}"})


@router.post("/batches/{batch_id}/retry")
async def retry(batch_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Failed":
        raise HTTPException(status_code=409, detail=f"Batch is {b.status}")
    dest = (await db.execute(select(ExportDestination).where(ExportDestination.id == b.destination_id))).scalar_one()
    b.retries += 1
    await deliver(dest, b)
    if b.status in ("Sent", "Acknowledged"):
        await mark_delivered(db, b.id)   # a successful retry must record what it delivered
    audit(db, "EXPORT_RETRIED", None, "ExportBatch", b.id, f"Retry {b.retries} to '{dest.name}': {b.status.lower()}",
          actor=user["user"] or "demo.user")
    await db.commit()
    return batch_view(b, dest.name)


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    """Abandon a failed batch and release its records so the next export picks them up again."""
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Failed":
        raise HTTPException(status_code=409, detail=f"Only failed batches can be cancelled (this one is {b.status})")
    released = (await db.execute(delete(ExportItem).where(ExportItem.batch_id == b.id, ExportItem.state == "Pending"))).rowcount or 0
    b.status = "Cancelled"
    # Free the idempotency key so an identical set of records can be exported again as a new batch.
    b.idempotency_key = hashlib.sha256((b.idempotency_key + ":cancelled:" + b.id).encode()).hexdigest()
    audit(db, "EXPORT_CANCELLED", None, "ExportBatch", b.id, f"Failed export cancelled; {released} record(s) released for re-export",
          actor=user["user"] or "demo.user", after={"released": released})
    await db.commit()
    return {**batch_view(b), "released": released}


# ---- Tracking: which records were exported, which are waiting, which are not eligible and why -----------------
TRACK_STATES = ("delivered", "in_batch", "waiting", "not_eligible")


async def ledger(db, dest_id: str, dataset: str) -> list[dict]:
    """One row per case for a destination and dataset, with its export state and the reason when it is not eligible."""
    cases = (await db.execute(select(Case.id, Case.case_number, Case.store_id, Case.business_date, Case.status, Case.updated_at))).all()
    items = {i.object_id: (i, bs, bid) for i, bs, bid in (await db.execute(
        select(ExportItem, ExportBatch.status, ExportBatch.id).join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .where(ExportItem.destination_id == dest_id, ExportItem.dataset == dataset))).all()}
    approved = {r.case_id: r.financial_impact for r in (await db.execute(select(Recommendation.case_id, Recommendation.financial_impact).where(
        Recommendation.status == "Approved"))).all()} if dataset == "adjustments" else {}
    validated = set((await db.execute(select(ValidationObligation.case_id).where(ValidationObligation.status == "Complete"))).scalars().all()) if dataset == "adjustments" else set()
    prior = await prior_exported_days(db, dest_id, dataset)
    rows = []
    for cid, number, store, bdate, status, updated in cases:
        ref = number if dataset == "resolutions" else f"NIM-{number}"
        row = {"object_id": cid, "record_ref": ref, "case_number": number, "store_id": store, "business_date": bdate, "case_status": status,
               "state": "", "reason": None, "batch_id": None, "batch_status": None, "delivered_at": None, "backpost": False, "backpost_reason": None}
        if cid in items:
            item, bstatus, bid = items[cid]
            row.update(record_ref=item.record_ref or ref, batch_id=bid, batch_status=bstatus, backpost=bool(item.backpost), delivered_at=item.delivered_at)
            if item.state == "Delivered":
                row["state"] = "delivered"
            else:
                row["state"], row["reason"] = "in_batch", ("The batch failed. Retry it, or cancel it to release these records." if bstatus == "Failed" else "Batch is being delivered.")
        elif status != "Closed":
            row["state"], row["reason"] = "not_eligible", f"Case is {status}; only closed cases are exported."
        elif dataset == "adjustments" and cid not in approved:
            row["state"], row["reason"] = "not_eligible", "No approved recommendation, so there is no adjustment to post."
        elif dataset == "adjustments" and (approved[cid] or 0) <= 0:
            row["state"], row["reason"] = "not_eligible", "Approved with no financial impact, so there is no journal line."
        elif dataset == "adjustments" and cid not in validated:
            row["state"], row["reason"] = "not_eligible", "Validation is not complete yet."
        else:
            is_back, why, _prior = classify_backpost(store, bdate, updated.date().isoformat(), prior)
            row.update(state="waiting", backpost=is_back, backpost_reason=why)
        rows.append(row)
    return rows


def ledger_summary(rows: list[dict]) -> dict:
    out = {s: 0 for s in TRACK_STATES}
    out["waiting_backposts"] = 0
    for r in rows:
        out[r["state"]] += 1
        if r["state"] == "waiting" and r["backpost"]:
            out["waiting_backposts"] += 1
    return out


@router.get("/tracking")
async def tracking(destination_id: str, dataset: str, state: str = "", q: str = "", page: int = 1, page_size: int = 50,
                   db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    if dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    if state and state not in TRACK_STATES:
        raise HTTPException(status_code=400, detail=f"state must be one of {TRACK_STATES}")
    if not (await db.execute(select(ExportDestination.id).where(ExportDestination.id == destination_id))).scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Destination not found")
    if dataset == "audit":
        # The audit trail is exported as a sequence, so coverage is a position rather than a record list.
        last = (await db.execute(select(func.max(ExportBatch.range_to)).where(
            ExportBatch.destination_id == destination_id, ExportBatch.dataset == "audit",
            ExportBatch.status.in_(["Sent", "Acknowledged"])))).scalar() or 0
        head = (await db.execute(select(func.max(AuditEvent.seq)))).scalar() or 0
        waiting = (await db.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.seq > last))).scalar() or 0
        return {"kind": "stream", "summary": {"last_exported_seq": last, "head_seq": head, "waiting_events": waiting},
                "items": [], "total": 0, "page": 1, "pages": 1}
    rows = await ledger(db, destination_id, dataset)
    summary = ledger_summary(rows)
    order = {"in_batch": 0, "waiting": 1, "not_eligible": 2, "delivered": 3}
    needle = q.strip().lower()
    shown = [r for r in rows if (not state or r["state"] == state)
             and (not needle or needle in f"{r['record_ref']} {r['case_number']} {r['store_id']} {r['business_date']}".lower())]
    shown.sort(key=lambda r: (order[r["state"]], r["business_date"] or "", r["record_ref"] or ""), reverse=False)
    page_size = max(1, min(page_size, 200)); page = max(1, page)
    total = len(shown)
    return {"kind": "records", "summary": summary, "items": shown[(page - 1) * page_size: page * page_size],
            "total": total, "page": page, "pages": max(1, -(-total // page_size))}


class AckIn(BaseModel):
    reference: str | None = None


@router.post("/batches/{batch_id}/ack")
async def acknowledge(batch_id: str, body: AckIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    """Record the downstream system's acknowledgment (for file drops and asynchronous receivers)."""
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Sent":
        raise HTTPException(status_code=409, detail=f"Only 'Sent' batches can be acknowledged (this one is {b.status})")
    b.status, b.acked_at = "Acknowledged", utcnow()
    if body.reference:
        b.response_ref = body.reference[:255]
    audit(db, "EXPORT_ACKNOWLEDGED", None, "ExportBatch", b.id, f"Downstream acknowledged export (ref {body.reference or 'n/a'})",
          actor=user["user"] or "demo.user")
    await db.commit()
    return batch_view(b)


@router.post("/_sink")
async def sink(request: Request, x_nimbus_signature: str | None = Header(default=None),
               x_nimbus_idempotency_key: str | None = Header(default=None)):
    """Built-in test receiver: verifies the HMAC signature and acknowledges. Not for production use."""
    body = await request.body()
    if not x_nimbus_signature or not hmac.compare_digest(x_nimbus_signature, sign(SINK_SECRET, body)):
        raise HTTPException(status_code=401, detail="Bad signature")
    return {"status": "acknowledged", "reference": f"SINK-{(x_nimbus_idempotency_key or 'none')[:10]}", "bytes": len(body)}

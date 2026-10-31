"""Downstream export: destinations, incremental idempotent batches, signed delivery, acknowledgment tracking."""
import csv
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
import uuid
from datetime import timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, case as sql_case, cast, delete, func, insert, literal, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from auth import require_role
from apps.request_body import read_limited_body
from database import get_db
from models import (AuditEvent, Case, ExportBatch, ExportDestination, ExportItem, HumanDecision, Recommendation, TestSinkReceipt,
                    ValidationObligation, Workflow, utcnow)
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/exports")
EXPORT_DIR = Path(os.getenv("NIMBUS_EXPORT_DIR", "/app/exports"))
MAX_BATCH = 5000
MAX_SINK_BODY_BYTES = 25 * 1024 * 1024
DATASETS = {
    "resolutions": "Closed, validated cases with decision, disposition and validation result",
    "adjustments": "Approved financial adjustments from validated cases (journal lines for ERP/GL)",
    "audit": "Audit trail events since the last delivery (with hashes)",
}
def test_sink_enabled() -> bool:
    environment = os.getenv("ENVIRONMENT", "production").strip().lower()
    explicit_setting = os.getenv("NIMBUS_ENABLE_TEST_SINK", "true").strip().lower()
    return environment in {"development", "test"} and explicit_setting in {"1", "true", "yes", "on"}


def sink_secret() -> str:
    configured = os.getenv("NIMBUS_SINK_SECRET")
    if configured:
        return configured
    return "demo-secret" if test_sink_enabled() else ""


SINK_SECRET = sink_secret()


async def require_test_sink_enabled() -> None:
    if not test_sink_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def sign_envelope(secret: str, dataset: str, idempotency_key: str, body: bytes) -> str:
    """Sign canonical delivery metadata and the payload digest (protocol v1)."""
    message = f"nimbus-export-v1\n{dataset}\n{idempotency_key}\n{hashlib.sha256(body).hexdigest()}".encode("ascii")
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def valid_signature(actual: str | None, expected: str) -> bool:
    return bool(actual and re.fullmatch(r"sha256=[0-9a-f]{64}", actual) and hmac.compare_digest(actual, expected))


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
            "range_from": b.range_from, "range_to": b.range_to, "backposted_count": b.backposted_count or 0,
            "attempt_started_at": b.attempt_started_at}


async def head_hash(db) -> str | None:
    return (await db.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))).scalar()


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


async def prior_exported_days(db, dest_id: str, dataset: str, cases: list[Case] | None = None) -> dict:
    """(store_id, business_date) -> short ids of earlier delivered batches that already covered that day."""
    query = (
        select(Case.store_id, Case.business_date, ExportBatch.id)
        .select_from(ExportItem)
        .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
        .join(Case, Case.id == ExportItem.object_id)
        .where(ExportItem.destination_id == dest_id, ExportItem.dataset == dataset,
               ExportItem.state == "Delivered", ExportBatch.status.in_(["Sent", "Acknowledged"])).distinct())
    keys = sorted({(case.store_id, case.business_date) for case in cases}) if cases is not None else None
    rows = []
    if keys is None:
        rows = (await db.execute(query)).all()
    else:
        for offset in range(0, len(keys), 300):
            rows.extend((await db.execute(query.where(tuple_(Case.store_id, Case.business_date).in_(keys[offset:offset + 300])))).all())
    out: dict = {}
    for store_id, business_date, batch_id in rows:
        out.setdefault((store_id, business_date), []).append(batch_id[:8])
    return {k: sorted(v) for k, v in out.items()}


def summarize(records: list[dict]) -> dict:
    back = [r for r in records if r.get("backpost")]
    return {"current": len(records) - len(back), "backposted": len(back),
            "business_dates": sorted({r["business_date"] for r in back if r.get("business_date")})}


async def mark_delivered(db, batch_id: str) -> None:
    await db.execute(update(ExportItem).where(ExportItem.batch_id == batch_id, ExportItem.state == "Pending")
                     .values(state="Delivered", delivered_at=utcnow()))


def verified_adjustment_evidence():
    """Return matching approvals and verified workflows for the same recommendation and case."""
    return (select(Recommendation, HumanDecision, ValidationObligation)
            .join(HumanDecision, (HumanDecision.recommendation_id == Recommendation.id)
                  & (HumanDecision.case_id == Recommendation.case_id))
            .join(Workflow, (Workflow.recommendation_id == Recommendation.id)
                  & (Workflow.case_id == Recommendation.case_id))
            .join(ValidationObligation, (ValidationObligation.workflow_id == Workflow.id)
                  & (ValidationObligation.case_id == Recommendation.case_id))
            .where(Recommendation.status == "Approved", Recommendation.financial_impact > 0,
                   HumanDecision.decision == "Approved", Workflow.state == "Completed",
                   ValidationObligation.status == "Complete", ValidationObligation.verification_result == "Verified"))


async def closed_case_context(db, cases: list[Case], positive_recommendations: bool = False) -> dict:
    ids = [c.id for c in cases]
    if positive_recommendations:
        evidence = (await db.execute(verified_adjustment_evidence().where(Recommendation.case_id.in_(ids)).order_by(
            Recommendation.created_at, Recommendation.id, HumanDecision.created_at, HumanDecision.id,
            ValidationObligation.created_at, ValidationObligation.id))).all()
        recs = {r.case_id: r for r, _, _ in evidence}
        decs = {r.case_id: d for r, d, _ in evidence}
        vals = {r.case_id: v for r, _, v in evidence}
    else:
        recs = {r.case_id: r for r in (await db.execute(select(Recommendation).where(
            Recommendation.case_id.in_(ids), Recommendation.status == "Approved").order_by(
            Recommendation.created_at, Recommendation.id))).scalars().all()}
        decs = {d.case_id: d for d in (await db.execute(select(HumanDecision).where(
            HumanDecision.case_id.in_(ids), HumanDecision.decision == "Approved"))).scalars().all()}
        vals = {v.case_id: v for v in (await db.execute(select(ValidationObligation).where(
            ValidationObligation.case_id.in_(ids), ValidationObligation.status == "Complete"))).scalars().all()}
    refs = dict((await db.execute(
        select(AuditEvent.case_id, AuditEvent.hash).where(AuditEvent.case_id.in_(ids)).order_by(AuditEvent.seq))).all())
    return {"recs": recs, "decs": decs, "vals": vals, "refs": refs}


async def build(db, dest: ExportDestination, dataset: str, scope: str = "all", *, lock_cases: bool = False) -> tuple[list[dict], list[tuple[str, str]], int | None, int | None]:
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

    reserved = select(ExportItem.id).where(
        ExportItem.destination_id == dest.id, ExportItem.dataset == dataset, ExportItem.object_id == Case.id
    ).exists()
    query = select(Case).where(Case.status == "Closed", Case.amount_needs_review.is_(False), ~reserved)
    if scope != "all":
        exported_case = aliased(Case)
        previously_exported = (select(ExportItem.id).select_from(ExportItem)
            .join(ExportBatch, ExportBatch.id == ExportItem.batch_id)
            .join(exported_case, exported_case.id == ExportItem.object_id)
            .where(ExportItem.destination_id == dest.id, ExportItem.dataset == dataset,
                   ExportItem.state == "Delivered", ExportBatch.status.in_(["Sent", "Acknowledged"]),
                   exported_case.store_id == Case.store_id, exported_case.business_date == Case.business_date)
            .exists())
        backpost = or_(cast(func.date(Case.updated_at), String) > Case.business_date, previously_exported)
        query = query.where(backpost if scope == "backposts" else ~backpost)
    if dataset == "adjustments":
        verified = verified_adjustment_evidence().where(Recommendation.case_id == Case.id).exists()
        query = query.where(verified)
    if lock_cases:
        # Hold selected case rows until their export reservations are committed.
        # Skipping locked rows avoids blocking or deadlocking correction workflows.
        query = query.with_for_update(of=Case, skip_locked=True).execution_options(populate_existing=True)
    cases = (await db.execute(query.order_by(Case.updated_at, Case.id).limit(MAX_BATCH))).scalars().all()
    ctx = await closed_case_context(db, cases, positive_recommendations=dataset == "adjustments")
    prior = await prior_exported_days(db, dest.id, dataset, cases)
    recs, items = [], []
    for c in cases:
        rec, dec, val = ctx["recs"].get(c.id), ctx["decs"].get(c.id), ctx["vals"].get(c.id)
        closed_date = c.updated_at.date().isoformat()
        is_back, why, prior_batches = classify_backpost(c.store_id, c.business_date, closed_date, prior)
        flags = {"backpost": is_back, "backpost_reason": why, "prior_batches": ";".join(prior_batches) or None}
        if dataset == "resolutions":
            recs.append({"case_number": c.case_number, "business_date": c.business_date, "store_id": c.store_id,
                         "case_type": c.case_type, "exception_amount": str(c.total_exception_amount),
                         "estimated_exposure": str(c.total_exposure), "currency": c.currency,
                         "disposition": rec.disposition_type if rec else None, "action_class": rec.action_class if rec else None,
                         "decided_by": dec.actor if dec else None, "decided_at": dec.created_at.isoformat() if dec else None,
                         "validation_result": val.verification_result if val else None,
                         "closed_at": c.updated_at.isoformat(), "posting_date": closed_date,
                         "audit_reference": ctx["refs"].get(c.id), **flags})
        else:
            if not rec or not val or rec.financial_impact <= 0:
                continue
            if not c.currency:
                raise HTTPException(status_code=422, detail=f"Case {c.case_number} has no currency; financial export cannot proceed")
            recs.append({"journal_id": f"NIM-{c.case_number}", "posting_date": c.updated_at.date().isoformat(),
                         "business_date": c.business_date, "store_id": c.store_id, "currency": c.currency,
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


def write_file_delivery(dest: ExportDestination, batch: ExportBatch, body: bytes) -> str:
    """Publish the checksum first and atomically expose the complete payload last."""
    folder_name = re.sub(r"[^A-Za-z0-9_.-]", "_", dest.name)
    if folder_name in {"", ".", ".."}:
        raise ValueError("Invalid destination directory name")
    root = EXPORT_DIR.resolve()
    folder = (root / folder_name).resolve()
    if not folder.is_relative_to(root) or folder == root:
        raise ValueError("Destination directory is outside the export root")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{batch.dataset}-{batch.id}.{dest.format}"
    temporary_paths = []
    try:
        for suffix, contents in ((".sha256", f"{batch.payload_hash}  {name}\n".encode()), ("", body)):
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".nimbus-", delete=False) as temporary:
                temporary_paths.append(Path(temporary.name))
                temporary.write(contents)
            os.replace(temporary_paths[-1], folder / (name + suffix))
        return str(folder / name)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


async def deliver(dest: ExportDestination, b: ExportBatch, client: httpx.AsyncClient | None = None) -> None:
    cfg = dest.config or {}
    try:
        if b.payload is None or not b.payload_hash:
            raise ValueError("Export payload or hash is missing")
        body = b.payload.encode()
        if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), b.payload_hash):
            raise ValueError("Export payload does not match its recorded hash")
        if dest.kind == "webhook":
            headers = {"Content-Type": "text/csv" if dest.format == "csv" else "application/json",
                       "X-Nimbus-Idempotency-Key": b.idempotency_key, "X-Nimbus-Dataset": b.dataset,
                       "X-Nimbus-Signature": sign(cfg.get("secret", ""), body),
                       "X-Nimbus-Envelope-Signature": sign_envelope(cfg.get("secret", ""), b.dataset, b.idempotency_key, body)}
            if client is None:
                async with httpx.AsyncClient(timeout=15) as owned_client:
                    r = await owned_client.post(cfg["url"], content=body, headers=headers)
            else:
                r = await client.post(cfg["url"], content=body, headers=headers)
            b.response_code = r.status_code
            if r.status_code in (200, 201):
                b.status, b.acked_at = "Acknowledged", utcnow()
                try:
                    response_body = r.json()
                except ValueError:
                    response_body = None
                if isinstance(response_body, dict):
                    b.response_ref = str(response_body.get("reference", ""))[:255] or None
            elif r.status_code == 202:
                b.status = "Sent"
            else:
                b.status, b.error = "Failed", f"HTTP {r.status_code}: {r.text[:200]}"
        else:
            b.response_ref = await asyncio.to_thread(write_file_delivery, dest, b, body)
            b.status = "Sent"
        if b.status in ("Sent", "Acknowledged"):
            b.sent_at, b.error = utcnow(), None
    except Exception as e:
        b.status, b.error = "Failed", f"{type(e).__name__}: {str(e)[:200]}"


async def finish_delivery(db: AsyncSession, batch: ExportBatch, dest_name: str, *, retrying: bool) -> dict:
    """Persist a delivery result only while this attempt still owns the batch."""
    result = (await db.execute(update(ExportBatch).where(
        ExportBatch.id == batch.id, ExportBatch.status == "Pending",
        ExportBatch.delivery_attempt_id == batch.delivery_attempt_id,
    ).values(status=batch.status, sent_at=batch.sent_at, acked_at=batch.acked_at,
             response_code=batch.response_code, response_ref=batch.response_ref, error=batch.error,
             delivery_attempt_id=None).returning(ExportBatch.id))).scalar_one_or_none()
    if result is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Delivery attempt was superseded; reconcile the downstream outcome")
    if batch.status in ("Sent", "Acknowledged"):
        await mark_delivered(db, batch.id)
    event = "EXPORT_RETRIED" if retrying else {"Acknowledged": "EXPORT_ACKNOWLEDGED", "Sent": "EXPORT_SENT"}.get(batch.status, "EXPORT_FAILED")
    audit(db, event, None, "ExportBatch", batch.id, f"Export to '{dest_name}' {batch.status.lower()}"
          + (f": {batch.error}" if batch.error else ""), actor="export-service")
    await db.commit()
    return batch_view(batch, dest_name)


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
    defaults = []
    if test_sink_enabled():
        defaults.append(ExportDestination(
            id=str(uuid.uuid4()), name="Test receiver (webhook)", kind="webhook", format="json",
            config={"url": "http://localhost:8000/api/exports/_sink", "secret": sink_secret()},
        ))
    defaults.append(ExportDestination(
        id=str(uuid.uuid4()), name="ERP/GL file drop", kind="file", format="csv", config={},
    ))
    db.add_all(defaults)
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
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A destination with that name exists") from exc
    return dest_view(d)


@router.post("/destinations/{dest_id}/toggle")
async def toggle_destination(dest_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    d = (await db.execute(select(ExportDestination).where(ExportDestination.id == dest_id).with_for_update())).scalar_one_or_none()
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
async def run_export(body: RunIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it")),
                     request: Request = None):
    if body.dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    destination_query = select(ExportDestination).where(ExportDestination.id == body.destination_id)
    if not body.dry_run:
        # Serialize delivery claims against destination toggles. Audit exports also serialize
        # stream ranges through this lock, so concurrent batches cannot overlap their ranges.
        destination_query = destination_query.with_for_update()
    dest = (await db.execute(destination_query)).scalar_one_or_none()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")
    if not dest.enabled:
        raise HTTPException(status_code=409, detail="Destination is disabled")
    actor = user["user"] or "demo.user"
    if body.scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {SCOPES}")
    if body.dataset == "audit" and not body.dry_run:
        outstanding = (await db.execute(select(ExportBatch.id).where(
            ExportBatch.destination_id == dest.id, ExportBatch.dataset == "audit",
            ExportBatch.status.in_(["Pending", "Failed"]),
        ).limit(1))).scalar_one_or_none()
        if outstanding:
            raise HTTPException(status_code=409, detail="An audit export is pending or failed; resolve the existing batch first")
    records, items, r_from, r_to = await build(db, dest, body.dataset, body.scope, lock_cases=not body.dry_run)
    everything = summarize(records)
    if body.dry_run and body.scope != "all" and body.dataset != "audit":
        unscoped_records, _, _, _ = await build(db, dest, body.dataset)
        everything = summarize(unscoped_records)  # preview is capped at MAX_BATCH
    if body.dry_run:
        return {"dry_run": True, "count": len(records), "sample": records[:3], "capped_at": MAX_BATCH,
                "waiting": everything, "scope": body.scope}
    if not records:
        return {"dry_run": False, "count": 0, "message": "Nothing new to export." if body.scope == "all" else f"Nothing waiting in the '{body.scope}' scope."}
    summary = summarize(records)

    head = await head_hash(db)
    batch_id = str(uuid.uuid4())
    # A key belongs to one immutable batch, not its list of records. Rebuilding a
    # cancelled batch may produce a different payload and must use a new key.
    key = hashlib.sha256(f"nimbus-export:{batch_id}".encode()).hexdigest()
    payload = render(dest, body.dataset, records, head, key, summary)
    b = ExportBatch(id=batch_id, destination_id=dest.id, dataset=body.dataset, idempotency_key=key,
                    created_by=actor, range_from=r_from, range_to=r_to,
                    delivery_attempt_id=str(uuid.uuid4()), attempt_started_at=utcnow())
    b.payload, b.payload_hash, b.record_count, b.audit_head_hash, b.status = payload.decode(), hashlib.sha256(payload).hexdigest(), len(records), head, "Pending"
    b.backposted_count = summary["backposted"]
    db.add(b)
    # Reserve every record for this batch before anything is sent: one record, one batch, no double delivery.
    try:
        await db.flush()
        for oid, ver, meta in items:
            db.add(ExportItem(id=str(uuid.uuid4()), batch_id=b.id, destination_id=dest.id, dataset=body.dataset, object_id=oid,
                              object_version=ver, state="Pending", **meta))
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="This export or some of its records are already reserved. Refresh and try again.")
    audit(db, "EXPORT_CREATED", None, "ExportBatch", b.id, f"Export of {len(records)} {body.dataset} record(s) to '{dest.name}' created"
          + (f" ({summary['backposted']} back-posted)" if summary["backposted"] else ""),
          actor=actor, after={"payload_hash": b.payload_hash, "idempotency_key": key, "scope": body.scope, **summary})
    await db.commit()
    db.expunge(b)
    client = getattr(request.app.state, "export_http_client", None) if request else None
    if client is None:
        await deliver(dest, b)
    else:
        await deliver(dest, b, client)
    return await finish_delivery(db, b, dest.name, retrying=False)


@router.get("/batches")
async def batches(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db),
                  _: dict = Depends(require_role("finance", "it"))):
    rows = (await db.execute(select(ExportBatch, ExportDestination.name).join(
        ExportDestination, ExportDestination.id == ExportBatch.destination_id).order_by(ExportBatch.created_at.desc()).limit(limit))).all()
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
async def retry(batch_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it")),
                request: Request = None):
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Failed":
        raise HTTPException(status_code=409, detail=f"Batch is {b.status}")
    dest = (await db.execute(select(ExportDestination).where(
        ExportDestination.id == b.destination_id).with_for_update())).scalar_one()
    if not dest.enabled:
        raise HTTPException(status_code=409, detail="Destination is disabled")
    claimed = (await db.execute(
        update(ExportBatch).where(ExportBatch.id == batch_id, ExportBatch.status == "Failed")
        .values(status="Pending", retries=ExportBatch.retries + 1,
                delivery_attempt_id=str(uuid.uuid4()), attempt_started_at=utcnow())
        .returning(ExportBatch.id).execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Batch is no longer available for retry")
    await db.commit()
    await db.refresh(b)
    db.expunge(b)
    client = getattr(request.app.state, "export_http_client", None) if request else None
    if client is None:
        await deliver(dest, b)
    else:
        await deliver(dest, b, client)
    return await finish_delivery(db, b, dest.name, retrying=True)


class FailedExportResolution(BaseModel):
    outcome: Literal["received", "not_received"]
    evidence: str


class InterruptedExportRecovery(BaseModel):
    worker_stopped: bool
    evidence: str


@router.post("/batches/{batch_id}/recover")
async def recover_interrupted_batch(batch_id: str, body: InterruptedExportRecovery,
                                    db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    """Fence an abandoned attempt after an operator verifies its worker stopped."""
    batch = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status != "Pending":
        raise HTTPException(status_code=409, detail="Only pending batches can be recovered")
    evidence = body.evidence.strip()
    if not body.worker_stopped or not evidence or len(evidence) > 2000:
        raise HTTPException(status_code=422, detail="Confirm the delivery worker stopped and provide evidence (up to 2000 characters)")
    started = batch.attempt_started_at or batch.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if started > utcnow() - timedelta(minutes=5):
        raise HTTPException(status_code=409, detail="The delivery attempt is too recent to recover")
    attempt_id = batch.delivery_attempt_id
    claimed = (await db.execute(update(ExportBatch).where(
        ExportBatch.id == batch_id, ExportBatch.status == "Pending", ExportBatch.delivery_attempt_id == attempt_id
    ).values(status="Failed", delivery_attempt_id=None,
             error="Interrupted delivery: verify the downstream outcome before resolving this batch")
        .returning(ExportBatch.id).execution_options(synchronize_session=False))).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Delivery attempt is no longer available for recovery")
    audit(db, "EXPORT_ATTEMPT_RECOVERED", None, "ExportBatch", batch_id,
          "Interrupted delivery fenced; downstream outcome requires reconciliation", actor=user["user"] or "demo.user",
          after={"attempt_id": attempt_id, "evidence": evidence})
    await db.commit()
    await db.refresh(batch)
    return batch_view(batch)


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it")),
                       body: FailedExportResolution | None = None):
    """Release a failed batch only after its downstream outcome has been checked."""
    return await resolve_failed_export(batch_id, body, db, user, require_not_received=True)


@router.post("/batches/{batch_id}/reconcile")
async def reconcile_failed_batch(batch_id: str, body: FailedExportResolution, db: AsyncSession = Depends(get_db),
                                 user: dict = Depends(require_role("it"))):
    return await resolve_failed_export(batch_id, body, db, user)


async def resolve_failed_export(batch_id: str, body: FailedExportResolution | None, db: AsyncSession,
                                user: dict, *, require_not_received: bool = False):
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Failed":
        raise HTTPException(status_code=409, detail=f"Only failed batches can be cancelled (this one is {b.status})")
    if body is None or not body.evidence.strip():
        raise HTTPException(status_code=422, detail="Verify the downstream outcome and provide evidence before resolving this batch")
    if len(body.evidence.strip()) > 2000:
        raise HTTPException(status_code=422, detail="Evidence must be at most 2000 characters")
    if require_not_received and body.outcome != "not_received":
        raise HTTPException(status_code=422, detail="Cancellation requires confirmation that the batch was not received")
    original_key = b.idempotency_key
    values = ({"status": "Cancelled"} if body.outcome == "not_received"
              else {"status": "Acknowledged", "acked_at": utcnow(), "sent_at": utcnow(), "error": None})
    claimed = (await db.execute(
        update(ExportBatch).where(ExportBatch.id == batch_id, ExportBatch.status == "Failed")
        .values(**values)
        .returning(ExportBatch.id).execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Batch is no longer available for cancellation")
    released = 0
    if body.outcome == "not_received":
        released = (await db.execute(delete(ExportItem).where(ExportItem.batch_id == b.id, ExportItem.state == "Pending"))).rowcount or 0
    else:
        await mark_delivered(db, b.id)
    await db.refresh(b)
    audit(db, "EXPORT_CANCELLED" if body.outcome == "not_received" else "EXPORT_RECONCILED_RECEIVED",
          None, "ExportBatch", b.id, f"Failed export reconciled as {body.outcome}; {released} record(s) released",
          actor=user["user"] or "demo.user", after={"outcome": body.outcome, "evidence": body.evidence.strip(),
                                                       "original_idempotency_key": original_key, "released": released})
    await db.commit()
    return {**batch_view(b), "released": released}


# ---- Tracking: which records were exported, which are waiting, which are not eligible and why -----------------
TRACK_STATES = ("delivered", "in_batch", "waiting", "not_eligible")


def tracking_projection(destination_id: str, dataset: str):
    """Build the SQL projection used by the paginated tracking API."""
    ranked_items = (select(
        ExportItem.object_id.label("object_id"), ExportItem.state.label("item_state"),
        ExportItem.record_ref.label("record_ref"), ExportItem.backpost.label("item_backpost"),
        ExportItem.delivered_at.label("delivered_at"), ExportBatch.id.label("batch_id"),
        ExportBatch.status.label("batch_status"),
        func.row_number().over(
            partition_by=ExportItem.object_id,
            order_by=(ExportBatch.created_at.desc(), ExportBatch.id.desc()),
        ).label("row_number"),
    ).join(ExportBatch, ExportBatch.id == ExportItem.batch_id).where(
        ExportItem.destination_id == destination_id, ExportItem.dataset == dataset,
    )).cte("ranked_export_items")
    items = select(ranked_items).where(ranked_items.c.row_number == 1).cte("latest_export_items")

    approved = select(Recommendation.id).where(
        Recommendation.case_id == Case.id, Recommendation.status == "Approved",
    ).exists()
    positive_approved = select(Recommendation.id).where(
        Recommendation.case_id == Case.id, Recommendation.status == "Approved",
        Recommendation.financial_impact > 0,
    ).exists()
    verified = verified_adjustment_evidence().with_only_columns(Recommendation.case_id).where(
        Recommendation.case_id == Case.id,
    ).exists()
    prior_case = aliased(Case)
    previously_exported = select(ExportItem.id).select_from(ExportItem).join(
        ExportBatch, ExportBatch.id == ExportItem.batch_id,
    ).join(prior_case, prior_case.id == ExportItem.object_id).where(
        ExportItem.destination_id == destination_id, ExportItem.dataset == dataset,
        ExportItem.state == "Delivered", ExportBatch.status.in_(("Sent", "Acknowledged")),
        prior_case.store_id == Case.store_id, prior_case.business_date == Case.business_date,
    ).exists()

    ready = select(
        Case.id.label("object_id"), Case.case_number, Case.store_id, Case.business_date,
        Case.status.label("case_status"), Case.updated_at, Case.amount_needs_review,
        items.c.object_id.label("item_object_id"), items.c.item_state,
        items.c.record_ref.label("item_record_ref"), items.c.item_backpost,
        items.c.delivered_at, items.c.batch_id, items.c.batch_status,
        approved.label("has_approved"), positive_approved.label("has_positive_approval"),
        verified.label("is_validated"), previously_exported.label("previously_exported"),
    ).select_from(Case).outerjoin(items, items.c.object_id == Case.id).cte("tracking_readiness")

    has_item = ready.c.item_object_id.is_not(None)
    state = sql_case(
        (and_(has_item, ready.c.item_state == "Delivered"), "delivered"),
        (has_item, "in_batch"),
        (ready.c.case_status != "Closed", "not_eligible"),
        (ready.c.amount_needs_review.is_(True), "not_eligible"),
        *(([(~ready.c.has_approved, "not_eligible"),
            (~ready.c.has_positive_approval, "not_eligible"),
            (~ready.c.is_validated, "not_eligible")]
           if dataset == "adjustments" else [])),
        else_="waiting",
    )
    waiting_backpost = and_(
        state == "waiting",
        or_(cast(func.date(ready.c.updated_at), String) > ready.c.business_date,
            ready.c.previously_exported),
    )
    record_ref = func.coalesce(
        func.nullif(ready.c.item_record_ref, ""),
        ready.c.case_number if dataset == "resolutions" else literal("NIM-") + ready.c.case_number,
    )
    reason = sql_case(
        (and_(has_item, ready.c.item_state != "Delivered"),
         sql_case((ready.c.batch_status == "Failed",
                   "The batch failed. Retry it, or cancel it to release these records."),
                  else_="Batch is being delivered.")),
        (ready.c.case_status != "Closed",
         literal("Case is ") + ready.c.case_status + "; only closed cases are exported."),
        (ready.c.amount_needs_review.is_(True),
         "Case amounts are stale after a correction; open a follow-up case for finance review."),
        *(([(~ready.c.has_approved, "No approved recommendation, so there is no adjustment to post."),
            (~ready.c.has_positive_approval, "Approved with no financial impact, so there is no journal line."),
            (~ready.c.is_validated, "Validation is not complete for an approved workflow.")]
           if dataset == "adjustments" else [])),
        else_=None,
    )
    backpost_reason = sql_case(
        (and_(waiting_backpost, ready.c.previously_exported), "Late addition to a business day already exported"),
        (waiting_backpost, "Resolved after the business date ended"),
        else_=None,
    )
    return select(
        ready.c.object_id, ready.c.case_number, ready.c.store_id, ready.c.business_date,
        ready.c.case_status, state.label("state"), reason.label("reason"),
        record_ref.label("record_ref"), ready.c.batch_id, ready.c.batch_status,
        ready.c.delivered_at, ready.c.item_backpost, waiting_backpost.label("waiting_backpost"),
        backpost_reason.label("backpost_reason"),
    ).select_from(ready).cte("tracking_rows")


async def tracking_page(db: AsyncSession, destination_id: str, dataset: str, state_filter: str,
                        search: str, page: int, page_size: int) -> tuple[dict, list[dict], int]:
    rows = tracking_projection(destination_id, dataset)
    state_counts = (await db.execute(select(
        rows.c.state, func.count(),
        func.sum(sql_case((rows.c.waiting_backpost, 1), else_=0)),
    ).group_by(rows.c.state))).all()
    summary = {name: 0 for name in TRACK_STATES}
    summary["waiting_backposts"] = 0
    for row_state, count, backposts in state_counts:
        summary[row_state] = count
        summary["waiting_backposts"] += backposts or 0

    conditions = []
    if state_filter:
        conditions.append(rows.c.state == state_filter)
    if search:
        needle = search.strip()
        conditions.append(or_(rows.c.record_ref.icontains(needle, autoescape=True),
                              rows.c.case_number.icontains(needle, autoescape=True),
                              rows.c.store_id.icontains(needle, autoescape=True),
                              rows.c.business_date.icontains(needle, autoescape=True)))
    total = (await db.execute(select(func.count()).select_from(rows).where(*conditions))).scalar_one()
    state_order = sql_case((rows.c.state == "in_batch", 0), (rows.c.state == "waiting", 1),
                           (rows.c.state == "not_eligible", 2), else_=3)
    selected = (await db.execute(select(rows).where(*conditions).order_by(
        state_order, rows.c.business_date, rows.c.record_ref,
    ).offset((page - 1) * page_size).limit(page_size))).mappings().all()
    items = []
    for row in selected:
        item_exists = row["batch_id"] is not None
        is_backpost = bool(row["item_backpost"] if item_exists else row["waiting_backpost"])
        items.append({
            "object_id": row["object_id"], "record_ref": row["record_ref"],
            "case_number": row["case_number"], "store_id": row["store_id"],
            "business_date": row["business_date"], "case_status": row["case_status"],
            "state": row["state"], "reason": row["reason"], "batch_id": row["batch_id"],
            "batch_status": row["batch_status"], "delivered_at": row["delivered_at"],
            "backpost": is_backpost,
            "backpost_reason": None if item_exists else row["backpost_reason"],
        })
    return summary, items, total


@router.get("/tracking")
async def tracking(destination_id: str, dataset: str,
                   state: Annotated[str, Query(max_length=20)] = "",
                   q: Annotated[str, Query(max_length=120)] = "",
                   page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                   page_size: Annotated[int, Query(ge=1, le=200)] = 50,
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
    summary, items, total = await tracking_page(db, destination_id, dataset, state, q, page, page_size)
    return {"kind": "records", "summary": summary, "items": items,
            "total": total, "page": page, "pages": max(1, -(-total // page_size))}


class AckIn(BaseModel):
    reference: str | None = Field(default=None, max_length=255)


@router.post("/batches/{batch_id}/ack")
async def acknowledge(batch_id: str, body: AckIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    """Record the downstream system's acknowledgment (for file drops and asynchronous receivers)."""
    acknowledged_at = utcnow()
    updated_id = (await db.execute(update(ExportBatch).where(
        ExportBatch.id == batch_id, ExportBatch.status == "Sent"
    ).values(status="Acknowledged", acked_at=acknowledged_at,
             response_ref=body.reference or ExportBatch.response_ref)
        .returning(ExportBatch.id).execution_options(synchronize_session=False))).scalar_one_or_none()
    if updated_id is None:
        current_status = (await db.execute(select(ExportBatch.status).where(
            ExportBatch.id == batch_id
        ))).scalar_one_or_none()
        if current_status is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        raise HTTPException(status_code=409, detail=f"Only 'Sent' batches can be acknowledged (this one is {current_status})")
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == updated_id))).scalar_one()
    audit(db, "EXPORT_ACKNOWLEDGED", None, "ExportBatch", b.id, f"Downstream acknowledged export (ref {body.reference or 'n/a'})",
          actor=user["user"] or "demo.user")
    await db.commit()
    return batch_view(b)


@router.post("/_sink", dependencies=[Depends(require_test_sink_enabled)])
async def sink(request: Request, x_nimbus_signature: str | None = Header(default=None),
               x_nimbus_idempotency_key: str | None = Header(default=None),
               x_nimbus_dataset: str | None = Header(default=None), db: AsyncSession = Depends(get_db),
               x_nimbus_envelope_signature: str | None = Header(default=None)):
    """Built-in test receiver with durable retry deduplication; not for production use."""
    body = await read_limited_body(request, MAX_SINK_BODY_BYTES)
    if not valid_signature(x_nimbus_signature, sign(SINK_SECRET, body)):
        raise HTTPException(status_code=401, detail="Bad signature")
    if not x_nimbus_idempotency_key or not re.fullmatch(r"[0-9a-f]{64}", x_nimbus_idempotency_key):
        raise HTTPException(status_code=400, detail="A valid idempotency key is required")
    if x_nimbus_dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="A valid dataset is required")
    if not valid_signature(x_nimbus_envelope_signature, sign_envelope(SINK_SECRET, x_nimbus_dataset,
                                                                        x_nimbus_idempotency_key, body)):
        raise HTTPException(status_code=401, detail="Bad envelope signature")
    digest = hashlib.sha256(body).hexdigest()
    try:
        async with db.begin_nested():
            await db.execute(insert(TestSinkReceipt).values(idempotency_key=x_nimbus_idempotency_key,
                             dataset=x_nimbus_dataset, payload_hash=digest, byte_count=len(body)))
    except IntegrityError:
        receipt = await db.get(TestSinkReceipt, x_nimbus_idempotency_key)
        if not receipt or receipt.payload_hash != digest or receipt.dataset != x_nimbus_dataset:
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different delivery")
    await db.commit()
    return {"status": "acknowledged", "reference": f"SINK-{x_nimbus_idempotency_key[:10]}", "bytes": len(body)}

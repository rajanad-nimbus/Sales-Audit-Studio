"""Downstream export: destinations, incremental idempotent batches, signed delivery, acknowledgment tracking."""
import hashlib
import os
import re
import uuid
from datetime import timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from database import get_db
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.database_errors import violates_unique_constraint
from apps.exports.delivery import (
    deliver as perform_delivery,
)
from apps.exports.delivery import (
    render as render,
)
from apps.exports.delivery import (
    sign as sign,
)
from apps.exports.delivery import (
    sign_envelope as sign_envelope,
)
from apps.exports.delivery import (
    valid_signature as valid_signature,
)
from apps.exports.models import ExportBatch, ExportDestination, ExportItem, TestSinkReceipt
from apps.exports.schemas import (
    AckIn,
    DestIn,
    FailedExportResolution,
    InterruptedExportRecovery,
    RunIn,
)
from apps.exports.selection import (
    MAX_BATCH,
    SCOPES,
    build,
    head_hash,
    summarize,
)
from apps.exports.selection import (
    classify_backpost as classify_backpost,
)
from apps.exports.selection import (
    closed_case_context as closed_case_context,
)
from apps.exports.selection import (
    prior_exported_days as prior_exported_days,
)
from apps.exports.tracking import TRACK_STATES, tracking_page
from apps.request_body import read_limited_body
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/exports")
EXPORT_DIR = Path(os.getenv("NIMBUS_EXPORT_DIR", "/app/exports"))
MAX_SINK_BODY_BYTES = 25 * 1024 * 1024
DATASETS = {
    "resolutions": "Closed, validated cases with decision, disposition and validation result",
    "adjustments": "Approved financial adjustments from validated cases (journal lines for ERP/GL)",
    "audit": "Audit trail events since the last delivery (with hashes)",
}
DEFAULT_DESTINATION_NAMESPACE = uuid.UUID("e3648eeb-fb20-4290-a08b-fd39404b0be4")


def test_sink_enabled() -> bool:
    environment = os.getenv("ENVIRONMENT", "production").strip().lower()
    explicit_setting = os.getenv("NIMBUS_ENABLE_TEST_SINK", "true").strip().lower()
    return environment in {"development", "test"} and explicit_setting in {"1", "true", "yes", "on"}


def sink_secret() -> str:
    configured = os.getenv("NIMBUS_SINK_SECRET")
    if configured:
        return configured
    return "nimbus-local-test-sink-secret-32chars" if test_sink_enabled() else ""


SINK_SECRET = sink_secret()


async def require_test_sink_enabled() -> None:
    if not test_sink_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _is_duplicate_destination_name(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"export_destinations_name_key"},
        sqlite_messages={"UNIQUE constraint failed: export_destinations.name"},
    )


def _is_export_reservation_conflict(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"export_batches_idempotency_key_key", "uq_export_item"},
        sqlite_messages={
            "UNIQUE constraint failed: export_batches.idempotency_key",
            "UNIQUE constraint failed: export_items.destination_id, export_items.dataset, "
            "export_items.object_id, export_items.object_version",
        },
    )


def _is_sink_receipt_conflict(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"test_sink_receipts_pkey"},
        sqlite_messages={"UNIQUE constraint failed: test_sink_receipts.idempotency_key"},
    )


def dest_view(d: ExportDestination, *, reveal_url: bool = False) -> dict:
    cfg = dict(d.config or {})
    if "secret" in cfg:
        cfg["secret"] = "••••••" if cfg["secret"] else ""
    if cfg.get("url") and not reveal_url:
        cfg["url"] = webhook_origin(str(cfg["url"]))
    return {"id": d.id, "name": d.name, "kind": d.kind, "config": cfg, "format": d.format, "enabled": d.enabled}


def webhook_origin(url: str) -> str:
    """Expose only the webhook origin when the caller cannot manage destination credentials."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not hostname:
            return "Webhook configured"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "Webhook configured"
    return f"{parsed.scheme}://{host}{port}"


def batch_view(b: ExportBatch, dest_name: str | None = None) -> dict:
    return {"id": b.id, "destination_id": b.destination_id, "destination": dest_name, "dataset": b.dataset,
            "status": b.status, "record_count": b.record_count, "payload_hash": b.payload_hash,
            "audit_head_hash": b.audit_head_hash, "created_by": b.created_by, "created_at": b.created_at,
            "sent_at": b.sent_at, "acked_at": b.acked_at, "response_code": b.response_code,
            "response_ref": b.response_ref, "error": b.error, "retries": b.retries,
            "range_from": b.range_from, "range_to": b.range_to, "backposted_count": b.backposted_count or 0,
            "attempt_started_at": b.attempt_started_at}


async def deliver(
    destination: ExportDestination,
    batch: ExportBatch,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Run delivery using the configured export root and shared HTTP client."""
    await perform_delivery(destination, batch, client, EXPORT_DIR)


async def mark_delivered(db: AsyncSession, batch_id: str) -> None:
    await db.execute(
        update(ExportItem)
        .where(ExportItem.batch_id == batch_id, ExportItem.state == "Pending")
        .values(state="Delivered", delivered_at=utcnow())
    )


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


@router.get("/datasets")
async def datasets(_: dict = Depends(require_role("finance", "it", "auditor"))):
    return [{"key": k, "description": v} for k, v in DATASETS.items()]


async def ensure_defaults(db):
    if (await db.execute(select(func.count()).select_from(ExportDestination))).scalar():
        return
    defaults = []
    if test_sink_enabled():
        defaults.append({
            "id": str(uuid.uuid5(DEFAULT_DESTINATION_NAMESPACE, "Test receiver (webhook)")),
            "name": "Test receiver (webhook)", "kind": "webhook", "format": "json",
            "config": {"url": "http://localhost:8000/api/exports/_sink", "secret": sink_secret()},
        })
    defaults.append({
        "id": str(uuid.uuid5(DEFAULT_DESTINATION_NAMESPACE, "ERP/GL file drop")),
        "name": "ERP/GL file drop", "kind": "file", "format": "csv", "config": {},
    })
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(ExportDestination).values(defaults).on_conflict_do_nothing(
            index_elements=["name"],
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(ExportDestination).values(defaults).on_conflict_do_nothing(
            index_elements=["name"],
        )
    else:
        raise RuntimeError(f"Default export destinations are unsupported for database dialect {dialect}")
    await db.execute(statement)
    await db.commit()


@router.get("/destinations")
async def list_destinations(db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it", "auditor"))):
    await ensure_defaults(db)
    reveal_url = user["role"] in {"it", "admin"}
    return [dest_view(d, reveal_url=reveal_url) for d in (await db.execute(select(ExportDestination).order_by(ExportDestination.name))).scalars().all()]


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
        if _is_duplicate_destination_name(exc):
            raise HTTPException(status_code=409, detail="A destination with that name exists") from exc
        raise
    return dest_view(d, reveal_url=True)


@router.post("/destinations/{dest_id}/toggle")
async def toggle_destination(dest_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    d = (await db.execute(select(ExportDestination).where(ExportDestination.id == dest_id).with_for_update())).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Destination not found")
    d.enabled = not d.enabled
    audit(db, "EXPORT_DESTINATION_TOGGLED", None, "ExportDestination", d.id, f"'{d.name}' {'enabled' if d.enabled else 'disabled'}",
          actor=user["user"] or "demo.user")
    await db.commit()
    return dest_view(d, reveal_url=True)


@router.post("/run")
async def run_export(body: RunIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it")),
                     request: Request = None):
    dest = await validate_export_request(body, db)
    actor = user["user"] or "demo.user"
    await validate_audit_export_claim(body, dest, db)
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
    b = await reserve_export_batch(db, body, dest, actor, records, items, r_from, r_to)
    client = getattr(request.app.state, "export_http_client", None) if request else None
    if client is None:
        await deliver(dest, b)
    else:
        await deliver(dest, b, client)
    return await finish_delivery(db, b, dest.name, retrying=False)


async def validate_export_request(body: RunIn, db: AsyncSession) -> ExportDestination:
    """Validate dataset, scope, and destination before preparing an export."""
    if body.dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    if body.scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {SCOPES}")
    destination_query = select(ExportDestination).where(ExportDestination.id == body.destination_id)
    if not body.dry_run:
        # Serialize delivery claims against destination toggles and audit range allocation.
        destination_query = destination_query.with_for_update()
    destination = (await db.execute(destination_query)).scalar_one_or_none()
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    if not destination.enabled:
        raise HTTPException(status_code=409, detail="Destination is disabled")
    return destination


async def validate_audit_export_claim(body: RunIn, destination: ExportDestination,
                                      db: AsyncSession) -> None:
    """Prevent a second audit range while a prior batch remains unresolved."""
    if body.dataset != "audit" or body.dry_run:
        return
    outstanding = (await db.execute(select(ExportBatch.id).where(
        ExportBatch.destination_id == destination.id, ExportBatch.dataset == "audit",
        ExportBatch.status.in_(["Pending", "Failed"]),
    ).limit(1))).scalar_one_or_none()
    if outstanding:
        raise HTTPException(status_code=409,
                            detail="An audit export is pending or failed; resolve the existing batch first")


async def reserve_export_batch(db: AsyncSession, body: RunIn, destination: ExportDestination,
                               actor: str, records: list[dict], items: list[tuple],
                               range_from: int | None, range_to: int | None) -> ExportBatch:
    """Persist an immutable payload and reserve its records before delivery."""
    summary = summarize(records)
    audit_head = await head_hash(db)
    batch_id = str(uuid.uuid4())
    idempotency_key = hashlib.sha256(f"nimbus-export:{batch_id}".encode()).hexdigest()
    payload = render(destination, body.dataset, records, audit_head, idempotency_key, summary)
    batch = ExportBatch(
        id=batch_id, destination_id=destination.id, dataset=body.dataset,
        idempotency_key=idempotency_key, created_by=actor,
        range_from=range_from, range_to=range_to,
        delivery_attempt_id=str(uuid.uuid4()), attempt_started_at=utcnow(),
        payload=payload.decode(), payload_hash=hashlib.sha256(payload).hexdigest(),
        record_count=len(records), audit_head_hash=audit_head, status="Pending",
        backposted_count=summary["backposted"],
    )
    db.add(batch)
    try:
        await db.flush()
        for object_id, version, metadata in items:
            db.add(ExportItem(
                id=str(uuid.uuid4()), batch_id=batch.id, destination_id=destination.id,
                dataset=body.dataset, object_id=object_id, object_version=version,
                state="Pending", **metadata))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if _is_export_reservation_conflict(exc):
            raise HTTPException(status_code=409,
                                detail="This export or some of its records are already reserved. Refresh and try again.") from exc
        raise
    audit(db, "EXPORT_CREATED", None, "ExportBatch", batch.id,
          f"Export of {len(records)} {body.dataset} record(s) to '{destination.name}' created"
          + (f" ({summary['backposted']} back-posted)" if summary["backposted"] else ""),
          actor=actor, after={"payload_hash": batch.payload_hash, "idempotency_key": idempotency_key,
                              "scope": body.scope, **summary})
    await db.commit()
    db.expunge(batch)
    return batch


@router.get("/batches")
async def batches(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db),
                  _: dict = Depends(require_role("finance", "it", "auditor"))):
    rows = (await db.execute(select(ExportBatch, ExportDestination.name).join(
        ExportDestination, ExportDestination.id == ExportBatch.destination_id).order_by(ExportBatch.created_at.desc()).limit(limit))).all()
    return [batch_view(b, n) for b, n in rows]


@router.get("/batches/{batch_id}/payload")
async def payload(batch_id: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it", "auditor"))):
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
@router.get("/tracking")
async def tracking(destination_id: str, dataset: str,
                   state: Annotated[str, Query(max_length=20)] = "",
                   q: Annotated[str, Query(max_length=120)] = "",
                   page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                   page_size: Annotated[int, Query(ge=1, le=200)] = 50,
                   db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it", "auditor"))):
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
    except IntegrityError as exc:
        if not _is_sink_receipt_conflict(exc):
            raise
        receipt = await db.get(TestSinkReceipt, x_nimbus_idempotency_key)
        if not receipt or receipt.payload_hash != digest or receipt.dataset != x_nimbus_dataset:
            raise HTTPException(
                status_code=409, detail="Idempotency key was already used for a different delivery"
            ) from exc
    await db.commit()
    return {"status": "acknowledged", "reference": f"SINK-{x_nimbus_idempotency_key[:10]}", "bytes": len(body)}

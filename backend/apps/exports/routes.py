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
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security.authentication import require_role
from apps.database_errors import violates_unique_constraint
from apps.request_body import read_limited_body
from database import get_db
from apps.audit.models import AuditEvent
from model_base import utcnow
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
    classify_backpost as classify_backpost,
    closed_case_context as closed_case_context,
    head_hash,
    prior_exported_days as prior_exported_days,
    summarize,
)
from apps.exports.tracking import TRACK_STATES, tracking_page
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/exports")
EXPORT_DIR = Path(os.getenv("NIMBUS_EXPORT_DIR", "/app/exports"))
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


async def mark_delivered(db, batch_id: str) -> None:
    await db.execute(update(ExportItem).where(ExportItem.batch_id == batch_id, ExportItem.state == "Pending")
                     .values(state="Delivered", delivered_at=utcnow()))


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
    try:
        body = verified_export_body(b)
        if dest.kind == "webhook":
            await deliver_webhook(dest, b, body, client)
        else:
            await deliver_file(dest, b, body)
        if b.status in ("Sent", "Acknowledged"):
            b.sent_at, b.error = utcnow(), None
    except Exception as e:
        b.status, b.error = "Failed", f"{type(e).__name__}: {str(e)[:200]}"


def verified_export_body(batch: ExportBatch) -> bytes:
    """Return the persisted payload only if its digest matches the immutable batch."""
    if batch.payload is None or not batch.payload_hash:
        raise ValueError("Export payload or hash is missing")
    body = batch.payload.encode()
    digest = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(digest, batch.payload_hash):
        raise ValueError("Export payload does not match its recorded hash")
    return body


async def deliver_webhook(destination: ExportDestination, batch: ExportBatch, body: bytes,
                          client: httpx.AsyncClient | None) -> None:
    """Send a signed webhook and map receiver responses onto batch state."""
    config = destination.config or {}
    secret = config.get("secret", "")
    headers = {
        "Content-Type": "text/csv" if destination.format == "csv" else "application/json",
        "X-Nimbus-Idempotency-Key": batch.idempotency_key,
        "X-Nimbus-Dataset": batch.dataset,
        "X-Nimbus-Signature": sign(secret, body),
        "X-Nimbus-Envelope-Signature": sign_envelope(
            secret, batch.dataset, batch.idempotency_key, body),
    }
    if client is None:
        async with httpx.AsyncClient(timeout=15) as owned_client:
            response = await owned_client.post(config["url"], content=body, headers=headers)
    else:
        response = await client.post(config["url"], content=body, headers=headers)
    batch.response_code = response.status_code
    if response.status_code in (200, 201):
        batch.status, batch.acked_at = "Acknowledged", utcnow()
        batch.response_ref = response_reference(response)
    elif response.status_code == 202:
        batch.status = "Sent"
    else:
        batch.status, batch.error = "Failed", f"HTTP {response.status_code}: {response.text[:200]}"


def response_reference(response: httpx.Response) -> str | None:
    """Extract a bounded receiver reference from a successful JSON response."""
    try:
        response_body = response.json()
    except ValueError:
        return None
    if not isinstance(response_body, dict):
        return None
    return str(response_body.get("reference", ""))[:255] or None


async def deliver_file(destination: ExportDestination, batch: ExportBatch, body: bytes) -> None:
    """Publish a verified payload and checksum through atomic file replacement."""
    batch.response_ref = await asyncio.to_thread(write_file_delivery, destination, batch, body)
    batch.status = "Sent"


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
        if _is_duplicate_destination_name(exc):
            raise HTTPException(status_code=409, detail="A destination with that name exists") from exc
        raise
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
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different delivery")
    await db.commit()
    return {"status": "acknowledged", "reference": f"SINK-{x_nimbus_idempotency_key[:10]}", "bytes": len(body)}

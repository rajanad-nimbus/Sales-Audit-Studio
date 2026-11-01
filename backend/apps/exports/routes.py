"""Downstream export: destinations, incremental idempotent batches, signed delivery, acknowledgment tracking."""

import hashlib
import os
import re
import uuid
from pathlib import Path

import httpx
from database import get_db
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
from apps.exports.batch_state import batch_view as batch_view
from apps.exports.batch_state import mark_delivered as mark_delivered
from apps.exports.catalog import DATASETS as DATASETS
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
from apps.exports.destinations import (
    _is_duplicate_destination_name as _is_duplicate_destination_name,
)
from apps.exports.destinations import (
    dest_view as dest_view,
)
from apps.exports.destinations import (
    ensure_defaults as ensure_destination_defaults,
)
from apps.exports.destinations import (
    router as destinations_router,
)
from apps.exports.destinations import (
    webhook_origin as webhook_origin,
)
from apps.exports.models import ExportBatch, ExportDestination, TestSinkReceipt
from apps.exports.reservations import (
    _is_export_reservation_conflict as _is_export_reservation_conflict,
)
from apps.exports.reservations import (
    reserve_export_batch as reserve_export_batch,
)
from apps.exports.resolution_routes import (
    cancel_batch as cancel_batch,
)
from apps.exports.resolution_routes import (
    reconcile_failed_batch as reconcile_failed_batch,
)
from apps.exports.resolution_routes import (
    recover_interrupted_batch as recover_interrupted_batch,
)
from apps.exports.resolution_routes import (
    resolve_failed_export as resolve_failed_export,
)
from apps.exports.resolution_routes import (
    router as resolution_router,
)
from apps.exports.schemas import (
    AckIn,
    RunIn,
)
from apps.exports.schemas import (
    FailedExportResolution as FailedExportResolution,
)
from apps.exports.schemas import (
    InterruptedExportRecovery as InterruptedExportRecovery,
)
from apps.exports.selection import (
    MAX_BATCH,
    SCOPES,
    build,
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
from apps.exports.settings import SINK_SECRET, sink_secret, test_sink_enabled
from apps.exports.tracking import tracking_page as tracking_page
from apps.exports.tracking_routes import router as tracking_router
from apps.exports.tracking_routes import tracking as tracking
from apps.request_body import read_limited_body
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/exports")
router.include_router(destinations_router)
router.include_router(resolution_router)
router.include_router(tracking_router)
EXPORT_DIR = Path(os.getenv("NIMBUS_EXPORT_DIR", "/app/exports"))
MAX_SINK_BODY_BYTES = 25 * 1024 * 1024


async def deliver(
    destination: ExportDestination,
    batch: ExportBatch,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Deliver using the configured export root and optional shared HTTP client."""
    await perform_delivery(destination, batch, client, EXPORT_DIR)


async def require_test_sink_enabled() -> None:
    if not test_sink_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _is_sink_receipt_conflict(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"test_sink_receipts_pkey"},
        sqlite_messages={"UNIQUE constraint failed: test_sink_receipts.idempotency_key"},
    )


async def finish_delivery(
    db: AsyncSession, batch: ExportBatch, dest_name: str, *, retrying: bool
) -> dict:
    """Persist a delivery result only while this attempt still owns the batch."""
    result = (
        await db.execute(
            update(ExportBatch)
            .where(
                ExportBatch.id == batch.id,
                ExportBatch.status == "Pending",
                ExportBatch.delivery_attempt_id == batch.delivery_attempt_id,
            )
            .values(
                status=batch.status,
                sent_at=batch.sent_at,
                acked_at=batch.acked_at,
                response_code=batch.response_code,
                response_ref=batch.response_ref,
                error=batch.error,
                delivery_attempt_id=None,
            )
            .returning(ExportBatch.id)
        )
    ).scalar_one_or_none()
    if result is None:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Delivery attempt was superseded; reconcile the downstream outcome",
        )
    if batch.status in ("Sent", "Acknowledged"):
        await mark_delivered(db, batch.id)
    event = (
        "EXPORT_RETRIED"
        if retrying
        else {"Acknowledged": "EXPORT_ACKNOWLEDGED", "Sent": "EXPORT_SENT"}.get(
            batch.status, "EXPORT_FAILED"
        )
    )
    audit(
        db,
        event,
        None,
        "ExportBatch",
        batch.id,
        f"Export to '{dest_name}' {batch.status.lower()}"
        + (f": {batch.error}" if batch.error else ""),
        actor="export-service",
    )
    await db.commit()
    return batch_view(batch, dest_name)


@router.get("/datasets")
async def datasets(_: dict = Depends(require_role("finance", "it", "auditor"))):
    return [{"key": k, "description": v} for k, v in DATASETS.items()]


async def ensure_defaults(db):
    """Compatibility wrapper for initializing default export destinations."""
    await ensure_destination_defaults(
        db, test_sink_enabled=test_sink_enabled(), sink_secret=sink_secret()
    )


@router.post("/run")
async def run_export(
    body: RunIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
    request: Request = None,
):
    dest = await validate_export_request(body, db)
    actor = user["user"] or "demo.user"
    await validate_audit_export_claim(body, dest, db)
    records, items, r_from, r_to = await build(
        db, dest, body.dataset, body.scope, lock_cases=not body.dry_run
    )
    everything = summarize(records)
    if body.dry_run and body.scope != "all" and body.dataset != "audit":
        unscoped_records, _, _, _ = await build(db, dest, body.dataset)
        everything = summarize(unscoped_records)  # preview is capped at MAX_BATCH
    if body.dry_run:
        return {
            "dry_run": True,
            "count": len(records),
            "sample": records[:3],
            "capped_at": MAX_BATCH,
            "waiting": everything,
            "scope": body.scope,
        }
    if not records:
        return {
            "dry_run": False,
            "count": 0,
            "message": "Nothing new to export."
            if body.scope == "all"
            else f"Nothing waiting in the '{body.scope}' scope.",
        }
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


async def validate_audit_export_claim(
    body: RunIn, destination: ExportDestination, db: AsyncSession
) -> None:
    """Prevent a second audit range while a prior batch remains unresolved."""
    if body.dataset != "audit" or body.dry_run:
        return
    outstanding = (
        await db.execute(
            select(ExportBatch.id)
            .where(
                ExportBatch.destination_id == destination.id,
                ExportBatch.dataset == "audit",
                ExportBatch.status.in_(["Pending", "Failed"]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if outstanding:
        raise HTTPException(
            status_code=409,
            detail="An audit export is pending or failed; resolve the existing batch first",
        )


@router.get("/batches")
async def batches(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    rows = (
        await db.execute(
            select(ExportBatch, ExportDestination.name)
            .join(ExportDestination, ExportDestination.id == ExportBatch.destination_id)
            .order_by(ExportBatch.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [batch_view(b, n) for b, n in rows]


@router.get("/batches/{batch_id}/payload")
async def payload(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    from fastapi.responses import Response

    b = (
        await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))
    ).scalar_one_or_none()
    if not b or b.payload is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    d = (
        await db.execute(select(ExportDestination).where(ExportDestination.id == b.destination_id))
    ).scalar_one()
    return Response(
        b.payload,
        media_type="text/csv" if d.format == "csv" else "application/json",
        headers={"Content-Disposition": f"attachment; filename={b.dataset}-{b.id[:8]}.{d.format}"},
    )


@router.post("/batches/{batch_id}/retry")
async def retry(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
    request: Request = None,
):
    b = (
        await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))
    ).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Failed":
        raise HTTPException(status_code=409, detail=f"Batch is {b.status}")
    dest = (
        await db.execute(
            select(ExportDestination)
            .where(ExportDestination.id == b.destination_id)
            .with_for_update()
        )
    ).scalar_one()
    if not dest.enabled:
        raise HTTPException(status_code=409, detail="Destination is disabled")
    claimed = (
        await db.execute(
            update(ExportBatch)
            .where(ExportBatch.id == batch_id, ExportBatch.status == "Failed")
            .values(
                status="Pending",
                retries=ExportBatch.retries + 1,
                delivery_attempt_id=str(uuid.uuid4()),
                attempt_started_at=utcnow(),
            )
            .returning(ExportBatch.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
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


@router.post("/batches/{batch_id}/ack")
async def acknowledge(
    batch_id: str,
    body: AckIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    """Record the downstream system's acknowledgment (for file drops and asynchronous receivers)."""
    acknowledged_at = utcnow()
    updated_id = (
        await db.execute(
            update(ExportBatch)
            .where(ExportBatch.id == batch_id, ExportBatch.status == "Sent")
            .values(
                status="Acknowledged",
                acked_at=acknowledged_at,
                response_ref=body.reference or ExportBatch.response_ref,
            )
            .returning(ExportBatch.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if updated_id is None:
        current_status = (
            await db.execute(select(ExportBatch.status).where(ExportBatch.id == batch_id))
        ).scalar_one_or_none()
        if current_status is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        raise HTTPException(
            status_code=409,
            detail=f"Only 'Sent' batches can be acknowledged (this one is {current_status})",
        )
    b = (await db.execute(select(ExportBatch).where(ExportBatch.id == updated_id))).scalar_one()
    audit(
        db,
        "EXPORT_ACKNOWLEDGED",
        None,
        "ExportBatch",
        b.id,
        f"Downstream acknowledged export (ref {body.reference or 'n/a'})",
        actor=user["user"] or "demo.user",
    )
    await db.commit()
    return batch_view(b)


@router.post("/_sink", dependencies=[Depends(require_test_sink_enabled)])
async def sink(
    request: Request,
    x_nimbus_signature: str | None = Header(default=None),
    x_nimbus_idempotency_key: str | None = Header(default=None),
    x_nimbus_dataset: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    x_nimbus_envelope_signature: str | None = Header(default=None),
):
    """Built-in test receiver with durable retry deduplication; not for production use."""
    body = await read_limited_body(request, MAX_SINK_BODY_BYTES)
    if not valid_signature(x_nimbus_signature, sign(SINK_SECRET, body)):
        raise HTTPException(status_code=401, detail="Bad signature")
    if not x_nimbus_idempotency_key or not re.fullmatch(r"[0-9a-f]{64}", x_nimbus_idempotency_key):
        raise HTTPException(status_code=400, detail="A valid idempotency key is required")
    if x_nimbus_dataset not in DATASETS:
        raise HTTPException(status_code=400, detail="A valid dataset is required")
    if not valid_signature(
        x_nimbus_envelope_signature,
        sign_envelope(SINK_SECRET, x_nimbus_dataset, x_nimbus_idempotency_key, body),
    ):
        raise HTTPException(status_code=401, detail="Bad envelope signature")
    digest = hashlib.sha256(body).hexdigest()
    try:
        async with db.begin_nested():
            await db.execute(
                insert(TestSinkReceipt).values(
                    idempotency_key=x_nimbus_idempotency_key,
                    dataset=x_nimbus_dataset,
                    payload_hash=digest,
                    byte_count=len(body),
                )
            )
    except IntegrityError as exc:
        if not _is_sink_receipt_conflict(exc):
            raise
        receipt = await db.get(TestSinkReceipt, x_nimbus_idempotency_key)
        if not receipt or receipt.payload_hash != digest or receipt.dataset != x_nimbus_dataset:
            raise HTTPException(
                status_code=409, detail="Idempotency key was already used for a different delivery"
            ) from exc
    await db.commit()
    return {
        "status": "acknowledged",
        "reference": f"SINK-{x_nimbus_idempotency_key[:10]}",
        "bytes": len(body),
    }

"""Recovery and outcome reconciliation for export batches."""

from datetime import UTC, timedelta

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.exports.batch_state import batch_view, mark_delivered
from apps.exports.models import ExportBatch, ExportItem
from apps.exports.schemas import FailedExportResolution, InterruptedExportRecovery
from apps.security.authentication import require_role

router = APIRouter()


@router.post("/batches/{batch_id}/recover")
async def recover_interrupted_batch(
    batch_id: str,
    body: InterruptedExportRecovery,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    """Fence an abandoned attempt after an operator verifies its worker stopped."""
    batch = (
        await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))
    ).scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status != "Pending":
        raise HTTPException(status_code=409, detail="Only pending batches can be recovered")
    evidence = body.evidence.strip()
    if not body.worker_stopped or not evidence or len(evidence) > 2000:
        raise HTTPException(
            status_code=422,
            detail="Confirm the delivery worker stopped and provide evidence (up to 2000 characters)",
        )
    started = batch.attempt_started_at or batch.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if started > utcnow() - timedelta(minutes=5):
        raise HTTPException(status_code=409, detail="The delivery attempt is too recent to recover")
    attempt_id = batch.delivery_attempt_id
    claimed = (
        await db.execute(
            update(ExportBatch)
            .where(
                ExportBatch.id == batch_id,
                ExportBatch.status == "Pending",
                ExportBatch.delivery_attempt_id == attempt_id,
            )
            .values(
                status="Failed",
                delivery_attempt_id=None,
                error="Interrupted delivery: verify the downstream outcome before resolving this batch",
            )
            .returning(ExportBatch.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Delivery attempt is no longer available for recovery"
        )
    audit(
        db,
        "EXPORT_ATTEMPT_RECOVERED",
        None,
        "ExportBatch",
        batch_id,
        "Interrupted delivery fenced; downstream outcome requires reconciliation",
        actor=user["user"] or "demo.user",
        after={"attempt_id": attempt_id, "evidence": evidence},
    )
    await db.commit()
    await db.refresh(batch)
    return batch_view(batch)


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
    body: FailedExportResolution | None = None,
):
    """Release a failed batch only after its downstream outcome has been checked."""
    return await resolve_failed_export(batch_id, body, db, user, require_not_received=True)


@router.post("/batches/{batch_id}/reconcile")
async def reconcile_failed_batch(
    batch_id: str,
    body: FailedExportResolution,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    return await resolve_failed_export(batch_id, body, db, user)


async def resolve_failed_export(
    batch_id: str,
    body: FailedExportResolution | None,
    db: AsyncSession,
    user: dict,
    *,
    require_not_received: bool = False,
):
    b = (
        await db.execute(select(ExportBatch).where(ExportBatch.id == batch_id))
    ).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    if b.status != "Failed":
        raise HTTPException(
            status_code=409, detail=f"Only failed batches can be cancelled (this one is {b.status})"
        )
    if body is None or not body.evidence.strip():
        raise HTTPException(
            status_code=422,
            detail="Verify the downstream outcome and provide evidence before resolving this batch",
        )
    if len(body.evidence.strip()) > 2000:
        raise HTTPException(status_code=422, detail="Evidence must be at most 2000 characters")
    if require_not_received and body.outcome != "not_received":
        raise HTTPException(
            status_code=422,
            detail="Cancellation requires confirmation that the batch was not received",
        )
    original_key = b.idempotency_key
    values = (
        {"status": "Cancelled"}
        if body.outcome == "not_received"
        else {"status": "Acknowledged", "acked_at": utcnow(), "sent_at": utcnow(), "error": None}
    )
    claimed = (
        await db.execute(
            update(ExportBatch)
            .where(ExportBatch.id == batch_id, ExportBatch.status == "Failed")
            .values(**values)
            .returning(ExportBatch.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Batch is no longer available for cancellation")
    released = 0
    if body.outcome == "not_received":
        released = (
            await db.execute(
                delete(ExportItem).where(ExportItem.batch_id == b.id, ExportItem.state == "Pending")
            )
        ).rowcount or 0
    else:
        await mark_delivered(db, b.id)
    await db.refresh(b)
    audit(
        db,
        "EXPORT_CANCELLED" if body.outcome == "not_received" else "EXPORT_RECONCILED_RECEIVED",
        None,
        "ExportBatch",
        b.id,
        f"Failed export reconciled as {body.outcome}; {released} record(s) released",
        actor=user["user"] or "demo.user",
        after={
            "outcome": body.outcome,
            "evidence": body.evidence.strip(),
            "original_idempotency_key": original_key,
            "released": released,
        },
    )
    await db.commit()
    return {**batch_view(b), "released": released}

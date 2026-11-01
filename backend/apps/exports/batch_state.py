"""Shared export batch projections and state transitions."""

from model_base import utcnow
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.exports.models import ExportBatch, ExportItem


def batch_view(b: ExportBatch, dest_name: str | None = None) -> dict:
    return {
        "id": b.id,
        "destination_id": b.destination_id,
        "destination": dest_name,
        "dataset": b.dataset,
        "status": b.status,
        "record_count": b.record_count,
        "payload_hash": b.payload_hash,
        "audit_head_hash": b.audit_head_hash,
        "created_by": b.created_by,
        "created_at": b.created_at,
        "sent_at": b.sent_at,
        "acked_at": b.acked_at,
        "response_code": b.response_code,
        "response_ref": b.response_ref,
        "error": b.error,
        "retries": b.retries,
        "range_from": b.range_from,
        "range_to": b.range_to,
        "backposted_count": b.backposted_count or 0,
        "attempt_started_at": b.attempt_started_at,
    }


async def mark_delivered(db: AsyncSession, batch_id: str) -> None:
    await db.execute(
        update(ExportItem)
        .where(ExportItem.batch_id == batch_id, ExportItem.state == "Pending")
        .values(state="Delivered", delivered_at=utcnow())
    )

"""Atomic export-batch reservation and immutable payload persistence."""

import hashlib
import uuid

from fastapi import HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
from apps.exports.delivery import render
from apps.exports.models import ExportBatch, ExportDestination, ExportItem
from apps.exports.schemas import RunIn
from apps.exports.selection import head_hash, summarize


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


async def reserve_export_batch(
    db: AsyncSession,
    body: RunIn,
    destination: ExportDestination,
    actor: str,
    records: list[dict],
    items: list[tuple],
    range_from: int | None,
    range_to: int | None,
) -> ExportBatch:
    """Persist an immutable payload and reserve its records before delivery."""
    summary = summarize(records)
    audit_head = await head_hash(db)
    batch_id = str(uuid.uuid4())
    idempotency_key = hashlib.sha256(f"nimbus-export:{batch_id}".encode()).hexdigest()
    payload = render(destination, body.dataset, records, audit_head, idempotency_key, summary)
    batch = ExportBatch(
        id=batch_id,
        destination_id=destination.id,
        dataset=body.dataset,
        idempotency_key=idempotency_key,
        created_by=actor,
        range_from=range_from,
        range_to=range_to,
        delivery_attempt_id=str(uuid.uuid4()),
        attempt_started_at=utcnow(),
        payload=payload.decode(),
        payload_hash=hashlib.sha256(payload).hexdigest(),
        record_count=len(records),
        audit_head_hash=audit_head,
        status="Pending",
        backposted_count=summary["backposted"],
    )
    db.add(batch)
    try:
        await db.flush()
        for object_id, version, metadata in items:
            db.add(
                ExportItem(
                    id=str(uuid.uuid4()),
                    batch_id=batch.id,
                    destination_id=destination.id,
                    dataset=body.dataset,
                    object_id=object_id,
                    object_version=version,
                    state="Pending",
                    **metadata,
                )
            )
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if _is_export_reservation_conflict(exc):
            raise HTTPException(
                status_code=409,
                detail="This export or some of its records are already reserved. Refresh and try again.",
            ) from exc
        raise
    audit(
        db,
        "EXPORT_CREATED",
        None,
        "ExportBatch",
        batch.id,
        f"Export of {len(records)} {body.dataset} record(s) to '{destination.name}' created"
        + (f" ({summary['backposted']} back-posted)" if summary["backposted"] else ""),
        actor=actor,
        after={
            "payload_hash": batch.payload_hash,
            "idempotency_key": idempotency_key,
            "scope": body.scope,
            **summary,
        },
    )
    await db.commit()
    db.expunge(batch)
    return batch

"""Archive, protect, and purge expired operational records."""

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.corrections.models import TransactionAdjustment
from apps.ingest.models import SourceRecord
from apps.operations.models import RetentionPolicy, RetentionRun
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)

DATASETS = {
    "source_records": (SourceRecord, SourceRecord.received_at),
    "canonical_transactions": (CanonicalTransaction, CanonicalTransaction.created_at),
    "audit_events": (AuditEvent, AuditEvent.created_at),
}
MAX_RETENTION_BATCH = 5000
MAX_RETENTION_ARCHIVE_BYTES = 64 * 1024 * 1024


async def lock_retention_policy(
    db: AsyncSession, dataset: str, *, shared: bool = False
) -> RetentionPolicy | None:
    """Lock the policy until a run or policy update completes its transaction."""
    query = (
        select(RetentionPolicy)
        .where(RetentionPolicy.dataset == dataset)
        .with_for_update(read=shared)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(query)).scalar_one_or_none()


def sync_directory(directory: Path) -> None:
    """Persist directory entry changes before an archived record can be purged."""
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_archive_directory(directory: Path) -> None:
    """Create missing archive directories and persist each new parent entry."""
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(exist_ok=True)
        sync_directory(path.parent)
    if not missing:
        sync_directory(directory.parent)


class RetentionArchiveWriter:
    """Write a JSONL archive incrementally and publish it only after it is complete."""

    def __init__(self, path: Path):
        self.path = path
        self.temporary_path: Path | None = None
        self.archive = None
        self.digest = hashlib.sha256()
        self.record_count = 0
        self.bytes_written = 0

    def __enter__(self):
        return self

    def write(self, row: dict) -> bool:
        contents = (json.dumps(row, sort_keys=True) + "\n").encode()
        if self.bytes_written + len(contents) > MAX_RETENTION_ARCHIVE_BYTES:
            if self.record_count == 0:
                raise HTTPException(
                    status_code=413,
                    detail="A retention record exceeds the per-run archive size limit",
                )
            return False
        if self.archive is None:
            ensure_archive_directory(self.path.parent)
            self.archive = tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=".retention-", delete=False
            )
            self.temporary_path = Path(self.archive.name)
        self.archive.write(contents)
        self.digest.update(contents)
        self.record_count += 1
        self.bytes_written += len(contents)
        return True

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.archive is not None:
                try:
                    if exc_type is None:
                        self.archive.flush()
                        os.fsync(self.archive.fileno())
                finally:
                    self.archive.close()
            if exc_type is None and self.temporary_path is not None:
                os.replace(self.temporary_path, self.path)
                sync_directory(self.path.parent)
        finally:
            if self.temporary_path is not None:
                self.temporary_path.unlink(missing_ok=True)


def serialise_row(row) -> dict:
    return {
        key: base64.b64encode(value).decode()
        if isinstance(value, bytes)
        else value.isoformat()
        if isinstance(value, (date, datetime, time))
        else str(value)
        if isinstance(value, Decimal)
        else value
        for key, value in row.items()
    }


async def stream_retention_rows(
    db: AsyncSession, query, path: Path | None
) -> tuple[list[str], str | None, int, bool]:
    """Stream one bounded retention batch; keep only record IDs in memory."""
    result = await db.stream(query)
    record_ids = []

    async def consume(archive: RetentionArchiveWriter | None) -> bool:
        async for row in result:
            if len(record_ids) == MAX_RETENTION_BATCH:
                return True
            if archive:
                if not archive.write(serialise_row(row._mapping)):
                    return True
                record_ids.append(row._mapping["id"])
            else:
                record_ids.append(row[0])
            if len(record_ids) % 100 == 0:
                await asyncio.sleep(0)
        return False

    try:
        if path:
            with RetentionArchiveWriter(path) as archive:
                has_more = await consume(archive)
                digest = archive.digest.hexdigest()
        else:
            has_more = await consume(None)
            digest = None
        return record_ids, digest, len(record_ids), has_more
    finally:
        await result.close()


async def canonical_transaction_has_dependents(db: AsyncSession, record_ids: list[str]) -> bool:
    """Protect retained canonical details and return lineage from parent deletion."""
    references = (
        select(CanonicalTransaction.id).where(
            CanonicalTransaction.original_transaction_id.in_(record_ids)
        ),
        select(TransactionAdjustment.id).where(
            TransactionAdjustment.transaction_id.in_(record_ids)
        ),
        select(TransactionDiscount.id).where(TransactionDiscount.transaction_id.in_(record_ids)),
        select(TransactionLine.id).where(TransactionLine.transaction_id.in_(record_ids)),
        select(TransactionTax.id).where(TransactionTax.transaction_id.in_(record_ids)),
        select(TransactionTender.id).where(TransactionTender.transaction_id.in_(record_ids)),
    )
    for query in references:
        if (await db.execute(query.limit(1))).first() is not None:
            return True
    return False


async def source_record_has_dependents(db: AsyncSession, record_ids: list[str]) -> bool:
    """Protect source payloads still referenced by canonical transaction lineage."""
    query = (
        select(CanonicalTransaction.id)
        .where(CanonicalTransaction.source_record_id.in_(record_ids))
        .limit(1)
    )
    return (await db.execute(query)).first() is not None


async def retention_candidates(db: AsyncSession, model, timestamp, cutoff):
    """Select one deterministic, locked batch of expired records."""
    query = (
        select(model.id)
        .where(timestamp < cutoff)
        .order_by(timestamp, model.id)
        .limit(MAX_RETENTION_BATCH + 1)
        .with_for_update(skip_locked=True)
    )
    return await stream_retention_rows(db, query, None)


async def ensure_retention_records_are_unreferenced(
    db: AsyncSession, dataset: str, record_ids: list[str]
) -> None:
    """Protect parent records whose lineage or transaction details remain in use."""
    if dataset == "canonical_transactions" and await canonical_transaction_has_dependents(
        db, record_ids
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot purge canonical transactions while return links, adjustments, or transaction details refer to them",
        )
    if dataset == "source_records" and await source_record_has_dependents(db, record_ids):
        raise HTTPException(
            status_code=409,
            detail="Cannot purge source records while canonical transactions reference their source lineage.",
        )


async def archive_retention_candidates(
    db: AsyncSession,
    dataset: str,
    model,
    timestamp,
    record_ids: list[str],
) -> tuple[Path, str, list[str], bool]:
    """Archive the selected records and verify the archive matches its locked batch."""
    archive_dir = Path(os.getenv("NIMBUS_ARCHIVE_DIR", "/app/archives")) / dataset
    archive_path = (
        archive_dir / f"{utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl"
    )
    try:
        columns = list(model.__table__.columns)
        archive_query = (
            select(*columns).where(model.id.in_(record_ids)).order_by(timestamp, model.id)
        )
        archived_ids, archive_hash, archived_count, has_more = await stream_retention_rows(
            db, archive_query, archive_path
        )
        if (
            not archived_ids
            or archived_ids != record_ids[: len(archived_ids)]
            or archived_count != len(archived_ids)
            or (len(archived_ids) < len(record_ids) and not has_more)
        ):
            raise HTTPException(
                status_code=409,
                detail="Retention candidates changed while preparing the archive; retry the operation",
            )
        return archive_path, archive_hash, archived_ids, has_more
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise


async def purge_retention_candidates(db: AsyncSession, model, record_ids: list[str]) -> int:
    """Delete only the selected IDs and return the database's affected row count."""
    result = await db.execute(
        delete(model).where(model.id.in_(record_ids)).execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def audit_retention_run(db: AsyncSession, run: RetentionRun, dataset: str) -> None:
    """Record every completed retention run, including runs with no eligible data."""
    audit(
        db,
        "RETENTION_RUN",
        None,
        "RetentionRun",
        run.id,
        f"Retention {dataset}: archived {run.archived_records}, purged {run.purged_records}",
        actor=run.performed_by,
        after={"archive_hash": run.archive_hash},
    )

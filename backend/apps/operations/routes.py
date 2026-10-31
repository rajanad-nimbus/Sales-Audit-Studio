"""Production operations controls: retention policy and safe previews."""
import base64
import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.security.authentication import require_role
from database import get_db
from apps.audit.models import AuditEvent
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from apps.ingest.models import SourceRecord
from apps.corrections.models import TransactionAdjustment
from model_base import utcnow
from apps.operations.models import RetentionPolicy, RetentionRun
from integrations.ontology.nimbus import audit

router = APIRouter(prefix="/api/operations", tags=["Production operations"])
DATASETS = {"source_records": (SourceRecord, SourceRecord.received_at), "canonical_transactions": (CanonicalTransaction, CanonicalTransaction.created_at), "audit_events": (AuditEvent, AuditEvent.created_at)}
MAX_RETENTION_BATCH = 5000


class RetentionArchiveWriter:
    """Write a JSONL archive incrementally and publish it only after it is complete."""

    def __init__(self, path: Path):
        self.path = path
        self.temporary_path: Path | None = None
        self.archive = None
        self.digest = hashlib.sha256()
        self.record_count = 0

    def __enter__(self):
        return self

    def write(self, row: dict) -> None:
        if self.archive is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.archive = tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".retention-", delete=False)
            self.temporary_path = Path(self.archive.name)
        contents = (json.dumps(row, sort_keys=True) + "\n").encode()
        self.archive.write(contents)
        self.digest.update(contents)
        self.record_count += 1

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
        finally:
            if self.temporary_path is not None:
                self.temporary_path.unlink(missing_ok=True)


def serialise_row(row) -> dict:
    return {
        key: base64.b64encode(value).decode() if isinstance(value, bytes)
        else value.isoformat() if isinstance(value, (date, datetime, time))
        else str(value) if isinstance(value, Decimal)
        else value
        for key, value in row.items()
    }


async def stream_retention_rows(db: AsyncSession, query, path: Path | None) -> tuple[list[str], str | None, int, bool]:
    """Stream one bounded retention batch; keep only record IDs in memory."""
    result = await db.stream(query)
    record_ids = []

    async def consume(archive: RetentionArchiveWriter | None) -> bool:
        async for row in result:
            if len(record_ids) == MAX_RETENTION_BATCH:
                return True
            if archive:
                archive.write(serialise_row(row._mapping))
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
        select(CanonicalTransaction.id).where(CanonicalTransaction.original_transaction_id.in_(record_ids)),
        select(TransactionAdjustment.id).where(TransactionAdjustment.transaction_id.in_(record_ids)),
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
    query = select(CanonicalTransaction.id).where(CanonicalTransaction.source_record_id.in_(record_ids)).limit(1)
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


async def ensure_retention_records_are_unreferenced(db: AsyncSession, dataset: str, record_ids: list[str]) -> None:
    """Protect parent records whose lineage or transaction details remain in use."""
    if dataset == "canonical_transactions" and await canonical_transaction_has_dependents(db, record_ids):
        raise HTTPException(
            status_code=409,
            detail="Cannot purge canonical transactions while return links, adjustments, or transaction details refer to them",
        )
    if dataset == "source_records" and await source_record_has_dependents(db, record_ids):
        raise HTTPException(
            status_code=409,
            detail="Cannot purge source records while canonical transactions reference their source lineage.",
        )


async def archive_retention_candidates(db: AsyncSession, dataset: str, model, timestamp, record_ids: list[str], expected_count: int):
    """Archive the selected records and verify the archive matches its locked batch."""
    archive_dir = Path(os.getenv("NIMBUS_ARCHIVE_DIR", "/app/archives")) / dataset
    archive_path = archive_dir / f"{utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl"
    columns = list(model.__table__.columns)
    archive_query = select(*columns).where(model.id.in_(record_ids)).order_by(timestamp, model.id)
    archived_ids, archive_hash, archived_count, _ = await stream_retention_rows(
        db, archive_query, archive_path
    )
    if archived_ids != record_ids[:len(archived_ids)] or archived_count != expected_count:
        raise HTTPException(
            status_code=409,
            detail="Retention candidates changed while preparing the archive; retry the operation",
        )
    return archive_path, archive_hash


async def purge_retention_candidates(db: AsyncSession, model, record_ids: list[str]) -> int:
    """Delete only the selected IDs and return the database's affected row count."""
    result = await db.execute(
        delete(model).where(model.id.in_(record_ids)).execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


class RetentionBody(BaseModel):
    retain_days: int
    archive_before_purge: bool = True
def view(row: RetentionPolicy): return {"id":row.id,"dataset":row.dataset,"retain_days":row.retain_days,"archive_before_purge":row.archive_before_purge,"updated_by":row.updated_by,"updated_at":row.updated_at}
@router.get("/retention")
async def list_retention(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))): return [view(row) for row in (await db.execute(select(RetentionPolicy).order_by(RetentionPolicy.dataset))).scalars().all()]
@router.put("/retention/{dataset}")
async def set_retention(dataset: str, body: RetentionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    if dataset not in DATASETS or body.retain_days < 30: raise HTTPException(status_code=422, detail="Use a supported dataset and retain at least 30 days")
    if dataset == "audit_events":
        raise HTTPException(status_code=409, detail="Audit events are append-only and hash-chained; retention policies cannot be changed.")
    row = (await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))).scalar_one_or_none()
    if not row: row = RetentionPolicy(id=str(uuid.uuid4()), dataset=dataset, retain_days=body.retain_days, archive_before_purge=body.archive_before_purge); db.add(row)
    row.retain_days, row.archive_before_purge, row.updated_by = body.retain_days, body.archive_before_purge, user["user"] or "ontology.user"
    await db.commit(); await db.refresh(row); return view(row)
@router.get("/retention/{dataset}/preview")
async def preview_retention(dataset: str, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    if dataset not in DATASETS: raise HTTPException(status_code=404, detail="Unknown retention dataset")
    policy = (await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))).scalar_one_or_none()
    if not policy: raise HTTPException(status_code=404, detail="Configure retention policy first")
    model, timestamp = DATASETS[dataset]; cutoff = utcnow() - timedelta(days=policy.retain_days)
    count = (await db.execute(select(func.count()).select_from(model).where(timestamp < cutoff))).scalar()
    return {"dataset":dataset,"cutoff":cutoff,"eligible_records":count,"archive_before_purge":policy.archive_before_purge,"mode":"preview-only","retention_supported":dataset != "audit_events"}

class RetentionRunBody(BaseModel):
    confirm_purge: bool = False

@router.get("/retention/runs")
async def retention_runs(limit: Annotated[int, Query(ge=1, le=200)] = 50, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    rows = (await db.execute(select(RetentionRun).order_by(RetentionRun.created_at.desc()).limit(min(limit, 200)))).scalars().all()
    return [{"id": r.id, "dataset": r.dataset, "retain_days": r.retain_days, "eligible_records": r.eligible_records, "archived_records": r.archived_records, "purged_records": r.purged_records, "archive_location": r.archive_location, "archive_hash": r.archive_hash, "status": r.status, "error": r.error, "performed_by": r.performed_by, "created_at": r.created_at} for r in rows]

@router.post("/retention/{dataset}/run")
async def execute_retention(dataset: str, body: RetentionRunBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    """Archive eligible records, then purge only when explicitly confirmed."""
    if dataset not in DATASETS: raise HTTPException(status_code=404, detail="Unknown retention dataset")
    if dataset == "audit_events":
        raise HTTPException(
            status_code=409,
            detail="Audit events are append-only and hash-chained; retention runs are disabled. Use the audit export to create a copy.",
        )
    policy = (await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))).scalar_one_or_none()
    if not policy: raise HTTPException(status_code=409, detail="Configure retention policy before running it")
    model, timestamp = DATASETS[dataset]; cutoff = utcnow() - timedelta(days=policy.retain_days)
    record_ids, _, eligible_count, has_more = await retention_candidates(db, model, timestamp, cutoff)
    run = RetentionRun(id=str(uuid.uuid4()), dataset=dataset, retain_days=policy.retain_days, eligible_records=eligible_count, archived_records=0, purged_records=0, status="Succeeded", performed_by=user["user"] or "ontology.user")
    if not record_ids:
        db.add(run); await db.commit(); return {"run_id":run.id,"eligible_records":0,"message":"No records eligible"}
    if body.confirm_purge:
        await ensure_retention_records_are_unreferenced(db, dataset, record_ids)
    archive_path = None
    archive_hash = None
    if policy.archive_before_purge:
        archive_path, archive_hash = await archive_retention_candidates(
            db, dataset, model, timestamp, record_ids, eligible_count
        )
    if archive_path:
        run.archived_records, run.archive_location, run.archive_hash = eligible_count, str(archive_path), archive_hash
    if body.confirm_purge:
        run.purged_records = await purge_retention_candidates(db, model, record_ids)
    db.add(run)
    audit(db, "RETENTION_RUN", None, "RetentionRun", run.id, f"Retention {dataset}: archived {run.archived_records}, purged {run.purged_records}", actor=run.performed_by, after={"archive_hash":run.archive_hash})
    await db.commit()
    return {"run_id":run.id,"eligible_records":eligible_count,"archived_records":run.archived_records,"purged_records":run.purged_records,"archive_location":run.archive_location,"archive_hash":run.archive_hash,"purge_confirmed":body.confirm_purge,"more_eligible":has_more}

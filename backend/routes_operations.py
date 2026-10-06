"""Production operations controls: retention policy and safe previews."""
import uuid
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from auth import require_role
from database import get_db
from models import AuditEvent, CanonicalTransaction, RetentionPolicy, SourceRecord, utcnow
from nimbus import audit

router = APIRouter(prefix="/api/operations", tags=["Production operations"])
DATASETS = {"source_records": (SourceRecord, SourceRecord.received_at), "canonical_transactions": (CanonicalTransaction, CanonicalTransaction.created_at), "audit_events": (AuditEvent, AuditEvent.created_at)}
class RetentionBody(BaseModel):
    retain_days: int
    archive_before_purge: bool = True
def view(row: RetentionPolicy): return {"id":row.id,"dataset":row.dataset,"retain_days":row.retain_days,"archive_before_purge":row.archive_before_purge,"updated_by":row.updated_by,"updated_at":row.updated_at}
@router.get("/retention")
async def list_retention(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))): return [view(row) for row in (await db.execute(select(RetentionPolicy).order_by(RetentionPolicy.dataset))).scalars().all()]
@router.put("/retention/{dataset}")
async def set_retention(dataset: str, body: RetentionBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    if dataset not in DATASETS or body.retain_days < 30: raise HTTPException(status_code=422, detail="Use a supported dataset and retain at least 30 days")
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
    return {"dataset":dataset,"cutoff":cutoff,"eligible_records":count,"archive_before_purge":policy.archive_before_purge,"mode":"preview-only"}

class RetentionRunBody(BaseModel):
    confirm_purge: bool = False

@router.get("/retention/runs")
async def retention_runs(limit: int = 50, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    from models import RetentionRun
    rows = (await db.execute(select(RetentionRun).order_by(RetentionRun.created_at.desc()).limit(min(limit, 200)))).scalars().all()
    return [{"id": r.id, "dataset": r.dataset, "retain_days": r.retain_days, "eligible_records": r.eligible_records, "archived_records": r.archived_records, "purged_records": r.purged_records, "archive_location": r.archive_location, "archive_hash": r.archive_hash, "status": r.status, "error": r.error, "performed_by": r.performed_by, "created_at": r.created_at} for r in rows]

@router.post("/retention/{dataset}/run")
async def execute_retention(dataset: str, body: RetentionRunBody, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))):
    """Archive eligible records, then purge only when explicitly confirmed."""
    import base64, hashlib, json, os
    from pathlib import Path
    from models import RetentionRun
    if dataset not in DATASETS: raise HTTPException(status_code=404, detail="Unknown retention dataset")
    policy = (await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))).scalar_one_or_none()
    if not policy: raise HTTPException(status_code=409, detail="Configure retention policy before running it")
    model, timestamp = DATASETS[dataset]; cutoff = utcnow() - timedelta(days=policy.retain_days)
    rows = (await db.execute(select(model).where(timestamp < cutoff).limit(5000))).scalars().all()
    run = RetentionRun(id=str(uuid.uuid4()), dataset=dataset, retain_days=policy.retain_days, eligible_records=len(rows), archived_records=0, purged_records=0, status="Succeeded", performed_by=user["user"] or "ontology.user")
    if not rows:
        db.add(run); await db.commit(); return {"run_id":run.id,"eligible_records":0,"message":"No records eligible"}
    if policy.archive_before_purge:
        archive_dir = Path(os.getenv("NIMBUS_ARCHIVE_DIR", "/app/archives")) / dataset
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{utcnow().strftime('%Y%m%dT%H%M%S')}-{run.id[:8]}.jsonl"
        def serialise(row):
            data = {}
            for col in row.__table__.columns:
                value = getattr(row, col.name)
                data[col.name] = base64.b64encode(value).decode() if isinstance(value, bytes) else str(value) if value is not None else None
            return data
        blob = ("\n".join(json.dumps(serialise(row), sort_keys=True) for row in rows) + "\n").encode()
        archive_path.write_bytes(blob); run.archived_records, run.archive_location, run.archive_hash = len(rows), str(archive_path), hashlib.sha256(blob).hexdigest()
    if body.confirm_purge:
        for row in rows: await db.delete(row)
        run.purged_records = len(rows)
    db.add(run)
    audit(db, "RETENTION_RUN", None, "RetentionRun", run.id, f"Retention {dataset}: archived {run.archived_records}, purged {run.purged_records}", actor=run.performed_by, after={"archive_hash":run.archive_hash})
    await db.commit()
    return {"run_id":run.id,"eligible_records":len(rows),"archived_records":run.archived_records,"purged_records":run.purged_records,"archive_location":run.archive_location,"archive_hash":run.archive_hash,"purge_confirmed":body.confirm_purge}

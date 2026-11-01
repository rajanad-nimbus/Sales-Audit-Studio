"""HTTP endpoints for production operations and retention controls."""

import uuid
from datetime import timedelta
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.operations.models import RetentionPolicy, RetentionRun
from apps.operations.retention import (
    DATASETS,
    archive_retention_candidates,
    audit_retention_run,
    ensure_retention_records_are_unreferenced,
    purge_retention_candidates,
    retention_candidates,
)
from apps.security.authentication import require_role

router = APIRouter(prefix="/api/operations", tags=["Production operations"])


class RetentionBody(BaseModel):
    retain_days: int
    archive_before_purge: bool = True


def view(row: RetentionPolicy) -> dict:
    return {
        "id": row.id,
        "dataset": row.dataset,
        "retain_days": row.retain_days,
        "archive_before_purge": row.archive_before_purge,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


@router.get("/retention")
async def list_retention(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it", "finance")),
) -> list[dict]:
    rows = (
        (await db.execute(select(RetentionPolicy).order_by(RetentionPolicy.dataset)))
        .scalars()
        .all()
    )
    return [view(row) for row in rows]


@router.put("/retention/{dataset}")
async def set_retention(
    dataset: str,
    body: RetentionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
) -> dict:
    if dataset not in DATASETS or body.retain_days < 30:
        raise HTTPException(
            status_code=422,
            detail="Use a supported dataset and retain at least 30 days",
        )
    if dataset == "audit_events":
        raise HTTPException(
            status_code=409,
            detail="Audit events are append-only and hash-chained; retention policies cannot be changed.",
        )
    row = (
        await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))
    ).scalar_one_or_none()
    previous_settings = (
        {
            "retain_days": row.retain_days,
            "archive_before_purge": row.archive_before_purge,
        }
        if row is not None
        else None
    )
    if row is None:
        row = RetentionPolicy(
            id=str(uuid.uuid4()),
            dataset=dataset,
            retain_days=body.retain_days,
            archive_before_purge=body.archive_before_purge,
        )
        db.add(row)
    row.retain_days = body.retain_days
    row.archive_before_purge = body.archive_before_purge
    row.updated_by = user["user"] or "ontology.user"
    audit(
        db,
        "RETENTION_POLICY_UPDATED" if previous_settings is not None else "RETENTION_POLICY_CREATED",
        None,
        "RetentionPolicy",
        row.id,
        f"Retention policy for {dataset}: keep {body.retain_days} days",
        actor=row.updated_by,
        after={
            "previous_settings": previous_settings,
            "retain_days": row.retain_days,
            "archive_before_purge": row.archive_before_purge,
        },
    )
    await db.commit()
    await db.refresh(row)
    return view(row)


@router.get("/retention/{dataset}/preview")
async def preview_retention(
    dataset: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
) -> dict:
    if dataset not in DATASETS:
        raise HTTPException(status_code=404, detail="Unknown retention dataset")
    policy = (
        await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Configure retention policy first")
    model, timestamp = DATASETS[dataset]
    cutoff = utcnow() - timedelta(days=policy.retain_days)
    count = (
        await db.execute(select(func.count()).select_from(model).where(timestamp < cutoff))
    ).scalar()
    return {
        "dataset": dataset,
        "cutoff": cutoff,
        "eligible_records": count,
        "archive_before_purge": policy.archive_before_purge,
        "mode": "preview-only",
        "retention_supported": dataset != "audit_events",
    }


class RetentionRunBody(BaseModel):
    confirm_purge: bool = False


@router.get("/retention/runs")
async def retention_runs(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("it")),
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(RetentionRun).order_by(RetentionRun.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "dataset": row.dataset,
            "retain_days": row.retain_days,
            "eligible_records": row.eligible_records,
            "archived_records": row.archived_records,
            "purged_records": row.purged_records,
            "archive_location": row.archive_location,
            "archive_hash": row.archive_hash,
            "status": row.status,
            "error": row.error,
            "performed_by": row.performed_by,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/retention/{dataset}/run")
async def execute_retention(
    dataset: str,
    body: RetentionRunBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
) -> dict:
    """Archive eligible records, then purge only when explicitly confirmed."""
    if dataset not in DATASETS:
        raise HTTPException(status_code=404, detail="Unknown retention dataset")
    if dataset == "audit_events":
        raise HTTPException(
            status_code=409,
            detail="Audit events are append-only and hash-chained; retention runs are disabled. Use the audit export to create a copy.",
        )
    policy = (
        await db.execute(select(RetentionPolicy).where(RetentionPolicy.dataset == dataset))
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=409, detail="Configure retention policy before running it")
    model, timestamp = DATASETS[dataset]
    cutoff = utcnow() - timedelta(days=policy.retain_days)
    candidate_ids, _, eligible_count, has_more = await retention_candidates(
        db, model, timestamp, cutoff
    )
    run = RetentionRun(
        id=str(uuid.uuid4()),
        dataset=dataset,
        retain_days=policy.retain_days,
        eligible_records=eligible_count,
        archived_records=0,
        purged_records=0,
        status="Succeeded",
        performed_by=user["user"] or "ontology.user",
    )
    if not candidate_ids:
        db.add(run)
        audit_retention_run(db, run, dataset)
        await db.commit()
        return {"run_id": run.id, "eligible_records": 0, "message": "No records eligible"}
    archive_path = None
    archive_hash = None
    try:
        if policy.archive_before_purge:
            (
                archive_path,
                archive_hash,
                record_ids,
                archive_has_more,
            ) = await archive_retention_candidates(db, dataset, model, timestamp, candidate_ids)
            eligible_count = len(record_ids)
            has_more = has_more or archive_has_more
        else:
            record_ids = candidate_ids
        run.eligible_records = eligible_count
        if body.confirm_purge:
            await ensure_retention_records_are_unreferenced(db, dataset, record_ids)
        if archive_path is not None:
            run.archived_records = eligible_count
            run.archive_location = str(archive_path)
            run.archive_hash = archive_hash
        if body.confirm_purge:
            run.purged_records = await purge_retention_candidates(db, model, record_ids)
        db.add(run)
        audit_retention_run(db, run, dataset)
        await db.commit()
    except BaseException:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        raise
    return {
        "run_id": run.id,
        "eligible_records": eligible_count,
        "archived_records": run.archived_records,
        "purged_records": run.purged_records,
        "archive_location": run.archive_location,
        "archive_hash": run.archive_hash,
        "purge_confirmed": body.confirm_purge,
        "more_eligible": has_more,
    }

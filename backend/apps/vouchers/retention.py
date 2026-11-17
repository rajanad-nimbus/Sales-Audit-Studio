"""Preview, archive and purge settled vouchers while reserving their identities."""

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from integrations.ontology.nimbus import audit
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.csv_export import csv_table
from apps.operations.models import RetentionRun
from apps.operations.retention import (
    archive_retention_candidates,
    audit_retention_run,
    purge_retention_candidates,
    serialise_row,
)
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.vouchers.models import Voucher, VoucherIdentity, VoucherOption

router = APIRouter()
logger = logging.getLogger(__name__)
BATCH_SIZE = 500
RETIRED_VOUCHER_EXPORT_MAX_ROWS = 50_000
READ_ROLES = ("analyst", "finance", "it", "auditor")


async def candidates(db: AsyncSession, tender: str, user: dict, *, lock: bool = False):
    option = (
        await db.execute(
            select(VoucherOption)
            .where(func.lower(VoucherOption.tender_type) == tender.strip().lower())
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if option is None or option.purge_days is None:
        raise HTTPException(
            status_code=409, detail="Configure a purge window for this tender first"
        )
    now = utcnow().date()
    cutoff = (now - timedelta(days=option.purge_days)).isoformat()
    filters = (
        func.lower(Voucher.tender_type) == option.tender_type.lower(),
        Voucher.issued_on <= cutoff,
        or_(
            and_(Voucher.status == "Redeemed", Voucher.redeemed_on <= now.isoformat()),
            and_(Voucher.status == "Escheated", Voucher.escheated_on <= now.isoformat()),
        ),
    )
    query = scope.restrict(select(Voucher).where(*filters), user, Voucher.issued_store_id)
    query = query.order_by(Voucher.issued_on, Voucher.id).limit(BATCH_SIZE + 1)
    if lock:
        query = query.with_for_update()
    rows = (await db.execute(query.execution_options(populate_existing=True))).scalars().all()
    count = await db.scalar(
        scope.restrict(
            select(func.count()).select_from(Voucher).where(*filters),
            user,
            Voucher.issued_store_id,
        )
    )
    rows = rows[:BATCH_SIZE]
    token_context = {
        "as_of": now.isoformat(),
        "tender_type": option.tender_type,
        "purge_days": option.purge_days,
        "records": [
            serialise_row(
                {column.name: getattr(row, column.name) for column in Voucher.__table__.columns}
            )
            for row in rows
        ],
    }
    token = hashlib.sha256(
        json.dumps(token_context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return option, rows, count, token


@router.get("/retention/preview")
async def preview_voucher_retention(
    tender_type: Annotated[str, Query(min_length=1, max_length=100)],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    option, rows, count, token = await candidates(db, tender_type, user)
    return {
        "tender_type": option.tender_type,
        "purge_days": option.purge_days,
        "eligible_records": count,
        "batch_size": len(rows),
        "more_eligible": count > len(rows),
        "preview_token": token,
        "vouchers": [
            {
                "voucher_number": row.voucher_number,
                "store_id": row.issued_store_id,
                "issued_on": row.issued_on,
                "status": row.status,
                "finalized_on": row.redeemed_on if row.status == "Redeemed" else row.escheated_on,
                "amount": row.amount,
                "currency": row.currency,
            }
            for row in rows
        ],
    }


class VoucherRetentionBody(BaseModel):
    tender_type: str = Field(min_length=1, max_length=100)
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_purge: bool = False

    @field_validator("tender_type")
    @classmethod
    def clean_tender(cls, value):
        if not value.strip():
            raise ValueError("Tender type cannot be blank")
        return value.strip()


@router.post("/retention/run")
async def execute_voucher_retention(
    body: VoucherRetentionBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    if scope.scoped(user):
        raise HTTPException(
            status_code=403, detail="Voucher retention requires access to all stores"
        )
    await lock_configuration(db)
    option, rows, count, token = await candidates(db, body.tender_type, user, lock=True)
    if token != body.preview_token:
        raise HTTPException(
            status_code=409, detail="Voucher retention candidates or policy changed. Preview again."
        )
    identities = {}
    if body.confirm_purge and rows:
        identities = {
            identity.voucher_id: identity
            for identity in (
                await db.execute(
                    select(VoucherIdentity)
                    .where(VoucherIdentity.voucher_id.in_([row.id for row in rows]))
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        }
        if any(
            row.id not in identities
            or identities[row.id].voucher_number != row.voucher_number
            or identities[row.id].retired_at is not None
            for row in rows
        ):
            raise HTTPException(
                status_code=409,
                detail="Voucher identity records are inconsistent; reconcile them before purging",
            )
    run = RetentionRun(
        id=str(uuid.uuid4()),
        dataset="vouchers",
        retain_days=option.purge_days,
        eligible_records=len(rows),
        archived_records=0,
        purged_records=0,
        status="Succeeded",
        performed_by=user["user"] or "ontology.user",
    )
    archive_path = None
    commit_started = False
    run_id = run.id
    has_more = count > len(rows)
    try:
        record_ids = [row.id for row in rows]
        if rows:
            (
                archive_path,
                archive_hash,
                record_ids,
                archive_has_more,
            ) = await archive_retention_candidates(
                db,
                "vouchers",
                Voucher,
                Voucher.issued_on,
                record_ids,
            )
            has_more = has_more or archive_has_more
            run.archived_records = len(record_ids)
            run.archive_location = str(archive_path)
            run.archive_hash = archive_hash
        db.add(run)
        await db.flush()
        if body.confirm_purge and record_ids:
            by_id = {row.id: row for row in rows}
            for identifier in record_ids:
                row, identity = by_id[identifier], identities[identifier]
                identity.retired_at = utcnow()
                identity.final_status = row.status
                identity.finalized_on = (
                    row.redeemed_on if row.status == "Redeemed" else row.escheated_on
                )
                identity.retention_run_id = run.id
            await db.flush()
            run.purged_records = await purge_retention_candidates(db, Voucher, record_ids)
            if run.purged_records != len(record_ids):
                raise HTTPException(
                    status_code=409, detail="Voucher candidates changed during purge; preview again"
                )
        audit_retention_run(db, run, "vouchers")
        audit(
            db,
            "VOUCHER_RETENTION_RUN",
            None,
            "RetentionRun",
            run.id,
            f"Voucher retention for {option.tender_type}: archived {run.archived_records}, purged {run.purged_records}",
            actor=run.performed_by,
            after={
                "tender_type": option.tender_type,
                "purge_days": option.purge_days,
                "voucher_numbers": [row.voucher_number for row in rows if row.id in record_ids],
                "archive_hash": run.archive_hash,
                "purge_confirmed": body.confirm_purge,
            },
        )
        await db.flush()
        commit_started = True
        await db.commit()
    except BaseException:
        await db.rollback()
        if archive_path is not None:
            if commit_started:
                logger.warning(
                    "Voucher retention commit outcome unknown for run %s; archive preserved at %s",
                    run_id,
                    archive_path,
                    exc_info=True,
                )
            else:
                archive_path.unlink(missing_ok=True)
        raise
    return {
        "run_id": run.id,
        "eligible_records": len(rows),
        "archived_records": run.archived_records,
        "purged_records": run.purged_records,
        "archive_hash": run.archive_hash,
        "more_eligible": has_more,
        "purge_confirmed": body.confirm_purge,
    }


@router.get("/retired")
async def list_retired_vouchers(
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor", "store_manager")),
):
    query = scope.restrict(
        select(VoucherIdentity).where(VoucherIdentity.retired_at.is_not(None)),
        user,
        VoucherIdentity.issued_store_id,
    )
    if store_id:
        query = query.where(VoucherIdentity.issued_store_id == store_id)
    if q:
        query = query.where(VoucherIdentity.voucher_number.contains(q, autoescape=True))
    rows = (
        (
            await db.execute(
                query.order_by(
                    VoucherIdentity.retired_at.desc(), VoucherIdentity.voucher_number
                ).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "voucher_number": row.voucher_number,
            "issued_store_id": row.issued_store_id,
            "tender_type": row.tender_type,
            "issued_on": row.issued_on,
            "amount": row.amount,
            "currency": row.currency,
            "final_status": row.final_status,
            "finalized_on": row.finalized_on,
            "retired_at": row.retired_at,
            "retention_run_id": row.retention_run_id,
        }
        for row in rows
    ]


@router.get("/retired/export.csv")
async def export_retired_vouchers(
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor", "store_manager")),
):
    """Export matching archived voucher identities without archived source payloads."""
    query = scope.restrict(
        select(VoucherIdentity).where(VoucherIdentity.retired_at.is_not(None)),
        user,
        VoucherIdentity.issued_store_id,
    )
    if store_id:
        query = query.where(VoucherIdentity.issued_store_id == store_id)
    if q:
        query = query.where(VoucherIdentity.voucher_number.contains(q, autoescape=True))
    found = list(
        (
            await db.execute(
                query.order_by(
                    VoucherIdentity.retired_at.desc(), VoucherIdentity.voucher_number
                ).limit(RETIRED_VOUCHER_EXPORT_MAX_ROWS + 1)
            )
        )
        .scalars()
        .all()
    )
    truncated = len(found) > RETIRED_VOUCHER_EXPORT_MAX_ROWS
    columns = [
        "Voucher number",
        "Issued store",
        "Tender type",
        "Issued on",
        "Amount",
        "Currency",
        "Final status",
        "Finalized on",
        "Retired at",
        "Retention run ID",
    ]
    rows = [
        [
            row.voucher_number,
            row.issued_store_id,
            row.tender_type,
            row.issued_on,
            row.amount,
            row.currency,
            row.final_status,
            row.finalized_on,
            row.retired_at,
            row.retention_run_id,
        ]
        for row in found[:RETIRED_VOUCHER_EXPORT_MAX_ROWS]
    ]
    return Response(
        csv_table(columns, rows),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="retired-vouchers.csv"',
            "X-Retired-Voucher-Export-Truncated": "true" if truncated else "false",
        },
    )

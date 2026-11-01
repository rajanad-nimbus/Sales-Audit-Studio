"""Proposal and review workflows for manual reconciliation matches."""

import uuid

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from model_base import utcnow
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.contracts import ManualMatchRequest
from apps.ingest.models import ReconciliationMatch
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.models import CanonicalTransaction

router = APIRouter()


@router.get("/api/reconciliation/matches")
async def list_manual_matches(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    query = select(ReconciliationMatch).order_by(
        ReconciliationMatch.created_at.desc(), ReconciliationMatch.id.desc()
    )
    if status:
        query = query.where(ReconciliationMatch.status == status)
    stores = scope.allowed_stores(user)
    if stores is not None:
        if not stores:
            return []
        query = query.where(ReconciliationMatch.store_id.in_(stores))
    rows = (await db.execute(query.limit(100))).scalars().all()
    return [
        {
            "id": row.id,
            "transaction_ids": row.transaction_ids,
            "match_type": row.match_type,
            "status": row.status,
            "rationale": row.rationale,
            "proposed_by": row.proposed_by,
            "reviewed_by": row.reviewed_by,
            "created_at": row.created_at,
        }
        for row in rows
    ]


async def authorized_match_transactions(
    db: AsyncSession, match: ReconciliationMatch, user: dict, *, lock: bool = False
) -> list[CanonicalTransaction]:
    ids = match.transaction_ids
    if (
        not isinstance(ids, list)
        or len(ids) < 2
        or any(not isinstance(item, str) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise HTTPException(status_code=409, detail="Match transaction references are invalid")
    query = (
        select(CanonicalTransaction)
        .where(CanonicalTransaction.id.in_(ids))
        .order_by(CanonicalTransaction.id)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = (await db.execute(query)).scalars().all()
    if len(rows) != len(ids):
        raise HTTPException(status_code=409, detail="Match source records are no longer available")
    for row in rows:
        scope.require_store(user, row.store_id)
    if len({row.store_id for row in rows}) != 1 or len({row.currency for row in rows}) != 1:
        raise HTTPException(
            status_code=409, detail="Match transactions must use one store and one currency"
        )
    return list(rows)


@router.post("/api/reconciliation/matches")
async def propose_manual_match(
    body: ManualMatchRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    ids = list(dict.fromkeys(body.transaction_ids))
    if len(ids) < 2 or not body.rationale.strip():
        raise HTTPException(
            status_code=422, detail="At least two transactions and a rationale are required"
        )
    rows = (
        (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id.in_(ids))))
        .scalars()
        .all()
    )
    if len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="One or more transactions were not found")
    for row in rows:
        scope.require_store(user, row.store_id)
    if len({row.store_id for row in rows}) != 1 or len({row.currency for row in rows}) != 1:
        raise HTTPException(
            status_code=422, detail="Manual matches must use one store and one currency"
        )
    if any(row.reconciliation_status not in {"Unmatched", "Exception"} for row in rows):
        raise HTTPException(
            status_code=409, detail="One or more transactions have already been reconciled"
        )
    match = ReconciliationMatch(
        id=str(uuid.uuid4()),
        transaction_ids=ids,
        store_id=rows[0].store_id,
        match_type=body.match_type.strip() or "Manual Match",
        rationale=body.rationale.strip(),
        proposed_by=user["user"] or "ontology.user",
    )
    db.add(match)
    audit(
        db,
        "MANUAL_MATCH_PROPOSED",
        None,
        "ReconciliationMatch",
        match.id,
        "Manual reconciliation match proposed",
        actor=match.proposed_by,
    )
    await db.commit()
    return {"id": match.id, "status": match.status}


@router.post("/api/reconciliation/matches/{match_id}/approve")
async def approve_manual_match(
    match_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))
):
    match = (
        await db.execute(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id))
    ).scalar_one_or_none()
    if not match or match.status != "Proposed":
        raise HTTPException(status_code=409, detail="Proposed manual match not found")
    rows = await authorized_match_transactions(db, match, user, lock=True)
    if any(row.reconciliation_status not in {"Unmatched", "Exception"} for row in rows):
        raise HTTPException(
            status_code=409, detail="One or more transactions have already been reconciled"
        )
    claimed = (
        await db.execute(
            update(ReconciliationMatch)
            .where(ReconciliationMatch.id == match_id, ReconciliationMatch.status == "Proposed")
            .values(
                status="Approved", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow()
            )
            .returning(ReconciliationMatch.id)
        )
    ).scalar_one_or_none()
    if not claimed:
        raise HTTPException(status_code=409, detail="Match was already reviewed")
    for row in rows:
        row.reconciliation_status, row.disposition = "Manually Matched", "Closed"
    audit(
        db,
        "MANUAL_MATCH_APPROVED",
        None,
        "ReconciliationMatch",
        match.id,
        "Manual reconciliation match approved",
        actor=match.reviewed_by,
    )
    await db.commit()
    return {"id": match.id, "status": match.status}


@router.post("/api/reconciliation/matches/{match_id}/reject")
async def reject_manual_match(
    match_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))
):
    match = (
        await db.execute(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id))
    ).scalar_one_or_none()
    if not match or match.status != "Proposed":
        raise HTTPException(status_code=409, detail="Proposed manual match not found")
    await authorized_match_transactions(db, match, user)
    claimed = (
        await db.execute(
            update(ReconciliationMatch)
            .where(ReconciliationMatch.id == match_id, ReconciliationMatch.status == "Proposed")
            .values(
                status="Rejected", reviewed_by=user["user"] or "ontology.user", reviewed_at=utcnow()
            )
            .returning(ReconciliationMatch.id)
        )
    ).scalar_one_or_none()
    if not claimed:
        raise HTTPException(status_code=409, detail="Match was already reviewed")
    audit(
        db,
        "MANUAL_MATCH_REJECTED",
        None,
        "ReconciliationMatch",
        match.id,
        "Manual reconciliation match rejected",
        actor=match.reviewed_by,
    )
    await db.commit()
    return {"id": match.id, "status": match.status}

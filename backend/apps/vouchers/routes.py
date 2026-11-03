"""Voucher ledger: issue, redeem once, and escheat what expires unclaimed."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.vouchers.models import Voucher

router = APIRouter(prefix="/api/vouchers", tags=["Vouchers"])
MAX_PAGE = 500
READ_ROLES = ("analyst", "finance", "it", "auditor", "store_manager")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def clean_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return validate_business_date(value)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


class IssueBody(BaseModel):
    voucher_number: str = Field(min_length=1, max_length=100)
    store_id: str = Field(min_length=1, max_length=50)
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    issued_on: str | None = Field(default=None, max_length=10)
    expires_on: str | None = Field(default=None, max_length=10)

    @field_validator("voucher_number")
    @classmethod
    def strip_number(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("voucher_number cannot be blank")
        return value.strip()

    @field_validator("issued_on", "expires_on")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        return clean_date(value)


class RedeemBody(BaseModel):
    store_id: str = Field(min_length=1, max_length=50)
    redeemed_on: str | None = Field(default=None, max_length=10)
    transaction_id: str | None = Field(default=None, max_length=36)

    @field_validator("redeemed_on")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        return clean_date(value)


class EscheatBody(BaseModel):
    voucher_numbers: list[str] = Field(min_length=1, max_length=MAX_PAGE)
    note: str | None = Field(default=None, max_length=2000)


def effective_status(row: Voucher, as_of: str) -> str:
    """Issued vouchers past their expiry date read as Expired."""
    if row.status == "Issued" and row.expires_on and row.expires_on < as_of:
        return "Expired"
    return row.status


def view(row: Voucher, as_of: str | None = None) -> dict:
    return {
        "id": row.id,
        "voucher_number": row.voucher_number,
        "issued_store_id": row.issued_store_id,
        "issued_on": row.issued_on,
        "expires_on": row.expires_on,
        "amount": row.amount,
        "currency": row.currency,
        "status": effective_status(row, as_of or today()),
        "redeemed_store_id": row.redeemed_store_id,
        "redeemed_on": row.redeemed_on,
        "redeemed_transaction_id": row.redeemed_transaction_id,
        "escheated_on": row.escheated_on,
    }


async def load(db: AsyncSession, number: str, *, lock: bool = False) -> Voucher:
    query = select(Voucher).where(Voucher.voucher_number == number)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Voucher not found")
    return row


@router.get("")
async def list_vouchers(
    status: Annotated[str | None, Query(pattern="^(Issued|Expired|Redeemed|Escheated)$")] = None,
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    now = today()
    query = scope.restrict(select(Voucher), user, Voucher.issued_store_id)
    if status == "Expired":
        query = query.where(Voucher.status == "Issued", Voucher.expires_on < now)
    elif status == "Issued":
        query = query.where(
            Voucher.status == "Issued",
            (Voucher.expires_on.is_(None)) | (Voucher.expires_on >= now),
        )
    elif status:
        query = query.where(Voucher.status == status)
    if store_id:
        query = query.where(Voucher.issued_store_id == store_id)
    if q:
        query = query.where(Voucher.voucher_number.contains(q))
    rows = (
        (await db.execute(query.order_by(Voucher.created_at.desc(), Voucher.id).limit(limit)))
        .scalars()
        .all()
    )
    return [view(row, now) for row in rows]


@router.post("")
async def issue_voucher(
    body: IssueBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it", "store_manager")),
):
    scope.require_store(user, body.store_id)
    issued_on = body.issued_on or today()
    if body.expires_on and body.expires_on < issued_on:
        raise HTTPException(status_code=422, detail="A voucher cannot expire before it is issued")
    actor = user["user"] or "ontology.user"
    row = Voucher(
        id=str(uuid.uuid4()),
        voucher_number=body.voucher_number,
        issued_store_id=body.store_id,
        issued_on=issued_on,
        expires_on=body.expires_on,
        amount=body.amount,
        currency=body.currency.upper(),
        issued_by=actor,
    )
    db.add(row)
    audit(
        db,
        "VOUCHER_ISSUED",
        None,
        "Voucher",
        row.id,
        f"Voucher {row.voucher_number} issued at {row.issued_store_id} for {row.amount}",
        actor=actor,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That voucher number already exists") from exc
    return view(row)


@router.post("/{voucher_number}/redeem")
async def redeem_voucher(
    voucher_number: str,
    body: RedeemBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it", "store_manager")),
):
    scope.require_store(user, body.store_id)
    row = await load(db, voucher_number, lock=True)
    redeemed_on = body.redeemed_on or today()
    if row.status == "Redeemed":
        raise HTTPException(
            status_code=409,
            detail=f"Already redeemed at {row.redeemed_store_id} on {row.redeemed_on}",
        )
    if row.status == "Escheated":
        raise HTTPException(status_code=409, detail="This voucher has been escheated")
    if row.expires_on and row.expires_on < redeemed_on:
        raise HTTPException(status_code=409, detail=f"This voucher expired on {row.expires_on}")
    actor = user["user"] or "ontology.user"
    row.status, row.redeemed_store_id, row.redeemed_on = "Redeemed", body.store_id, redeemed_on
    row.redeemed_transaction_id, row.redeemed_by = body.transaction_id, actor
    audit(
        db,
        "VOUCHER_REDEEMED",
        None,
        "Voucher",
        row.id,
        f"Voucher {row.voucher_number} redeemed at {body.store_id}",
        actor=actor,
    )
    await db.commit()
    return view(row)


@router.get("/escheat")
async def escheat_report(
    waiting_days: Annotated[int, Query(ge=0, le=3650)] = 0,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    """Vouchers that expired at least waiting_days ago and were never redeemed, by store."""
    cutoff = (date.fromisoformat(today()) - timedelta(days=waiting_days)).isoformat()
    base = (
        select(Voucher)
        .where(Voucher.status == "Issued", Voucher.expires_on < cutoff)
        .order_by(Voucher.expires_on, Voucher.voucher_number)
    )
    rows = (
        (await db.execute(scope.restrict(base, user, Voucher.issued_store_id).limit(MAX_PAGE + 1)))
        .scalars()
        .all()
    )
    by_store: dict[tuple[str, str], dict] = {}
    for row in rows[:MAX_PAGE]:
        entry = by_store.setdefault(
            (row.issued_store_id, row.currency),
            {"store_id": row.issued_store_id, "currency": row.currency, "vouchers": 0, "amount": 0},
        )
        entry["vouchers"] += 1
        entry["amount"] += row.amount
    return {
        "expired_before": cutoff,
        "truncated": len(rows) > MAX_PAGE,
        "by_store": sorted(by_store.values(), key=lambda e: (e["store_id"], e["currency"])),
        "vouchers": [view(row) for row in rows[:MAX_PAGE]],
    }


@router.post("/escheat")
async def escheat_vouchers(
    body: EscheatBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """Hand expired, unredeemed vouchers to the state: they leave the liability for good."""
    if scope.scoped(user):
        raise HTTPException(
            status_code=403, detail="Escheating needs a user who can see all stores"
        )
    now = today()
    numbers = sorted(set(body.voucher_numbers))
    rows = (
        (
            await db.execute(
                select(Voucher)
                .where(Voucher.voucher_number.in_(numbers))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    found = {row.voucher_number: row for row in rows}
    problems = [
        f"{number}: not found"
        if number not in found
        else f"{number}: {effective_status(found[number], now).lower()}, not expired"
        for number in numbers
        if number not in found or effective_status(found[number], now) != "Expired"
    ]
    if problems:
        raise HTTPException(
            status_code=422,
            detail="Only expired, unredeemed vouchers can be escheated. " + "; ".join(problems[:5]),
        )
    actor = user["user"] or "ontology.user"
    total = sum((row.amount for row in rows), Decimal("0"))
    for row in rows:
        row.status, row.escheated_on, row.escheated_by = "Escheated", now, actor
        row.escheat_note = (body.note or "").strip() or None
    audit(
        db,
        "VOUCHERS_ESCHEATED",
        None,
        "Voucher",
        numbers[0],
        f"{len(rows)} expired voucher(s) escheated",
        actor=actor,
        after={"voucher_numbers": numbers, "amount": str(total), "note": body.note},
    )
    await db.commit()
    return {"escheated": len(rows), "amount": total}


@router.get("/summary")
async def voucher_summary(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    """Outstanding liability: issued vouchers that are still redeemable, and those expired unclaimed."""
    now = today()
    query = scope.restrict(
        select(
            Voucher.status,
            Voucher.expires_on,
            Voucher.currency,
            func.count(),
            func.sum(Voucher.amount),
        )
        .where(Voucher.status == "Issued")
        .group_by(Voucher.status, Voucher.expires_on, Voucher.currency),
        user,
        Voucher.issued_store_id,
    )
    out: dict[str, dict] = {}
    for _status, expires_on, currency, count, amount in (await db.execute(query)).all():
        key = "expired" if expires_on and expires_on < now else "outstanding"
        entry = out.setdefault(key, {}).setdefault(currency, {"vouchers": 0, "amount": 0})
        entry["vouchers"] += count
        entry["amount"] += amount
    return {
        "as_of": now,
        "outstanding": out.get("outstanding", {}),
        "expired": out.get("expired", {}),
    }

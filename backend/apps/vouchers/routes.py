"""Voucher ledger and retailer-defined tender aging for Finance escheatment review."""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.csv_export import csv_table
from apps.ingest.manifests import validate_business_date
from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTender,
)
from apps.vouchers.models import Voucher, VoucherOption, VoucherIdentity
from apps.vouchers.jurisdiction_routes import router as jurisdiction_router
from apps.vouchers.policy_packages import router as voucher_policy_package_router
from apps.vouchers.retention import router as voucher_retention_router
from apps.vouchers.jurisdictions import (
    VoucherLocations,
    route_voucher,
    routing_context,
    routing_token,
)

router = APIRouter(prefix="/api/vouchers", tags=["Vouchers"])
router.include_router(jurisdiction_router)
router.include_router(voucher_policy_package_router)
router.include_router(voucher_retention_router)
MAX_PAGE = 500
VOUCHER_EXPORT_MAX_ROWS = 50_000
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
    tender_type: str = Field(default="Voucher", min_length=1, max_length=100)
    issued_on: str | None = Field(default=None, max_length=10)
    expires_on: str | None = Field(default=None, max_length=10)
    locations: VoucherLocations = Field(default_factory=VoucherLocations)

    @field_validator("voucher_number")
    @classmethod
    def strip_number(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("voucher_number cannot be blank")
        return value.strip()

    @field_validator("tender_type")
    @classmethod
    def strip_tender_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tender_type cannot be blank")
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


async def transaction_references_voucher(
    db: AsyncSession, transaction_id: str, voucher_number: str
) -> bool:
    """Return whether transaction detail carries this voucher on a tender or item discount."""
    number = voucher_number.strip().lower()
    tender_match = await db.scalar(
        select(TransactionTender.id)
        .where(
            TransactionTender.transaction_id == transaction_id,
            func.lower(TransactionTender.tender_type).in_(
                ("voucher", "giftcard", "storecredit")
            ),
            func.lower(func.trim(TransactionTender.reference)) == number,
        )
        .limit(1)
    )
    if tender_match is not None:
        return True
    line_match = await db.scalar(
        select(TransactionLine.id)
        .where(
            TransactionLine.transaction_id == transaction_id,
            func.lower(
                func.trim(TransactionLine.source_attributes["voucher_number"].as_string())
            )
            == number,
        )
        .limit(1)
    )
    if line_match is not None:
        return True
    discount_match = await db.scalar(
        select(TransactionDiscount.id)
        .where(
            TransactionDiscount.transaction_id == transaction_id,
            TransactionDiscount.kind == "Voucher",
            func.lower(func.trim(TransactionDiscount.code)) == number,
        )
        .limit(1)
    )
    return discount_match is not None


class EscheatBody(BaseModel):
    voucher_numbers: list[str] = Field(min_length=1, max_length=MAX_PAGE)
    note: str | None = Field(default=None, max_length=2000)
    routing_tokens: dict[str, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] | None = Field(
        default=None, max_length=MAX_PAGE
    )


class VoucherOptionInput(BaseModel):
    tender_type: str = Field(min_length=1, max_length=100)
    escheat_days: int = Field(ge=0, le=36500)
    purge_days: int | None = Field(default=None, ge=0, le=36500)

    @field_validator("tender_type")
    @classmethod
    def strip_tender_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tender_type cannot be blank")
        return value.strip()


class VoucherOptionsBody(BaseModel):
    options: list[VoucherOptionInput] = Field(max_length=100)


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
        "tender_type": row.tender_type,
        "status": effective_status(row, as_of or today()),
        "redeemed_store_id": row.redeemed_store_id,
        "redeemed_on": row.redeemed_on,
        "redeemed_transaction_id": row.redeemed_transaction_id,
        "escheated_on": row.escheated_on,
        "escheat_locations": row.escheat_locations or {},
        "routing": row.escheat_routing_snapshot,
    }


def escheat_due_on(row: Voucher, options: dict[str, VoucherOption]) -> str | None:
    """Use tender aging when configured; otherwise preserve expiry-based eligibility."""
    option = options.get(row.tender_type.lower())
    if option is not None:
        try:
            return (
                date.fromisoformat(row.issued_on) + timedelta(days=option.escheat_days)
            ).isoformat()
        except OverflowError:
            return None
    if row.expires_on is None:
        return None
    try:
        return (date.fromisoformat(row.expires_on) + timedelta(days=1)).isoformat()
    except OverflowError:
        return None


def is_escheat_eligible(row: Voucher, as_of: str, options: dict[str, VoucherOption]) -> bool:
    if row.status != "Issued":
        return False
    due_on = escheat_due_on(row, options)
    return due_on is not None and due_on <= as_of


async def voucher_options_by_tender(db: AsyncSession) -> dict[str, VoucherOption]:
    return {
        row.tender_type.lower(): row
        for row in (
            await db.execute(select(VoucherOption).execution_options(populate_existing=True))
        )
        .scalars()
        .all()
    }


def escheat_eligibility_filter(as_of: str, options: dict[str, VoucherOption]):
    """Filter before pagination so recently issued eligible vouchers are not omitted."""
    tender = func.lower(Voucher.tender_type)
    conditions = [
        and_(
            tender == key,
            Voucher.issued_on
            <= (date.fromisoformat(as_of) - timedelta(days=option.escheat_days)).isoformat(),
        )
        for key, option in options.items()
    ]
    conditions.append(and_(tender.not_in(list(options)), Voucher.expires_on < as_of))
    return and_(Voucher.status == "Issued", or_(*conditions))


def eligibility_view(
    row: Voucher,
    as_of: str,
    options: dict[str, VoucherOption],
    context: tuple[dict, dict] | None = None,
) -> dict:
    result = {
        **view(row, as_of),
        "eligible_on": escheat_due_on(row, options) if row.status == "Issued" else None,
        "escheat_eligible": is_escheat_eligible(row, as_of, options),
        "eligibility_basis": "Tender policy"
        if row.tender_type.lower() in options
        else "Expiry date",
    }
    if context is not None:
        decision = route_voucher(row, *context)
        result["routing"] = decision
        result["routing_token"] = (
            routing_token(row, decision, result["eligible_on"]) if row.status == "Issued" else None
        )
    return result


@router.get("/options")
async def list_voucher_options(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    rows = (
        (await db.execute(select(VoucherOption).order_by(VoucherOption.tender_type)))
        .scalars()
        .all()
    )
    return [
        {
            "tender_type": row.tender_type,
            "escheat_days": row.escheat_days,
            "purge_days": row.purge_days,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.put("/options")
async def save_voucher_options(
    body: VoucherOptionsBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it", "admin")),
):
    names = [row.tender_type.lower() for row in body.options]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="Tender types must be unique")
    await lock_configuration(db, exclusive=True)
    existing = (await db.execute(select(VoucherOption))).scalars().all()
    before = [
        {
            "tender_type": row.tender_type,
            "escheat_days": row.escheat_days,
            "purge_days": row.purge_days,
        }
        for row in existing
    ]
    actor = user["user"] or "ontology.user"
    existing_by_tender = {row.tender_type.lower(): row for row in existing}
    after = []
    for item in body.options:
        row = existing_by_tender.pop(item.tender_type.lower(), None)
        if row is None:
            row = VoucherOption(tender_type=item.tender_type)
            db.add(row)
        row.tender_type = item.tender_type
        row.escheat_days = item.escheat_days
        if "purge_days" in item.model_fields_set:
            row.purge_days = item.purge_days
        row.updated_by = actor
        after.append(
            {
                "tender_type": row.tender_type,
                "escheat_days": row.escheat_days,
                "purge_days": row.purge_days,
            }
        )
    for row in existing_by_tender.values():
        await db.delete(row)
    audit(
        db,
        "VOUCHER_OPTIONS_UPDATED",
        None,
        "VoucherPolicy",
        "tender-options",
        "Voucher aging options changed",
        actor=actor,
        after={"before": before, "options": after},
    )
    await db.commit()
    return await list_voucher_options(db=db, user=user)


async def load(db: AsyncSession, number: str, *, lock: bool = False) -> Voucher:
    query = select(Voucher).where(Voucher.voucher_number == number)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        identity = await db.get(VoucherIdentity, number)
        if identity is not None and identity.retired_at is not None:
            disposition = "redemption" if identity.final_status == "Redeemed" else "escheatment"
            raise HTTPException(
                status_code=410,
                detail=f"Voucher was retired after {disposition} on {identity.finalized_on}",
            )
        raise HTTPException(status_code=404, detail="Voucher not found")
    return row


@router.get("")
async def list_vouchers(
    status: Annotated[
        str | None, Query(pattern="^(Issued|Expired|Redeemed|Escheated|Eligible)$")
    ] = None,
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    now = today()
    options = await voucher_options_by_tender(db)
    query = scope.restrict(select(Voucher), user, Voucher.issued_store_id)
    if status == "Eligible":
        query = query.where(escheat_eligibility_filter(now, options))
    elif status == "Expired":
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
        query = query.where(Voucher.voucher_number.contains(q, autoescape=True))
    rows = (
        (await db.execute(query.order_by(Voucher.created_at.desc(), Voucher.id).limit(limit)))
        .scalars()
        .all()
    )
    context = await routing_context(db, {row.issued_store_id for row in rows})
    return [eligibility_view(row, now, options, context) for row in rows]


@router.get("/export.csv")
async def export_vouchers(
    status: Annotated[
        str | None, Query(pattern="^(Issued|Expired|Redeemed|Escheated|Eligible)$")
    ] = None,
    store_id: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(*READ_ROLES)),
):
    """Export the full matching voucher ledger, bounded to protect the service."""
    now = today()
    options = await voucher_options_by_tender(db)
    query = scope.restrict(select(Voucher), user, Voucher.issued_store_id)
    if status == "Eligible":
        query = query.where(escheat_eligibility_filter(now, options))
    elif status == "Expired":
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
        query = query.where(Voucher.voucher_number.contains(q, autoescape=True))
    found = list(
        (
            await db.execute(
                query.order_by(Voucher.created_at.desc(), Voucher.id).limit(
                    VOUCHER_EXPORT_MAX_ROWS + 1
                )
            )
        )
        .scalars()
        .all()
    )
    truncated = len(found) > VOUCHER_EXPORT_MAX_ROWS
    rows = found[:VOUCHER_EXPORT_MAX_ROWS]
    context = await routing_context(db, {row.issued_store_id for row in rows})
    views = [eligibility_view(row, now, options, context) for row in rows]
    columns = [
        "Voucher number",
        "Status",
        "Issued store",
        "Issued on",
        "Expires on",
        "Amount",
        "Currency",
        "Tender type",
        "Escheat eligible",
        "Eligible on",
        "Eligibility basis",
        "Redeemed store",
        "Redeemed on",
        "Redeemed transaction ID",
        "Escheat routing",
    ]
    csv_rows = [
        [
            item.get("voucher_number"),
            item.get("status"),
            item.get("issued_store_id"),
            item.get("issued_on"),
            item.get("expires_on"),
            item.get("amount"),
            item.get("currency"),
            item.get("tender_type"),
            item.get("escheat_eligible"),
            item.get("eligible_on"),
            item.get("eligibility_basis"),
            item.get("redeemed_store_id"),
            item.get("redeemed_on"),
            item.get("redeemed_transaction_id"),
            json.dumps(item.get("routing"), sort_keys=True, separators=(",", ":"))
            if item.get("routing")
            else None,
        ]
        for item in views
    ]
    return Response(
        csv_table(columns, csv_rows),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="vouchers.csv"',
            "X-Voucher-Export-Truncated": "true" if truncated else "false",
        },
    )


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
    _, stores = await routing_context(db, {body.store_id})
    locations = body.locations.model_dump(exclude_none=True)
    if "Store" not in locations and stores.get(body.store_id):
        locations["Store"] = stores[body.store_id]
    row = Voucher(
        id=str(uuid.uuid4()),
        voucher_number=body.voucher_number,
        issued_store_id=body.store_id,
        issued_on=issued_on,
        expires_on=body.expires_on,
        amount=body.amount,
        currency=body.currency.upper(),
        tender_type=body.tender_type,
        issued_by=actor,
        escheat_locations=locations,
    )
    try:
        db.add(
            VoucherIdentity(
                voucher_number=row.voucher_number,
                voucher_id=row.id,
                issued_store_id=row.issued_store_id,
                tender_type=row.tender_type,
                issued_on=row.issued_on,
                amount=row.amount,
                currency=row.currency,
            )
        )
        await db.flush()
        db.add(row)
        audit(
            db,
            "VOUCHER_ISSUED",
            None,
            "Voucher",
            row.id,
            f"Voucher {row.voucher_number} issued at {row.issued_store_id} for {row.amount}",
            actor=actor,
            after={"locations": locations},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="That voucher number already exists or is permanently reserved"
        ) from exc
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
    linked_transaction = None
    if body.transaction_id:
        linked_transaction = await db.get(CanonicalTransaction, body.transaction_id)
        if linked_transaction is None:
            raise HTTPException(status_code=404, detail="Redemption transaction not found")
        scope.require_store(user, linked_transaction.store_id)
        if linked_transaction.store_id != body.store_id:
            raise HTTPException(
                status_code=422,
                detail="The redemption store must match the transaction store",
            )
        if not await transaction_references_voucher(db, linked_transaction.id, row.voucher_number):
            raise HTTPException(
                status_code=422,
                detail="The selected transaction has no matching voucher tender or item reference",
            )
    redeemed_on = body.redeemed_on or (
        linked_transaction.business_date if linked_transaction else today()
    )
    if row.status == "Redeemed":
        raise HTTPException(
            status_code=409,
            detail=f"Already redeemed at {row.redeemed_store_id} on {row.redeemed_on}",
        )
    if row.status == "Escheated":
        raise HTTPException(status_code=409, detail="This voucher has been escheated")
    if row.expires_on and row.expires_on < redeemed_on:
        raise HTTPException(status_code=409, detail=f"This voucher expired on {row.expires_on}")
    if row.issued_on > redeemed_on:
        raise HTTPException(status_code=409, detail="The voucher was issued after the redemption date")
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
    """Vouchers whose configured tender aging window has elapsed, grouped by store."""
    cutoff = (date.fromisoformat(today()) - timedelta(days=waiting_days)).isoformat()
    options = await voucher_options_by_tender(db)
    rows = (
        (
            await db.execute(
                scope.restrict(
                    select(Voucher).where(escheat_eligibility_filter(cutoff, options)),
                    user,
                    Voucher.issued_store_id,
                )
                .order_by(Voucher.issued_on, Voucher.voucher_number)
                .limit(MAX_PAGE + 1)
            )
        )
        .scalars()
        .all()
    )
    eligible = [
        (row, escheat_due_on(row, options))
        for row in rows
        if is_escheat_eligible(row, cutoff, options)
    ]
    eligible.sort(key=lambda item: (item[1] or "", item[0].voucher_number))
    context = await routing_context(db, {row.issued_store_id for row, _ in eligible[:MAX_PAGE]})
    by_store: dict[tuple[str, str], dict] = {}
    for row, due_on in eligible[:MAX_PAGE]:
        entry = by_store.setdefault(
            (row.issued_store_id, row.currency),
            {"store_id": row.issued_store_id, "currency": row.currency, "vouchers": 0, "amount": 0},
        )
        entry["vouchers"] += 1
        entry["amount"] += row.amount
    return {
        "eligible_as_of": cutoff,
        "expired_before": cutoff,
        "truncated": len(rows) > MAX_PAGE,
        "by_store": sorted(by_store.values(), key=lambda e: (e["store_id"], e["currency"])),
        "vouchers": [
            eligibility_view(row, cutoff, options, context) for row, due_on in eligible[:MAX_PAGE]
        ],
    }


@router.post("/escheat")
async def escheat_vouchers(
    body: EscheatBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance")),
):
    """Record Finance's escheatment of eligible outstanding vouchers."""
    if scope.scoped(user):
        raise HTTPException(
            status_code=403, detail="Escheating needs a user who can see all stores"
        )
    now = today()
    await lock_configuration(db)
    options = await voucher_options_by_tender(db)
    numbers = sorted(set(body.voucher_numbers))
    rows = (
        (
            await db.execute(
                select(Voucher)
                .where(Voucher.voucher_number.in_(numbers))
                .order_by(Voucher.voucher_number)
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
        else f"{number}: not yet eligible under its tender escheat policy"
        for number in numbers
        if number not in found or not is_escheat_eligible(found[number], now, options)
    ]
    if problems:
        raise HTTPException(
            status_code=422,
            detail="Only eligible, unredeemed vouchers can be escheated. "
            + "; ".join(problems[:5]),
        )
    context = await routing_context(db, {row.issued_store_id for row in rows})
    decisions = {row.voucher_number: route_voucher(row, *context) for row in rows}
    if context[0]["enabled"] or body.routing_tokens is not None:
        tokens = body.routing_tokens or {}
        changed = [
            row.voucher_number
            for row in rows
            if tokens.get(row.voucher_number)
            != routing_token(row, decisions[row.voucher_number], escheat_due_on(row, options))
        ]
        if changed:
            raise HTTPException(
                status_code=409,
                detail="Voucher routing changed or was not reviewed. Refresh the vouchers and review destinations again: "
                + ", ".join(changed[:5]),
            )
    actor = user["user"] or "ontology.user"
    amounts: dict[str, Decimal] = {}
    for row in rows:
        amounts[row.currency] = amounts.get(row.currency, Decimal("0")) + row.amount
    total = next(iter(amounts.values())) if len(amounts) == 1 else None
    for row in rows:
        row.status, row.escheated_on, row.escheated_by = "Escheated", now, actor
        row.escheat_note = (body.note or "").strip() or None
        row.escheat_routing_snapshot = decisions[row.voucher_number]
    audit(
        db,
        "VOUCHERS_ESCHEATED",
        None,
        "Voucher",
        numbers[0],
        f"{len(rows)} eligible voucher(s) escheated",
        actor=actor,
        after={
            "voucher_numbers": numbers,
            "amount": str(total) if total is not None else None,
            "amounts": {currency: str(amount) for currency, amount in amounts.items()},
            "note": body.note,
            "routing": decisions,
            "eligibility": [
                {
                    "voucher_number": row.voucher_number,
                    "tender_type": row.tender_type,
                    "eligible_on": escheat_due_on(row, options),
                    "escheat_days": options[row.tender_type.lower()].escheat_days
                    if row.tender_type.lower() in options
                    else None,
                }
                for row in rows
            ],
        },
    )
    await db.commit()
    return {"escheated": len(rows), "amount": total, "amounts": amounts}


@router.get("/redeem-candidates")
async def voucher_redemption_candidates(
    voucher_number: Annotated[str, Query(min_length=1, max_length=100)],
    store_id: Annotated[str, Query(min_length=1, max_length=50)],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it", "store_manager")),
):
    """Find in-store transactions whose item or tender detail names this voucher."""
    scope.require_store(user, store_id)
    voucher = await load(db, voucher_number)
    number = voucher_number.strip().lower()
    tender_ids = select(TransactionTender.transaction_id).where(
        func.lower(TransactionTender.tender_type).in_(
            ("voucher", "giftcard", "storecredit")
        ),
        func.lower(func.trim(TransactionTender.reference)) == number,
    )
    discount_ids = select(TransactionDiscount.transaction_id).where(
        TransactionDiscount.kind == "Voucher",
        func.lower(func.trim(TransactionDiscount.code)) == number,
    )
    item_ids = select(TransactionLine.transaction_id).where(
        func.lower(func.trim(TransactionLine.source_attributes["voucher_number"].as_string()))
        == number
    )
    query = scope.restrict(
        select(CanonicalTransaction)
        .where(
            CanonicalTransaction.store_id == store_id,
            CanonicalTransaction.business_date >= voucher.issued_on,
            or_(
                CanonicalTransaction.id.in_(tender_ids),
                CanonicalTransaction.id.in_(discount_ids),
                CanonicalTransaction.id.in_(item_ids),
            ),
        )
        .order_by(CanonicalTransaction.business_date.desc(), CanonicalTransaction.id.desc())
        .limit(51),
        user,
        CanonicalTransaction.store_id,
    )
    rows = (await db.execute(query)).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "business_date": row.business_date,
                "store_id": row.store_id,
                "transaction_type": row.transaction_type,
                "transaction_number": row.transaction_number,
                "register_id": row.register_id,
                "amount": str(row.signed_amount),
                "currency": row.currency,
            }
            for row in rows[:50]
        ],
        "truncated": len(rows) > 50,
    }


@router.get("/transactions/{transaction_id}/context")
async def transaction_voucher_context(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    """Reconcile voucher tender/item references with the voucher ledger for one transaction."""
    transaction = await db.get(CanonicalTransaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    scope.require_store(user, transaction.store_id)

    tender_rows = (
        await db.execute(
            select(
                TransactionTender.reference,
                TransactionTender.tender_type,
                TransactionTender.amount,
            )
            .where(
                TransactionTender.transaction_id == transaction.id,
                func.lower(TransactionTender.tender_type).in_(
                    ("voucher", "giftcard", "storecredit")
                ),
                TransactionTender.reference.is_not(None),
            )
            .order_by(TransactionTender.id)
            .limit(1001)
        )
    ).all()
    discount_rows = (
        await db.execute(
            select(
                TransactionDiscount.code,
                TransactionDiscount.line_no,
                TransactionDiscount.amount,
            )
            .where(
                TransactionDiscount.transaction_id == transaction.id,
                TransactionDiscount.kind == "Voucher",
                TransactionDiscount.code.is_not(None),
            )
            .order_by(TransactionDiscount.id)
            .limit(1001)
        )
    ).all()
    item_voucher_rows = (
        await db.execute(
            select(TransactionLine.line_no, TransactionLine.source_attributes)
            .where(
                TransactionLine.transaction_id == transaction.id,
                TransactionLine.source_attributes["voucher_number"].as_string().is_not(None),
            )
            .order_by(TransactionLine.line_no, TransactionLine.id)
            .limit(1001)
        )
    ).all()
    contexts: dict[str, list[dict]] = defaultdict(list)
    for number, tender_type, amount in tender_rows[:1000]:
        contexts[number].append(
            {"source": "Tender", "detail": tender_type, "amount": str(amount)}
        )
    for number, line_no, amount in discount_rows[:1000]:
        contexts[number].append(
            {"source": "Item voucher", "detail": f"Line {line_no}" if line_no else None, "amount": str(amount)}
        )
    for line_no, attributes in item_voucher_rows[:1000]:
        number = (attributes or {}).get("voucher_number")
        if isinstance(number, str) and number.strip():
            contexts[number].append(
                {"source": "Item voucher", "detail": f"Line {line_no}", "amount": None}
            )
    normalized_contexts: dict[str, list[dict]] = defaultdict(list)
    for number, details in contexts.items():
        normalized_contexts[number.strip().lower()].extend(details)
    codes = sorted(normalized_contexts)
    voucher_limit = 200
    selected_codes = codes[:voucher_limit]
    voucher_rows = (
        await db.execute(
            select(Voucher).where(
                or_(
                    Voucher.redeemed_transaction_id == transaction.id,
                    func.lower(Voucher.voucher_number).in_(selected_codes),
                )
            )
            .order_by(Voucher.voucher_number)
            .limit(voucher_limit + 1)
        )
    ).scalars().all()
    linked_vouchers_truncated = len(voucher_rows) > voucher_limit
    voucher_rows = voucher_rows[:voucher_limit]
    identity_rows = (
        await db.execute(
            select(VoucherIdentity).where(
                func.lower(VoucherIdentity.voucher_number).in_(selected_codes)
            )
        )
    ).scalars().all()
    vouchers = {row.voucher_number.lower(): row for row in voucher_rows}
    identities = {row.voucher_number.lower(): row for row in identity_rows}
    numbers = sorted(set(selected_codes) | set(vouchers))
    items = []
    for number in numbers:
        row = vouchers.get(number)
        identity = identities.get(number)
        linked = bool(row and row.redeemed_transaction_id == transaction.id)
        status = (
            effective_status(row, today())
            if row
            else (identity.final_status or "Reserved")
            if identity
            else "Not in ledger"
        )
        items.append(
            {
                "voucher_number": row.voucher_number if row else identity.voucher_number if identity else number,
                "contexts": normalized_contexts.get(number, []),
                "status": status,
                "amount": str(row.amount if row else identity.amount if identity else ""),
                "currency": row.currency if row else identity.currency if identity else None,
                "issued_store_id": row.issued_store_id if row else identity.issued_store_id if identity else None,
                "redeemed_store_id": row.redeemed_store_id if row else None,
                "redeemed_on": row.redeemed_on if row else identity.finalized_on if identity else None,
                "transaction_linked": linked,
                "ledger_transaction_mismatch": bool(
                    row and row.redeemed_transaction_id and not linked
                ),
            }
        )
    return {
        "items": items,
        "truncated": (
            len(codes) > voucher_limit
            or len(tender_rows) > 1000
            or len(discount_rows) > 1000
            or len(item_voucher_rows) > 1000
            or linked_vouchers_truncated
        ),
    }


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

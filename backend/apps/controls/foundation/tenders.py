"""Tender types: which forms of payment a retailer accepts, and the rules each one carries.

Tenders live in the Foundation reference data (category "Tender"), so the audit rules that already
read that list keep working. This adds validated attributes, a dedicated API and the audit rules
that use them: a tender that needs a reference, and a limit on one tender's amount.
"""

import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import FoundationReference
from apps.security import scope
from apps.security.authentication import require_role
from apps.transactions.detail import MAX_MONEY, TENDER_TYPES
from apps.transactions.models import CanonicalTransaction, TransactionTender

TENDER_GROUPS = ("Cash", "Card", "Voucher", "Other")
router = APIRouter(prefix="/api/tender-types", tags=["Tender types"])


def normalize_attributes(attributes: dict) -> dict:
    """Validate the tender attributes and return them in their stored shape.

    Other keys are kept as they are. Raises ValueError with the message the user should see.
    """
    group = attributes.get("tender_group", "Other")
    if group not in TENDER_GROUPS:
        raise ValueError(f"tender_group must be one of {', '.join(sorted(TENDER_GROUPS))}")
    requires = attributes.get("requires_reference", False)
    if not isinstance(requires, bool):
        raise ValueError("requires_reference must be true or false")
    limit = attributes.get("max_amount")
    if limit in (None, ""):
        limit = None
    else:
        try:
            amount = Decimal(str(limit))
        except InvalidOperation as exc:
            raise ValueError("max_amount must be a number") from exc
        if not amount.is_finite() or amount <= 0 or amount > MAX_MONEY:
            raise ValueError("max_amount must be a positive amount")
        limit = str(amount.quantize(Decimal("0.01")))
    return {
        **attributes,
        "tender_group": group,
        "requires_reference": requires,
        "max_amount": limit,
    }


class TenderBody(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    active: bool = True
    tender_group: str = "Other"
    requires_reference: bool = False
    max_amount: str | None = Field(default=None, max_length=30)

    @field_validator("code", "name")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value cannot be blank")
        return value.strip()

    def attributes(self, existing: dict | None = None) -> dict:
        try:
            return normalize_attributes(
                {
                    **(existing or {}),
                    "tender_group": self.tender_group,
                    "requires_reference": self.requires_reference,
                    "max_amount": self.max_amount,
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def view(row: FoundationReference, used: int = 0) -> dict:
    attributes = row.attributes if isinstance(row.attributes, dict) else {}
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "active": row.active,
        "tender_group": attributes.get("tender_group", "Other"),
        "requires_reference": bool(attributes.get("requires_reference", False)),
        "max_amount": attributes.get("max_amount"),
        "tender_lines": used,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def audit_state(row: FoundationReference) -> dict:
    """The business fields of a tender, without timestamps, as the audit trail stores them."""
    return {k: v for k, v in view(row).items() if k not in ("updated_at", "tender_lines")}


def require_known_code(code: str) -> None:
    """Ingest rejects any other tender code, so defining one could never match a payment."""
    if code not in TENDER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Tender code must be one of {', '.join(TENDER_TYPES)}; the feed cannot send others",
        )


@router.get("")
async def list_tenders(
    active: Annotated[bool | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    query = select(FoundationReference).where(FoundationReference.category == "Tender")
    if active is not None:
        query = query.where(FoundationReference.active.is_(active))
    rows = (await db.execute(query.order_by(FoundationReference.code))).scalars().all()
    usage = dict(
        (
            await db.execute(
                scope.restrict(
                    select(TransactionTender.tender_type, func.count())
                    .join(
                        CanonicalTransaction,
                        CanonicalTransaction.id == TransactionTender.transaction_id,
                    )
                    .group_by(TransactionTender.tender_type),
                    user,
                    CanonicalTransaction.store_id,
                )
            )
        ).all()
    )
    return {
        "known_codes": list(TENDER_TYPES),
        "groups": list(TENDER_GROUPS),
        "items": [view(row, usage.get(row.code, 0)) for row in rows],
    }


@router.post("")
async def create_tender(
    body: TenderBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    require_known_code(body.code)
    actor = user["user"] or "ontology.user"
    row = FoundationReference(
        id=str(uuid.uuid4()),
        category="Tender",
        code=body.code,
        name=body.name,
        active=body.active,
        attributes=body.attributes(),
        updated_by=actor,
    )
    db.add(row)
    audit(
        db,
        "TENDER_TYPE_CREATED",
        None,
        "FoundationReference",
        row.id,
        f"Tender type {row.code} defined in group {row.attributes['tender_group']}",
        actor=actor,
        after=audit_state(row),
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That tender type already exists") from exc
    return view(row)


@router.put("/{tender_id}")
async def update_tender(
    tender_id: str,
    body: TenderBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it")),
):
    row = await db.get(FoundationReference, tender_id)
    if row is None or row.category != "Tender":
        raise HTTPException(status_code=404, detail="Tender type not found")
    if body.code != row.code:
        raise HTTPException(
            status_code=422, detail="A tender's code cannot change; add a new tender instead"
        )
    actor = user["user"] or "ontology.user"
    before = audit_state(row)
    row.name, row.active, row.updated_by = body.name, body.active, actor
    row.attributes = body.attributes(row.attributes if isinstance(row.attributes, dict) else {})
    audit(
        db,
        "TENDER_TYPE_UPDATED",
        None,
        "FoundationReference",
        row.id,
        f"Tender type {row.code} updated",
        actor=actor,
        after={"before": before, "after": audit_state(row)},
    )
    await db.commit()
    return view(row)

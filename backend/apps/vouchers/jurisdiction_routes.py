"""Govern jurisdiction policy and correct voucher-level jurisdiction evidence."""

import uuid

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.security import scope
from apps.security.authentication import require_role
from apps.store_days.freshness import lock_configuration
from apps.vouchers.jurisdictions import RoutingPolicyInput, VoucherLocations, policy_view
from apps.vouchers.models import Voucher, VoucherRoutingPolicy

router = APIRouter()


@router.get("/routing-policy")
async def get_routing_policy(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    return policy_view(await db.get(VoucherRoutingPolicy, "default", populate_existing=True))


@router.put("/routing-policy")
async def update_routing_policy(
    body: RoutingPolicyInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    row = await db.get(VoucherRoutingPolicy, "default", populate_existing=True)
    if body.expected_revision != (row.revision if row else None):
        raise HTTPException(
            status_code=409, detail="Voucher routing policy changed. Reload it before saving."
        )
    before = policy_view(row)
    actor = user["user"] or "ontology.user"
    if row is None:
        row = VoucherRoutingPolicy(id="default")
        db.add(row)
    row.enabled = body.enabled
    row.hq_location = body.hq_location.model_dump() if body.hq_location else None
    row.income_partner = body.income_partner
    row.rules = [rule.model_dump() for rule in body.rules]
    row.revision = str(uuid.uuid4())
    row.updated_by = actor
    audit(
        db,
        "VOUCHER_ROUTING_POLICY_UPDATED",
        None,
        "VoucherRoutingPolicy",
        "default",
        "Voucher jurisdiction routing changed",
        actor=actor,
        after={
            "before": before,
            "policy": body.model_dump(exclude={"expected_revision"}),
            "revision": row.revision,
        },
    )
    await db.commit()
    return policy_view(row)


class VoucherLocationsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locations: VoucherLocations
    expected_locations: VoucherLocations | None = None


@router.put("/{voucher_number}/jurisdictions")
async def update_voucher_locations(
    voucher_number: str,
    body: VoucherLocationsBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it", "store_manager")),
):
    row = (
        await db.execute(
            select(Voucher)
            .where(Voucher.voucher_number == voucher_number)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Voucher not found")
    scope.require_store(user, row.issued_store_id)
    if row.status != "Issued":
        raise HTTPException(
            status_code=409, detail="Jurisdictions cannot change after redemption or escheatment"
        )
    before = row.escheat_locations or {}
    if body.expected_locations is not None and before != body.expected_locations.model_dump(
        exclude_none=True
    ):
        raise HTTPException(
            status_code=409, detail="Voucher jurisdictions changed. Reload before saving."
        )
    row.escheat_locations = body.locations.model_dump(exclude_none=True)
    audit(
        db,
        "VOUCHER_JURISDICTIONS_UPDATED",
        None,
        "Voucher",
        row.id,
        f"Jurisdictions updated for voucher {row.voucher_number}",
        actor=user["user"] or "ontology.user",
        after={"before": before, "locations": row.escheat_locations},
    )
    await db.commit()
    return {"voucher_number": row.voucher_number, "escheat_locations": row.escheat_locations}

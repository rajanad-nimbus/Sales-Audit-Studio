"""Retailer-defined voucher routing, with explicit hierarchy evidence and stable previews."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import FoundationReference
from apps.vouchers.models import Voucher, VoucherRoutingPolicy

LOCATIONS = ("Store", "Issuer", "Recipient", "HQ")
LocationOf = Literal["Store", "Issuer", "Recipient", "HQ"]


class Jurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    state: str | None = Field(default=None, max_length=100)

    @field_validator("country", mode="before")
    @classmethod
    def country_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("state", mode="before")
    @classmethod
    def subdivision(cls, value):
        return value.strip().upper() or None if isinstance(value, str) else value


class VoucherLocations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    Store: Jurisdiction | None = None
    Issuer: Jurisdiction | None = None
    Recipient: Jurisdiction | None = None


class JurisdictionRule(Jurisdiction):
    locations: list[LocationOf] = Field(default_factory=list, max_length=4)

    @field_validator("locations")
    @classmethod
    def unique_locations(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Each location may appear only once")
        return [location for location in LOCATIONS if location in values]


class RoutingPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    hq_location: Jurisdiction | None = None
    income_partner: str | None = Field(default=None, max_length=100)
    rules: list[JurisdictionRule] = Field(default_factory=list, max_length=200)
    expected_revision: str | None = Field(default=None, max_length=36)

    @field_validator("income_partner", mode="before")
    @classmethod
    def partner_code(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_policy(self):
        keys = [(rule.country, rule.state) for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("Configure each country/state jurisdiction only once")
        if self.enabled and not self.income_partner:
            raise ValueError("An income adjustment partner is required when routing is enabled")
        return self


def policy_view(row: VoucherRoutingPolicy | None) -> dict:
    return {
        "enabled": bool(row and row.enabled),
        "hq_location": row.hq_location if row else None,
        "income_partner": row.income_partner if row else None,
        "rules": row.rules if row else [],
        "revision": row.revision if row else None,
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at.isoformat() if row else None,
    }


def store_jurisdiction(attributes: dict) -> dict | None:
    country = attributes.get("country_code") or attributes.get("country")
    state = (
        attributes.get("state_code")
        or attributes.get("province_code")
        or attributes.get("state")
        or attributes.get("province")
    )
    try:
        return Jurisdiction(country=country, state=state).model_dump()
    except ValidationError:
        return None


async def routing_context(db: AsyncSession, store_ids: set[str]) -> tuple[dict, dict]:
    policy = policy_view(await db.get(VoucherRoutingPolicy, "default", populate_existing=True))
    references = (
        (
            await db.execute(
                select(FoundationReference)
                .where(
                    FoundationReference.category == "Store", FoundationReference.code.in_(store_ids)
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
        if store_ids
        else []
    )
    return policy, {row.code: store_jurisdiction(row.attributes or {}) for row in references}


def route_voucher(row: Voucher, policy: dict, stores: dict) -> dict:
    """A state rule takes precedence over the country's rule, including its exclusions."""
    if row.status == "Escheated":
        return row.escheat_routing_snapshot or {
            "destination_type": "Legacy",
            "label": "Recorded before routing",
            "trace": [],
        }
    base = {
        "policy_revision": policy["revision"],
        "country": None,
        "state": None,
        "location_of": None,
        "income_partner": None,
        "trace": [],
    }
    if not policy["enabled"]:
        return {**base, "destination_type": "Manual", "label": "Manual escheatment"}

    locations = row.escheat_locations or {}
    rules = {(rule["country"], rule.get("state")): rule for rule in policy["rules"]}
    trace = []
    for level in LOCATIONS:
        source = "HQ policy" if level == "HQ" else "Voucher"
        location = policy["hq_location"] if level == "HQ" else locations.get(level)
        if level == "Store" and not location:
            location, source = stores.get(row.issued_store_id), "Store reference"
        step = {"location_of": level, "source": source, "jurisdiction": location}
        if not location:
            trace.append({**step, "outcome": "No jurisdiction supplied"})
            continue
        country, state = location["country"], location.get("state")
        rule = rules.get((country, state)) if state else None
        rule = rule if rule is not None else rules.get((country, None))
        if rule is None:
            trace.append({**step, "outcome": "No jurisdiction rule"})
            continue
        matched = {"country": rule["country"], "state": rule.get("state")}
        if level not in rule["locations"]:
            trace.append({**step, "matched_rule": matched, "outcome": "Location excluded by rule"})
            continue
        trace.append({**step, "matched_rule": matched, "outcome": "Selected"})
        return {
            **base,
            **matched,
            "destination_type": "Government",
            "location_of": level,
            "label": country + (f" / {rule['state']}" if rule.get("state") else ""),
            "trace": trace,
        }
    return {
        **base,
        "destination_type": "RetailerIncome",
        "label": "Retailer income",
        "income_partner": policy["income_partner"],
        "trace": trace,
    }


def routing_token(row: Voucher, decision: dict, eligible_on: str | None) -> str:
    context = {
        "voucher_id": row.id,
        "amount": format(row.amount, ".2f"),
        "currency": row.currency,
        "locations": row.escheat_locations,
        "decision": decision,
        "eligible_on": eligible_on,
    }
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

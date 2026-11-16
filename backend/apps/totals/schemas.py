"""Validated API request contracts for audit total definitions."""

import json
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.totals.sources import normalize_source_system

REALM_FIELDS = {
    "Transaction": {
        "business_date": "text",
        "transaction_type": "text",
        "transaction_subtype": "text",
        "source_lineage": "text",
        "currency": "text",
        "signed_amount": "number",
        "quantity": "number",
        "register_id": "text",
        "cashier_id": "text",
        "salesperson_id": "text",
        "channel_id": "text",
        "tender_type": "text",
        "detail_status": "text",
        "return_reason": "text",
        "transaction_number": "text",
    },
    "Line": {
        "item_code": "text",
        "quantity": "number",
        "unit_price": "number",
        "gross_amount": "number",
        "tax_code": "text",
        "tax_amount": "number",
        "return_reason": "text",
    },
    "Tax": {
        "tax_code": "text",
        "tax_name": "text",
        "rate": "number",
        "taxable_amount": "number",
        "tax_amount": "number",
    },
    "Tender": {
        "tender_type": "text",
        "amount": "number",
        "reference": "text",
        "authorization": "text",
    },
    "Discount": {
        "kind": "text",
        "code": "text",
        "description": "text",
        "amount": "number",
    },
}


class ActivationBody(BaseModel):
    """Activation must name the impact preview the approver reviewed."""

    impact_fingerprint: str = Field(min_length=64, max_length=64)


class ReturnBody(BaseModel):
    """Why a reviewer sent a submitted revision back to Worksheet."""

    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def strip_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


class TotalRestrictionBody(BaseModel):
    """A bounded condition on an allowlisted transaction realm."""

    realm: Literal["Transaction", "Line", "Tax", "Tender", "Discount"]
    field: str = Field(min_length=1, max_length=40)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_empty", "not_empty"]
    value: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_restriction(self):
        available = REALM_FIELDS[self.realm]
        if self.field not in available:
            raise ValueError(f"Field {self.field} is not available in the {self.realm} realm")
        if self.operator in {"gt", "gte", "lt", "lte"} and available[self.field] != "number":
            raise ValueError("Ordered comparisons are supported only for numeric fields")
        if self.operator in {"is_empty", "not_empty"}:
            if self.value not in (None, ""):
                raise ValueError("Empty checks do not take a comparison value")
            return self
        if self.value is None or not self.value.strip():
            raise ValueError("This comparison requires a value")
        if available[self.field] == "number":
            try:
                number = Decimal(self.value)
            except InvalidOperation as exc:
                raise ValueError("Enter a valid numeric comparison value") from exc
            if not number.is_finite():
                raise ValueError("Enter a finite numeric comparison value")
        return self


class TotalParameterBody(BaseModel):
    """The field summed or counted from a selected total realm."""

    realm: Literal["Transaction", "Line", "Tax", "Tender", "Discount"]
    field: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_parameter(self):
        if self.field not in REALM_FIELDS[self.realm]:
            raise ValueError(f"Field {self.field} is not available in the {self.realm} realm")
        return self


class TotalRollupBody(BaseModel):
    """A selected field that subdivides calculated totals for analysis."""

    realm: Literal["Transaction", "Line", "Tax", "Tender", "Discount"]
    field: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_rollup(self):
        if self.field not in REALM_FIELDS[self.realm]:
            raise ValueError(f"Field {self.field} is not available in the {self.realm} realm")
        return self


class TotalUsageBody(BaseModel):
    """Destination that consumes a calculated total."""

    destination_id: str = Field(min_length=1, max_length=36)


class TotalDefinitionBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    aggregation: str = Field(default="Sum", max_length=16)
    measure: str = Field(default="Amount", max_length=16)
    source_system: str | None = Field(default=None, max_length=50)
    transaction_types: list[str] = Field(default_factory=list, max_length=100)
    tender_type: str | None = Field(default=None, max_length=50)
    level: str = Field(default="Store", max_length=24)
    active: bool = True
    revision: int | None = None
    description: str | None = Field(default=None, max_length=5000)
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    category: str = Field(default="Miscellaneous", max_length=50)
    over_short_group: str | None = Field(default=None, max_length=32)
    over_short_operator: str | None = Field(default=None, max_length=8)
    calculation_basis: str = Field(default="Raw", max_length=32)
    status: str = Field(default="Worksheet", max_length=24)
    corresponds_to_rtlog: bool = False
    system_calculated: bool = True
    created_with_wizard: bool = True
    required_by_system: bool = False
    display_group: int = Field(default=0, ge=0, le=2**31 - 1)
    realms: list[dict] = Field(default_factory=list, max_length=100)
    joins: list[dict] = Field(default_factory=list, max_length=100)
    parameters: list[TotalParameterBody] = Field(default_factory=list, max_length=1)
    rollups: list[TotalRollupBody] = Field(default_factory=list, max_length=3)
    restrictions: list[TotalRestrictionBody] = Field(default_factory=list, max_length=100)
    combined_total_ids: list[str] = Field(default_factory=list, max_length=20)
    combined_total_operators: list[str] = Field(default_factory=list, max_length=19)
    combined_operator: str | None = Field(default=None, max_length=1)
    location_traits: list[str] = Field(default_factory=list, max_length=100)
    usages: list[TotalUsageBody] = Field(default_factory=list, max_length=100)
    sql_statement: str | None = Field(default=None, max_length=10000)

    @field_validator("source_system")
    @classmethod
    def validate_source_system(cls, value: str | None) -> str | None:
        return normalize_source_system(value)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Total name cannot be blank")
        return normalized

    @field_validator("location_traits")
    @classmethod
    def validate_location_traits(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values) or len(
            set(values)
        ) != len(values):
            raise ValueError(
                "Location traits must be distinct non-empty codes without surrounding whitespace"
            )
        return values

    @model_validator(mode="after")
    def enforce_metadata_size(self):
        if self.calculation_basis == "Combined" and (self.restrictions or self.parameters):
            raise ValueError("Combined totals use components and cannot define realm parameters")
        if self.parameters and self.measure != "Amount":
            raise ValueError("Select Amount measure when choosing a realm parameter")
        rollup_fields = [(item.realm, item.field) for item in self.rollups]
        if len(set(rollup_fields)) != len(rollup_fields):
            raise ValueError("Roll-up fields must be unique")
        usage_destinations = [item.destination_id for item in self.usages]
        if len(set(usage_destinations)) != len(usage_destinations):
            raise ValueError("Export destinations must be unique in total usages")
        if self.calculation_basis != "Combined":
            measure_realm = self.parameters[0].realm if self.parameters else "Transaction"
            if any(item.realm not in {"Transaction", measure_realm} for item in self.rollups):
                raise ValueError("Roll-ups must use the measure realm or its transaction header")
        if self.aggregation == "Sum" and self.parameters:
            parameter = self.parameters[0]
            if REALM_FIELDS[parameter.realm][parameter.field] != "number":
                raise ValueError("Sum requires a numeric realm parameter")
        metadata = {
            "transaction_types": self.transaction_types,
            "realms": self.realms,
            "joins": self.joins,
            "parameters": [parameter.model_dump() for parameter in self.parameters],
            "rollups": [rollup.model_dump() for rollup in self.rollups],
            "usages": [usage.model_dump() for usage in self.usages],
            "restrictions": [restriction.model_dump() for restriction in self.restrictions],
            "combined_total_ids": self.combined_total_ids,
            "combined_total_operators": self.combined_total_operators,
            "location_traits": self.location_traits,
        }
        if (
            len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            > 256 * 1024
        ):
            raise ValueError("Total-definition metadata exceeds the 256 KiB limit")
        return self

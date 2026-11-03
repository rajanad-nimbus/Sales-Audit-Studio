"""Validated API request contracts for audit total definitions."""

import json

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.totals.sources import normalize_source_system


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
    parameters: list[dict] = Field(default_factory=list, max_length=100)
    rollups: list[dict] = Field(default_factory=list, max_length=100)
    restrictions: list[dict] = Field(default_factory=list, max_length=100)
    combined_total_ids: list[str] = Field(default_factory=list, max_length=2)
    combined_operator: str | None = Field(default=None, max_length=1)
    location_traits: list[str] = Field(default_factory=list, max_length=100)
    usages: list[dict] = Field(default_factory=list, max_length=100)
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
        metadata = {
            "transaction_types": self.transaction_types,
            "realms": self.realms,
            "joins": self.joins,
            "parameters": self.parameters,
            "rollups": self.rollups,
            "restrictions": self.restrictions,
            "combined_total_ids": self.combined_total_ids,
            "location_traits": self.location_traits,
            "usages": self.usages,
        }
        if (
            len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
            > 256 * 1024
        ):
            raise ValueError("Total-definition metadata exceeds the 256 KiB limit")
        return self

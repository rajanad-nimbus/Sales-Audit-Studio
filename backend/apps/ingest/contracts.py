"""Request contracts and bounded parsing for source ingestion."""

import csv
import io
import json
import re
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.ingest.limits import MAX_DELIVERY_BYTES, MAX_DELIVERY_RECORDS


def json_payload_size(value, limit: int) -> int:
    """Count compact UTF-8 JSON bytes without building a second full serialized payload."""
    encoder = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False)
    size = 0
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            return size
    return size


class IngestRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=50)
    delivery_id: str = Field(min_length=1, max_length=255)
    records: list[dict] = Field(max_length=MAX_DELIVERY_RECORDS)

    @field_validator("source_system", "delivery_id")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def enforce_payload_size(self):
        if json_payload_size(self.records, MAX_DELIVERY_BYTES) > MAX_DELIVERY_BYTES:
            raise ValueError(f"Delivery payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


class CsvIngestRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=50)
    delivery_id: str = Field(min_length=1, max_length=255)
    csv_data: str = Field(max_length=MAX_DELIVERY_BYTES)
    profile_id: str | None = None

    @field_validator("source_system", "delivery_id")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def enforce_payload_size(self):
        if len(self.csv_data.encode("utf-8")) > MAX_DELIVERY_BYTES:
            raise ValueError(f"CSV payload exceeds {MAX_DELIVERY_BYTES} bytes")
        return self


def validate_profile_mapping(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    normalized = {}
    for canonical, source_column in value.items():
        target, source = canonical.strip(), source_column.strip()
        if not target or not source:
            raise ValueError("Column mapping keys and values must not be blank")
        if len(target) > 100 or len(source) > 100:
            raise ValueError("Column mapping keys and values must be at most 100 characters")
        if target in normalized:
            raise ValueError("Column mapping keys must remain unique after trimming")
        normalized[target] = source
    return normalized


def parse_csv_delivery(csv_data: str, mapping: dict[str, str] | None = None) -> list[dict[str, str | None]]:
    """Parse a structurally unambiguous CSV within the configured record cap."""
    rows = []
    try:
        reader = csv.DictReader(io.StringIO(csv_data), strict=True)
        headers = reader.fieldnames
        if not headers or any(not header.strip() for header in headers):
            raise ValueError("CSV must contain non-empty column headers")
        if len(headers) != len(set(headers)):
            raise ValueError("CSV column headers must be unique")
        for index, row in enumerate(reader):
            if index >= MAX_DELIVERY_RECORDS:
                raise ValueError(f"CSV exceeds the {MAX_DELIVERY_RECORDS:,}-record delivery limit")
            if None in row:
                raise ValueError(f"CSV row {index + 2} has more fields than its header")
            rows.append(
                {canonical: row.get(source_column) for canonical, source_column in mapping.items()}
                if mapping else row
            )
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV structure: {exc}") from exc
    if not rows:
        raise ValueError("CSV contains no data rows")
    return rows


class SourceFeedProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source_system: str = Field(min_length=1, max_length=32)
    schedule: str | None = Field(default=None, max_length=128)
    column_mapping: dict[str, str] = Field(default_factory=dict, max_length=100)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_profile_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Profile name cannot be blank")
        return normalized

    @field_validator("schedule")
    @classmethod
    def normalize_schedule(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("column_mapping")
    @classmethod
    def validate_column_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_profile_mapping(value) or {}


class SourceFeedProfilePatch(BaseModel):
    schedule: str | None = Field(default=None, max_length=128)
    column_mapping: dict[str, str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def normalize_schedule(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("column_mapping")
    @classmethod
    def validate_column_mapping(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_profile_mapping(value)

    @model_validator(mode="after")
    def reject_null_mapping(self):
        if "column_mapping" in self.model_fields_set and self.column_mapping is None:
            raise ValueError("column_mapping must be an object when provided")
        return self


class ReconciliationPolicyRequest(BaseModel):
    amount_tolerance: Decimal = Decimal("0")
    amount_tolerance_currency: str = "USD"
    settlement_day_tolerance: int = 0
    gap_min_count: int = 10
    gap_share: Decimal = Decimal("0.1")


class ManualMatchRequest(BaseModel):
    transaction_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(min_length=2, max_length=100)
    match_type: str = Field(default="Manual Match", min_length=1, max_length=50)
    rationale: str = Field(min_length=1, max_length=2000)



def schedule_window_hours(schedule: str | None) -> int | None:
    """Interpret a small, explicit cadence vocabulary without guessing cron syntax."""
    value = (schedule or "").strip().lower()
    if not value:
        return None
    if "hour" in value:
        return 2
    if "daily" in value or "day" in value:
        return 26
    if "weekly" in value or "week" in value:
        return 8 * 24
    return None


class SourceSettingPatch(BaseModel):
    enabled: bool | None = None
    in_daily_batch: bool | None = None
    manual_sync: bool | None = None
    sync_frequency: Literal["off", "daily", "weekly"] | None = None
    sync_time: str | None = None          # HH:MM, 24 hour
    sync_weekday: int | None = Field(default=None, ge=0, le=6)   # 0 = Monday
    sync_timezone: str | None = Field(default=None, max_length=64)

    @field_validator("sync_time")
    @classmethod
    def valid_time(cls, v):
        if v is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("sync_time must be HH:MM in 24 hour time")
        return v

    @field_validator("sync_timezone")
    @classmethod
    def known_zone(cls, v):
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("sync_timezone must be an IANA time zone such as Europe/London") from exc
        return v


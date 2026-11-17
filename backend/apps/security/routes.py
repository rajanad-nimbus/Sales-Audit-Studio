"""Identity and access information endpoints."""

import csv
import hashlib
import io
import json
import uuid
from datetime import date, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from model_base import utcnow
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import exists, func, or_, select, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cash.models import CashControl
from apps.controls.foundation.models import FoundationReference, StoreLocationTrait
from apps.csv_export import csv_row
from apps.security import access, scope
from apps.security import authentication as auth
from apps.security.authentication import (
    ASSIGNABLE_ROLES,
    STORE_ROLES,
    current_user,
    require_role,
)
from apps.security.models import (
    StoreAssignment,
    UserPreference,
    UserRoleAssignment,
    UserSavedViews,
    VerifiedStoreUser,
)
from apps.store_days.models import StoreDay
from apps.transactions.models import CanonicalTransaction

router = APIRouter()


class UserPreferenceInput(BaseModel):
    locale: Literal["en-US", "en-GB", "en-CA", "fr-FR", "de-DE", "ja-JP"] = "en-US"
    time_zone: str = Field(default="UTC", min_length=1, max_length=64)
    density: Literal["comfortable", "compact"] = "comfortable"
    contrast: Literal["standard", "high"] = "standard"
    motion: Literal["system", "reduce"] = "system"
    theme: Literal["system", "light", "dark"] = "system"

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA time zone") from None
        return normalized


class ActionCenterSavedViewSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sort: Literal[
        "sla",
        "exposure",
        "amount",
        "age",
        "store",
        "type",
        "date",
        "case_number",
        "status",
        "priority",
        "evidence",
    ] = "sla"
    dir: Literal["asc", "desc"] = "asc"


class ActionCenterSavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    f: dict[str, str] = Field(default_factory=dict)
    sort: ActionCenterSavedViewSort = Field(default_factory=ActionCenterSavedViewSort)
    groupBy: Literal["", "case_type", "store", "day", "priority", "assignee"] = ""
    pageSize: Literal[20, 50, 100] = 50

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("f")
    @classmethod
    def valid_filters(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "q",
            "stage",
            "case_type",
            "store",
            "priority",
            "assignee",
            "sla",
            "min_amount",
            "date_from",
            "date_to",
            "snoozed",
        }
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported filter")
        if any(len(item) > 200 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 200 characters")
        return value


class ActionCenterSavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[ActionCenterSavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[ActionCenterSavedView]) -> list[ActionCenterSavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class TransactionSavedViewSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sort: Literal[
        "business_date",
        "event_timestamp",
        "amount",
        "store",
        "type",
        "status",
        "tender",
        "source",
    ] = "business_date"
    dir: Literal["asc", "desc"] = "desc"


class TransactionSavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    f: dict[str, str] = Field(default_factory=dict)
    sort: TransactionSavedViewSort = Field(default_factory=TransactionSavedViewSort)
    pageSize: Literal[25, 50, 100, 200] = 50

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("f")
    @classmethod
    def valid_filters(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "q",
            "store_id",
            "source_system",
            "transaction_type",
            "tender_type",
            "status",
            "detail_status",
            "register_id",
            "cashier_id",
            "salesperson_id",
            "item_code",
            "date_from",
            "date_to",
        }
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported transaction filter")
        if any(len(item) > 255 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 255 characters")
        return value


class TransactionSavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[TransactionSavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[TransactionSavedView]) -> list[TransactionSavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class StoreDaysSavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "q",
            "store_id",
            "date_from",
            "date_to",
            "status",
            "tab",
            "page_size",
            "page",
            "late_date",
            "late_page",
            "late_size",
            "stuck_after",
        }
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported Store Days filter")
        if any(len(item) > 255 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 255 characters")
        if value.get("tab", "days") not in {"days", "late", "stuck"}:
            raise ValueError("Saved view tab must be days, late, or stuck")
        if value.get("page_size", "50") not in {"25", "50", "100", "200"}:
            raise ValueError("Saved view page size is not supported")
        if value.get("late_size", "100") not in {"50", "100", "200", "500"}:
            raise ValueError("Saved view late-store page size is not supported")
        if value.get("stuck_after", "3") not in {"1", "3", "7", "14"}:
            raise ValueError("Saved view stuck-day threshold is not supported")
        for key in ("page", "late_page"):
            if key in value and (not value[key].isdigit() or not 1 <= int(value[key]) <= 100_000):
                raise ValueError(f"Saved view {key} must be between 1 and 100000")
        for key in ("date_from", "date_to", "late_date"):
            if key in value and value[key]:
                try:
                    date.fromisoformat(value[key])
                except ValueError as exc:
                    raise ValueError(f"Saved view {key} must be an ISO business date") from exc
        date_from, date_to = value.get("date_from"), value.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise ValueError("Saved view date_from must not be after date_to")
        return value


class StoreDaysSavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[StoreDaysSavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[StoreDaysSavedView]) -> list[StoreDaysSavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class AuditErrorsSavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"q", "show", "days", "family"}
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported Audit Errors filter")
        if any(len(item) > 255 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 255 characters")
        if value.get("show", "open") not in {"open", "all"}:
            raise ValueError("Saved view error scope must be open or all")
        if value.get("days", "90") not in {"30", "90", "365", "0"}:
            raise ValueError("Saved view raised-in window is not supported")
        return value


class AuditErrorsSavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[AuditErrorsSavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[AuditErrorsSavedView]) -> list[AuditErrorsSavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class ActivitySavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    params: dict[str, str | bool] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, str | bool]) -> dict[str, str | bool]:
        allowed = {"agent", "q", "alerts_only"}
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported Agent Activity filter")
        if not isinstance(value.get("agent", "all"), str) or value.get("agent", "all") not in {
            "all",
            "reconciliation",
            "investigation",
            "policy",
            "orchestrator",
            "resolution",
            "validation",
            "connectors",
            "people",
        }:
            raise ValueError("Saved view actor filter is not supported")
        search = value.get("q", "")
        if not isinstance(search, str) or len(search) > 255:
            raise ValueError("Saved view search must be at most 255 characters")
        if "alerts_only" in value and not isinstance(value["alerts_only"], bool):
            raise ValueError("Saved view alerts_only filter must be a boolean")
        return value


class ActivitySavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[ActivitySavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[ActivitySavedView]) -> list[ActivitySavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class CasesSavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"q", "status", "priority", "sort", "dir", "page", "page_size"}
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported Cases filter")
        if any(len(item) > 255 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 255 characters")
        if value.get("priority", "") not in {"", "Critical", "High", "Normal"}:
            raise ValueError("Saved view priority is not supported")
        if value.get("sort", "exposure") not in {
            "case_number",
            "type",
            "store",
            "amount",
            "exposure",
            "evidence",
            "priority",
            "status",
        }:
            raise ValueError("Saved view sort field is not supported")
        if value.get("dir", "desc") not in {"asc", "desc"}:
            raise ValueError("Saved view sort direction must be asc or desc")
        if value.get("page_size", "50") not in {"25", "50", "100", "200"}:
            raise ValueError("Saved view page size is not supported")
        if "page" in value and (
            not value["page"].isdigit() or not 1 <= int(value["page"]) <= 100_000
        ):
            raise ValueError("Saved view page must be between 1 and 100000")
        return value


class CasesSavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[CasesSavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[CasesSavedView]) -> list[CasesSavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class SummarySavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"store_id", "date_from", "date_to", "q"}
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported sales summary filter")
        if any(len(item) > 255 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 255 characters")
        for key in ("date_from", "date_to"):
            if key in value:
                try:
                    normalized = date.fromisoformat(value[key]).isoformat()
                except ValueError as exc:
                    raise ValueError(f"{key} must use YYYY-MM-DD") from exc
                if normalized != value[key]:
                    raise ValueError(f"{key} must use YYYY-MM-DD")
        if "store_id" in value and not value["store_id"].strip():
            raise ValueError("Saved view store cannot be blank")
        return value


class SummarySavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[SummarySavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[SummarySavedView]) -> list[SummarySavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class RegisterSummarySavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A saved view name is required")
        return normalized

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"store_id", "business_date", "group_by"}
        if set(value) - allowed:
            raise ValueError("Saved view contains an unsupported register summary filter")
        if not value.get("store_id") or not value.get("business_date"):
            raise ValueError("Saved register views must include a store and business date")
        if any(len(item) > 255 for item in value.values()):
            raise ValueError("Saved view filter values must be at most 255 characters")
        if "business_date" in value:
            try:
                normalized = date.fromisoformat(value["business_date"]).isoformat()
            except ValueError as exc:
                raise ValueError("business_date must use YYYY-MM-DD") from exc
            if normalized != value["business_date"]:
                raise ValueError("business_date must use YYYY-MM-DD")
        if "store_id" in value and not value["store_id"].strip():
            raise ValueError("Saved view store cannot be blank")
        if value.get("group_by", "register") not in {"register", "cashier"}:
            raise ValueError("Saved view group_by must be register or cashier")
        return value


class RegisterSummarySavedViewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[RegisterSummarySavedView] = Field(max_length=30)

    @field_validator("views")
    @classmethod
    def unique_names(cls, values: list[RegisterSummarySavedView]) -> list[RegisterSummarySavedView]:
        names = [view.name.casefold() for view in values]
        if len(names) != len(set(names)):
            raise ValueError("Saved view names must be unique")
        return values


class SavedViewsEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[dict] = Field(max_length=30)


def user_preference_identifier(user: dict) -> str:
    """Keep preferences attached to the verified identity during role impersonation."""
    identity = user.get("ontology_user_id") or user.get("user")
    if identity is None or not str(identity).strip():
        raise HTTPException(
            status_code=400, detail="Your signed-in identity has no stable identifier"
        )
    return str(identity).strip().casefold()


def user_preference_view(row: UserPreference | None) -> dict:
    return {
        "locale": row.locale if row else "en-US",
        "time_zone": row.time_zone if row else "UTC",
        "density": row.density if row else "comfortable",
        "contrast": row.contrast if row else "standard",
        "motion": row.motion if row else "system",
        "theme": row.theme if row else "system",
        "updated_at": row.updated_at.isoformat() if row else None,
    }


class StoreAssignmentInput(BaseModel):
    user_identifier: str = Field(min_length=1, max_length=255)
    store_ids: list[str] = Field(max_length=500)
    location_traits: list[str] | None = Field(default=None, max_length=100)

    @field_validator("user_identifier")
    @classmethod
    def normalize_user_identifier(cls, value: str) -> str:
        value = value.strip().casefold()
        if not value:
            raise ValueError("user_identifier must not be blank")
        return value

    @field_validator("store_ids")
    @classmethod
    def validate_store_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(
            not value or value != original
            for value, original in zip(normalized, values, strict=True)
        ):
            raise ValueError(
                "store_ids must contain non-blank store codes without surrounding whitespace"
            )
        if len(set(normalized)) != len(normalized):
            raise ValueError("store_ids must not contain duplicates")
        if any(len(value) > 50 for value in normalized):
            raise ValueError("store codes must be at most 50 characters")
        return normalized

    @field_validator("location_traits")
    @classmethod
    def validate_location_traits(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("location_traits must contain nonblank trait codes")
        if len(set(normalized)) != len(normalized):
            raise ValueError("location_traits must not contain duplicates")
        if any(len(value) > 100 for value in normalized):
            raise ValueError("location trait codes must be at most 100 characters")
        return normalized


class StoreAssignmentClearInput(BaseModel):
    user_identifier: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("user_identifier")
    @classmethod
    def normalize_user_identifier(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("user_identifier must not be blank")
        return normalized

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("Provide a reason with at least 10 characters")
        return normalized


class StoreAssignmentImportBody(BaseModel):
    csv_text: str = Field(min_length=1, max_length=1_500_000)
    preview_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def parse_store_assignment_csv(csv_text: str) -> tuple[list[dict], list[dict]]:
    """Validate a bounded CSV into normalized store-assignment replacements."""
    errors: list[dict] = []
    rows: list[dict] = []
    seen: set[str] = set()
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    headers = [header.strip().lower() if header else "" for header in (reader.fieldnames or [])]
    required = {"assignment_identifier", "store_ids", "location_traits"}
    if len(headers) != 3 or len(set(headers)) != 3 or set(headers) != required:
        return [], [
            {
                "row": 1,
                "message": "CSV headers must be assignment_identifier, store_ids, location_traits",
            }
        ]
    reader.fieldnames = headers
    try:
        for row_number, raw in enumerate(reader, start=2):
            if None in raw:
                errors.append({"row": row_number, "message": "Row has more values than the header"})
                continue
            identifier = (raw.get("assignment_identifier") or "").strip().casefold()
            stores = [
                value.strip() for value in (raw.get("store_ids") or "").split(";") if value.strip()
            ]
            traits = [
                value.strip()
                for value in (raw.get("location_traits") or "").split(";")
                if value.strip()
            ]
            try:
                normalized = StoreAssignmentInput.model_validate(
                    {
                        "user_identifier": identifier,
                        "store_ids": stores,
                        "location_traits": traits,
                    }
                )
            except ValueError as exc:
                errors.append({"row": row_number, "message": str(exc)})
                continue
            if normalized.user_identifier in seen:
                errors.append(
                    {
                        "row": row_number,
                        "message": f"Duplicate user identifier {normalized.user_identifier}",
                    }
                )
                continue
            seen.add(normalized.user_identifier)
            rows.append({**normalized.model_dump(), "row": row_number})
            if len(rows) > 5000:
                errors.append(
                    {"row": row_number, "message": "A file may contain at most 5,000 rows"}
                )
                break
    except csv.Error as exc:
        errors.append({"row": reader.line_num, "message": f"Invalid CSV: {exc}"})
    if not rows and not errors:
        errors.append({"row": 1, "message": "CSV contains no assignment rows"})
    return rows, errors


async def preview_store_assignment_rows(db: AsyncSession, rows: list[dict], *, lock: bool = False):
    """Validate referenced stores and traits and fingerprint the assignment state."""
    known_stores = set(await known_store_codes(db))
    known_traits = set(
        (await db.execute(select(StoreLocationTrait.location_trait).distinct())).scalars().all()
    )
    errors = []
    preview_rows = []
    for row in rows:
        row_number = row["row"]
        unknown_stores = sorted(set(row["store_ids"]) - known_stores)
        unknown_traits = sorted(set(row["location_traits"]) - known_traits)
        if unknown_stores:
            errors.append(
                {"row": row_number, "message": f"Unknown stores: {', '.join(unknown_stores)}"}
            )
        if unknown_traits:
            errors.append(
                {
                    "row": row_number,
                    "message": f"Unknown or unassigned location traits: {', '.join(unknown_traits)}",
                }
            )
    identifiers = [row["user_identifier"] for row in rows]
    query = select(StoreAssignment).where(StoreAssignment.user_identifier.in_(identifiers))
    if lock:
        query = query.with_for_update()
    existing = (await db.execute(query)).scalars().all() if identifiers else []
    by_identifier = {row.user_identifier: row for row in existing}
    verified_roles = (
        dict(
            (
                await db.execute(
                    select(
                        VerifiedStoreUser.assignment_identifier,
                        VerifiedStoreUser.role,
                    ).where(VerifiedStoreUser.assignment_identifier.in_(identifiers))
                )
            ).all()
        )
        if identifiers
        else {}
    )
    snapshot = []
    counts = {"Create": 0, "Replace": 0, "Unchanged": 0}
    for row in rows:
        current = by_identifier.get(row["user_identifier"])
        if current is None:
            verified_role = verified_roles.get(row["user_identifier"])
            if verified_role is None:
                errors.append(
                    {
                        "row": row["row"],
                        "message": "User has not signed in through Ontology Studio yet; assign access after their first sign-in",
                    }
                )
            elif verified_role not in STORE_ROLES:
                errors.append(
                    {
                        "row": row["row"],
                        "message": "Store assignments are only valid for store managers and store staff",
                    }
                )
        before = (
            {
                "id": current.id,
                "store_ids": current.store_ids or [],
                "location_traits": current.location_traits or [],
                "updated_at": current.updated_at.isoformat() if current.updated_at else None,
            }
            if current
            else None
        )
        if current is None:
            action = "Create"
        elif (
            before["store_ids"] == row["store_ids"]
            and before["location_traits"] == row["location_traits"]
        ):
            action = "Unchanged"
        else:
            action = "Replace"
        counts[action] += 1
        snapshot.append({"input": row, "before": before})
        preview_rows.append({**row, "action": action})
    token = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return preview_rows, counts, errors, token, by_identifier


def store_assignment_view(row: StoreAssignment) -> dict:
    return {
        "id": row.id,
        "user_identifier": row.user_identifier,
        "store_ids": row.store_ids or [],
        "location_traits": row.location_traits or [],
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat(),
    }


async def known_store_codes(db: AsyncSession, user: dict | None = None) -> list[str]:
    store_codes = union(
        select(FoundationReference.code.label("store_id")).where(
            FoundationReference.category == "Store",
            FoundationReference.active.is_(True),
            FoundationReference.code.is_not(None),
        ),
        select(CanonicalTransaction.store_id.label("store_id")).where(
            CanonicalTransaction.store_id.is_not(None),
        ),
        select(StoreDay.store_id.label("store_id")).where(StoreDay.store_id.is_not(None)),
    ).subquery()
    query = select(store_codes.c.store_id)
    if user is not None:
        query = scope.restrict(query, user, store_codes.c.store_id)
    codes = (await db.execute(query.order_by(store_codes.c.store_id))).scalars().all()
    return [code for code in codes if code]


@router.get("/api/me")
async def me(user: dict = Depends(current_user)):
    """Return identity and the screens available to the signed-in user."""
    public_user = {key: value for key, value in user.items() if key != "store_scope_store_ids"}
    return {
        **public_user,
        "role_label": access.ROLE_LABELS.get(user["role"], user["role"]),
        "screens": access.screens_for(user["role"]),
    }


@router.get("/api/me/preferences")
async def get_my_preferences(
    db: AsyncSession = Depends(get_db), user: dict = Depends(current_user)
):
    identifier = user_preference_identifier(user)
    row = (
        await db.execute(select(UserPreference).where(UserPreference.user_identifier == identifier))
    ).scalar_one_or_none()
    return user_preference_view(row)


@router.put("/api/me/preferences")
async def update_my_preferences(
    body: UserPreferenceInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    identifier = user_preference_identifier(user)
    row = (
        await db.execute(
            select(UserPreference)
            .where(UserPreference.user_identifier == identifier)
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = utcnow()
    if row is None:
        row = UserPreference(user_identifier=identifier, **body.model_dump(), updated_at=now)
        db.add(row)
    else:
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        row.updated_at = now
    await db.commit()
    await db.refresh(row)
    return user_preference_view(row)


@router.get("/api/me/saved-views/{scope_name}")
async def get_my_saved_views(
    scope_name: Literal[
        "action_center",
        "transactions",
        "store_days",
        "audit_errors",
        "activity",
        "cases",
        "item_summary",
        "tender_summary",
        "misc_totals",
        "register_summary",
    ],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    identifier = user_preference_identifier(user)
    row = (
        await db.execute(
            select(UserSavedViews).where(
                UserSavedViews.user_identifier == identifier,
                UserSavedViews.scope == scope_name,
            )
        )
    ).scalar_one_or_none()
    return {"views": (row.views if row else None) or []}


@router.put("/api/me/saved-views/{scope_name}")
async def update_my_saved_views(
    scope_name: Literal[
        "action_center",
        "transactions",
        "store_days",
        "audit_errors",
        "activity",
        "cases",
        "item_summary",
        "tender_summary",
        "misc_totals",
        "register_summary",
    ],
    body: SavedViewsEnvelope,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    identifier = user_preference_identifier(user)
    row = (
        await db.execute(
            select(UserSavedViews)
            .where(
                UserSavedViews.user_identifier == identifier,
                UserSavedViews.scope == scope_name,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    contracts = {
        "action_center": ActionCenterSavedViewsInput,
        "transactions": TransactionSavedViewsInput,
        "store_days": StoreDaysSavedViewsInput,
        "audit_errors": AuditErrorsSavedViewsInput,
        "activity": ActivitySavedViewsInput,
        "cases": CasesSavedViewsInput,
        "item_summary": SummarySavedViewsInput,
        "tender_summary": SummarySavedViewsInput,
        "misc_totals": SummarySavedViewsInput,
        "register_summary": RegisterSummarySavedViewsInput,
    }
    try:
        validated = contracts[scope_name].model_validate({"views": body.views})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    views = [view.model_dump() for view in validated.views]
    if row is None:
        row = UserSavedViews(
            user_identifier=identifier,
            scope=scope_name,
            views=views,
            updated_at=utcnow(),
        )
        db.add(row)
    else:
        row.views = views
        row.updated_at = utcnow()
    await db.commit()
    return {"views": views}


@router.post("/api/auth/logout")
async def logout(request: Request, user: dict = Depends(current_user)):
    """End the upstream session when possible and always clear the local cookie."""
    ended = False
    cookie = request.cookies.get("app_session_id")
    base = auth.ONTOLOGY_AUTH_URL.rsplit("/auth/", 1)[0] if auth.ONTOLOGY_AUTH_URL else ""
    if base and cookie:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                ended = (
                    await client.post(
                        f"{base}/auth/logout", headers={"Cookie": f"app_session_id={cookie}"}
                    )
                ).status_code < 400
        except httpx.HTTPError:
            pass
    response = JSONResponse({"success": True, "ontology_session_ended": ended})
    response.delete_cookie("app_session_id", path="/")
    return response


@router.get("/api/access/matrix")
async def access_matrix(user: dict = Depends(current_user)):
    return {**access.matrix(), "you": user["role"]}


@router.get("/api/access/store-assignments")
async def list_store_assignments(
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=50, ge=1, le=200),
    format: Literal["json", "csv"] = "json",
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("admin")),
):
    filters = []
    normalized_query = (q or "").strip().lower()
    if normalized_query:
        filters.append(
            func.lower(StoreAssignment.user_identifier).contains(normalized_query, autoescape=True)
        )
    total = await db.scalar(select(func.count()).select_from(StoreAssignment).where(*filters))

    if format == "csv":
        row_limit = 50_000

        async def generate():
            yield csv_row(
                (
                    "assignment_identifier",
                    "store_ids",
                    "location_traits",
                    "updated_by",
                    "updated_at",
                )
            )
            result = await db.stream(
                select(StoreAssignment)
                .where(*filters)
                .order_by(StoreAssignment.user_identifier)
                .limit(row_limit)
                .execution_options(yield_per=1000)
            )
            try:
                async for (row,) in result:
                    yield csv_row(
                        (
                            row.user_identifier,
                            ";".join(row.store_ids),
                            ";".join(row.location_traits),
                            row.updated_by,
                            row.updated_at.isoformat(),
                        )
                    )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=store-assignments.csv",
                "X-Store-Assignments-Truncated": "true" if total > row_limit else "false",
            },
        )

    rows = (
        (
            await db.execute(
                select(StoreAssignment)
                .where(*filters)
                .order_by(StoreAssignment.user_identifier)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [store_assignment_view(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/api/access/verified-store-users")
async def list_verified_store_users(
    q: str | None = Query(default=None, max_length=100),
    role: Literal["all", "store_manager", "store_staff"] = "all",
    scope_status: Literal["all", "assigned", "unassigned"] = "all",
    inactive_days: int | None = Query(default=None, ge=1, le=3650),
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=50, ge=1, le=200),
    format: Literal["json", "csv"] = "json",
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("admin")),
):
    has_local_scope = exists(
        select(StoreAssignment.id).where(
            or_(
                StoreAssignment.user_identifier == VerifiedStoreUser.assignment_identifier,
                StoreAssignment.user_identifier == func.lower(VerifiedStoreUser.user_identifier),
            )
        )
    )
    filters = [VerifiedStoreUser.role.in_(STORE_ROLES)]
    if inactive_days is not None:
        filters.append(VerifiedStoreUser.last_seen_at < utcnow() - timedelta(days=inactive_days))
    if role != "all":
        filters.append(VerifiedStoreUser.role == role)
    normalized_query = (q or "").strip().lower()
    if normalized_query:
        filters.append(
            or_(
                func.lower(VerifiedStoreUser.display_name).contains(
                    normalized_query, autoescape=True
                ),
                func.lower(VerifiedStoreUser.user_identifier).contains(
                    normalized_query, autoescape=True
                ),
                func.lower(VerifiedStoreUser.assignment_identifier).contains(
                    normalized_query, autoescape=True
                ),
                func.lower(VerifiedStoreUser.identity_id).contains(
                    normalized_query, autoescape=True
                ),
            )
        )
    if scope_status == "assigned":
        filters.append(has_local_scope)
    elif scope_status == "unassigned":
        filters.append(~has_local_scope)
    total = await db.scalar(select(func.count()).select_from(VerifiedStoreUser).where(*filters))
    if format == "csv":
        row_limit = 50_000

        async def generate():
            yield csv_row(
                (
                    "assignment_identifier",
                    "user_identifier",
                    "display_name",
                    "role",
                    "identity_id",
                    "local_scope",
                    "first_seen_at",
                    "last_seen_at",
                )
            )
            result = await db.stream(
                select(VerifiedStoreUser, has_local_scope.label("has_local_scope"))
                .where(*filters)
                .order_by(VerifiedStoreUser.display_name, VerifiedStoreUser.assignment_identifier)
                .limit(row_limit)
                .execution_options(yield_per=1000)
            )
            try:
                async for row, assigned in result:
                    yield csv_row(
                        (
                            row.assignment_identifier,
                            row.user_identifier,
                            row.display_name,
                            row.role,
                            row.identity_id,
                            "Assigned" if assigned else "Identity claims",
                            row.first_seen_at.isoformat(),
                            row.last_seen_at.isoformat(),
                        )
                    )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=verified-store-employees.csv",
                "X-Verified-Users-Truncated": "true" if total > row_limit else "false",
            },
        )

    rows = (
        await db.execute(
            select(VerifiedStoreUser, has_local_scope.label("has_local_scope"))
            .where(*filters)
            .order_by(VerifiedStoreUser.display_name, VerifiedStoreUser.assignment_identifier)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [
            {
                "assignment_identifier": row.assignment_identifier,
                "user_identifier": row.user_identifier,
                "identity_id": row.identity_id,
                "display_name": row.display_name,
                "role": row.role,
                "first_seen_at": row.first_seen_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "has_local_scope": assigned,
            }
            for row, assigned in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.post("/api/access/store-assignments/import/preview")
async def preview_store_assignment_import(
    body: StoreAssignmentImportBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("admin")),
):
    rows, errors = parse_store_assignment_csv(body.csv_text)
    if not errors:
        preview, counts, reference_errors, token, _ = await preview_store_assignment_rows(db, rows)
        errors.extend(reference_errors)
    else:
        preview, counts, token = [], {"Create": 0, "Replace": 0, "Unchanged": 0}, None
    return {
        "valid": not errors and bool(rows),
        "preview_token": token if not errors else None,
        "rows": preview,
        "counts": counts,
        "errors": errors[:100],
        "errors_truncated": len(errors) > 100,
    }


@router.post("/api/access/store-assignments/import")
async def apply_store_assignment_import(
    body: StoreAssignmentImportBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    if not body.preview_token:
        raise HTTPException(status_code=422, detail="Preview this CSV before applying it")
    rows, errors = parse_store_assignment_csv(body.csv_text)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors[:100]})
    preview, counts, reference_errors, token, existing = await preview_store_assignment_rows(
        db, rows, lock=True
    )
    if reference_errors:
        raise HTTPException(status_code=422, detail={"errors": reference_errors[:100]})
    if token != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Store assignments changed after preview. Preview the file again before applying it.",
        )
    actor = user.get("user") or "ontology.user"
    now = utcnow()
    applied = {"created": 0, "replaced": 0, "unchanged": counts["Unchanged"]}
    for row_preview in preview:
        identifier = row_preview["user_identifier"]
        row = existing.get(identifier)
        if row_preview["action"] == "Unchanged":
            continue
        before = (
            {
                "user_identifier": row.user_identifier,
                "store_ids": row.store_ids or [],
                "location_traits": row.location_traits or [],
            }
            if row
            else None
        )
        if row is None:
            row = StoreAssignment(
                id=str(uuid.uuid4()),
                user_identifier=identifier,
                store_ids=row_preview["store_ids"],
                location_traits=row_preview["location_traits"],
                updated_by=actor,
                updated_at=now,
            )
            db.add(row)
            applied["created"] += 1
        else:
            row.store_ids = row_preview["store_ids"]
            row.location_traits = row_preview["location_traits"]
            row.updated_by = actor
            row.updated_at = now
            applied["replaced"] += 1
        db.add(
            AuditEvent(
                id=str(uuid.uuid4()),
                event_type="STORE_ASSIGNMENT_REPLACED",
                actor=actor,
                object_type="StoreAssignment",
                object_id=row.id,
                action_description=f"Store scope imported for {identifier}",
                before_state=before,
                after_state={
                    "user_identifier": identifier,
                    "store_ids": row.store_ids or [],
                    "location_traits": row.location_traits or [],
                    "import_source": "CSV",
                },
            )
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An assignment changed while the import was being applied. Preview the file again.",
        ) from exc
    return {"applied": applied}


@router.put("/api/access/store-assignments")
async def replace_store_assignment(
    body: StoreAssignmentInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    known = set(await known_store_codes(db))
    unknown = sorted(set(body.store_ids) - known)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown store codes: {', '.join(unknown)}",
        )
    known_traits = set(
        (await db.execute(select(StoreLocationTrait.location_trait).distinct())).scalars().all()
    )
    requested_traits = body.location_traits or []
    unknown_traits = sorted(set(requested_traits) - known_traits)
    if unknown_traits:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or unassigned location traits: {', '.join(unknown_traits)}",
        )
    row = (
        await db.execute(
            select(StoreAssignment)
            .where(StoreAssignment.user_identifier == body.user_identifier)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        verified = await db.get(VerifiedStoreUser, body.user_identifier)
        if verified is None:
            raise HTTPException(
                status_code=422,
                detail="This identity has not signed in through Ontology Studio yet. Ask them to sign in before assigning store access.",
            )
        if verified.role not in STORE_ROLES:
            raise HTTPException(
                status_code=422,
                detail="Store assignments are only valid for store managers and store staff.",
            )
    before = (
        {
            "user_identifier": row.user_identifier,
            "store_ids": row.store_ids or [],
            "location_traits": row.location_traits or [],
        }
        if row
        else None
    )
    if row:
        row.store_ids = body.store_ids
        if body.location_traits is not None:
            row.location_traits = body.location_traits
        row.updated_by = user.get("user") or "ontology.user"
        row.updated_at = utcnow()
    else:
        row = StoreAssignment(
            id=str(uuid.uuid4()),
            user_identifier=body.user_identifier,
            store_ids=body.store_ids,
            location_traits=requested_traits,
            updated_by=user.get("user") or "ontology.user",
            updated_at=utcnow(),
        )
        db.add(row)
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="STORE_ASSIGNMENT_REPLACED",
            actor=user.get("user") or "ontology.user",
            object_type="StoreAssignment",
            object_id=row.id,
            action_description=f"Store scope assigned to {body.user_identifier}",
            before_state=before,
            after_state={
                "user_identifier": body.user_identifier,
                "store_ids": body.store_ids,
                "location_traits": row.location_traits or [],
            },
        )
    )
    await db.commit()
    await db.refresh(row)
    return store_assignment_view(row)


@router.post("/api/access/store-assignments/clear")
async def clear_store_assignment(
    body: StoreAssignmentClearInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    row = (
        await db.execute(
            select(StoreAssignment)
            .where(StoreAssignment.user_identifier == body.user_identifier)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail="No local store assignment exists for this user"
        )
    before = {
        "user_identifier": row.user_identifier,
        "store_ids": row.store_ids or [],
        "location_traits": row.location_traits or [],
    }
    actor = user.get("user") or "ontology.user"
    await db.delete(row)
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="STORE_ASSIGNMENT_CLEARED",
            actor=actor,
            object_type="StoreAssignment",
            object_id=row.id,
            action_description=f"Cleared local store assignment for {row.user_identifier}: {body.reason}",
            before_state=before,
            after_state={"user_identifier": row.user_identifier, "cleared": True},
        )
    )
    await db.commit()
    return {"user_identifier": body.user_identifier, "cleared": True}


class RoleAssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_identifier: str = Field(min_length=1, max_length=255)
    role: Literal[
        "finance",
        "it",
        "analyst",
        "sales_audit_manager",
        "auditor",
        "store_manager",
        "store_staff",
    ]
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("assignment_identifier")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("assignment_identifier must not be blank")
        return normalized

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Reason must contain at least 10 non-whitespace characters")
        return value


class RoleAssignmentImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv_text: str = Field(min_length=1, max_length=1_500_000)
    preview_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def parse_role_assignment_csv(csv_text: str) -> tuple[list[dict], list[dict]]:
    """Parse a bounded role import, requiring an auditable reason on every row."""
    errors: list[dict] = []
    rows: list[dict] = []
    seen: set[str] = set()
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    headers = [header.strip().lower() if header else "" for header in (reader.fieldnames or [])]
    required = {"assignment_identifier", "role", "reason"}
    if len(headers) != 3 or len(set(headers)) != 3 or set(headers) != required:
        return [], [
            {
                "row": 1,
                "message": "CSV headers must be assignment_identifier, role, reason",
            }
        ]
    reader.fieldnames = headers
    try:
        for row_number, raw in enumerate(reader, start=2):
            if None in raw:
                errors.append({"row": row_number, "message": "Row has more values than the header"})
                continue
            try:
                normalized = RoleAssignmentInput.model_validate(
                    {
                        "assignment_identifier": raw.get("assignment_identifier") or "",
                        "role": (raw.get("role") or "").strip().lower(),
                        "reason": raw.get("reason") or "",
                    }
                )
            except ValueError as exc:
                errors.append({"row": row_number, "message": str(exc)})
                continue
            if normalized.assignment_identifier in seen:
                errors.append(
                    {
                        "row": row_number,
                        "message": f"Duplicate identity {normalized.assignment_identifier}",
                    }
                )
                continue
            seen.add(normalized.assignment_identifier)
            rows.append({**normalized.model_dump(), "row": row_number})
            if len(rows) > 5000:
                errors.append(
                    {"row": row_number, "message": "A file may contain at most 5,000 rows"}
                )
                break
    except csv.Error as exc:
        errors.append({"row": reader.line_num, "message": f"Invalid CSV: {exc}"})
    if not rows and not errors:
        errors.append({"row": 1, "message": "CSV contains no role assignments"})
    return rows, errors


async def preview_role_assignment_rows(db: AsyncSession, rows: list[dict], *, lock: bool = False):
    """Check verified identities and fingerprint both their identity and assigned role state."""
    identifiers = [row["assignment_identifier"] for row in rows]
    people_query = select(VerifiedStoreUser).where(
        VerifiedStoreUser.assignment_identifier.in_(identifiers)
    )
    assignments_query = select(UserRoleAssignment).where(
        UserRoleAssignment.assignment_identifier.in_(identifiers)
    )
    if lock:
        people_query = people_query.with_for_update()
        assignments_query = assignments_query.with_for_update()
    people = (
        {
            person.assignment_identifier: person
            for person in (await db.execute(people_query)).scalars()
        }
        if identifiers
        else {}
    )
    assignments = (
        {
            assignment.assignment_identifier: assignment
            for assignment in (await db.execute(assignments_query)).scalars()
        }
        if identifiers
        else {}
    )
    errors: list[dict] = []
    preview_rows: list[dict] = []
    snapshot: list[dict] = []
    counts = {"Create": 0, "Replace": 0, "Unchanged": 0}
    for row in rows:
        identifier = row["assignment_identifier"]
        person = people.get(identifier)
        current = assignments.get(identifier)
        if person is None:
            errors.append(
                {
                    "row": row["row"],
                    "message": "Identity has not signed in through Ontology Studio yet; assign a role after their first sign-in",
                }
            )
        elif person.role == "admin":
            errors.append(
                {
                    "row": row["row"],
                    "message": "Studio administrators keep their Studio role and cannot receive a Nimbus business role",
                }
            )
        before = (
            {
                "role": current.role,
                "reason": current.reason,
                "assigned_at": current.assigned_at.isoformat() if current.assigned_at else None,
            }
            if current
            else None
        )
        action = (
            "Create"
            if current is None
            else "Unchanged"
            if current.role == row["role"] and current.reason == row["reason"]
            else "Replace"
        )
        counts[action] += 1
        preview_rows.append(
            {
                **row,
                "display_name": person.display_name if person else "",
                "user_identifier": person.user_identifier if person else "",
                "signed_in_role": person.role if person else "",
                "current_role": current.role if current else None,
                "action": action,
            }
        )
        snapshot.append(
            {
                "input": row,
                "identity": (
                    {
                        "identity_id": person.identity_id,
                        "user_identifier": person.user_identifier,
                        "role": person.role,
                    }
                    if person
                    else None
                ),
                "before": before,
            }
        )
    token = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return preview_rows, counts, errors, token, assignments, people


def role_assignment_view(person: VerifiedStoreUser, assignment: UserRoleAssignment | None) -> dict:
    return {
        "assignment_identifier": person.assignment_identifier,
        "user_identifier": person.user_identifier,
        "display_name": person.display_name,
        "last_seen_at": person.last_seen_at,
        "signed_in_role": person.role,
        "assigned_role": assignment.role if assignment else None,
        "assigned_by": assignment.assigned_by if assignment else None,
        "assigned_at": assignment.assigned_at if assignment else None,
        "reason": assignment.reason if assignment else None,
    }


@router.get("/api/access/role-assignments")
async def list_role_assignments(
    q: str | None = Query(default=None, max_length=100),
    assigned: Literal["all", "assigned", "unassigned"] = "all",
    assigned_role: Literal[
        "finance",
        "it",
        "analyst",
        "sales_audit_manager",
        "auditor",
        "store_manager",
        "store_staff",
    ]
    | None = None,
    inactive_days: int | None = Query(default=None, ge=1, le=3650),
    page: int = Query(default=1, ge=1, le=100_000),
    page_size: int = Query(default=50, ge=1, le=200),
    format: Literal["json", "csv"] = "json",
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("admin")),
):
    """People who have signed in through Ontology Studio, with the business role Nimbus gives each."""
    filters = []
    if inactive_days is not None:
        filters.append(VerifiedStoreUser.last_seen_at < utcnow() - timedelta(days=inactive_days))
    if assigned == "assigned":
        filters.append(UserRoleAssignment.assignment_identifier.is_not(None))
    elif assigned == "unassigned":
        filters.append(UserRoleAssignment.assignment_identifier.is_(None))
    if assigned_role is not None:
        filters.append(UserRoleAssignment.role == assigned_role)
    needle = (q or "").strip().lower()
    if needle:
        filters.append(
            or_(
                func.lower(VerifiedStoreUser.display_name).contains(needle, autoescape=True),
                func.lower(VerifiedStoreUser.user_identifier).contains(needle, autoescape=True),
                func.lower(VerifiedStoreUser.assignment_identifier).contains(
                    needle, autoescape=True
                ),
            )
        )
    joined = select(VerifiedStoreUser, UserRoleAssignment).outerjoin(
        UserRoleAssignment,
        UserRoleAssignment.assignment_identifier == VerifiedStoreUser.assignment_identifier,
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(VerifiedStoreUser)
            .outerjoin(
                UserRoleAssignment,
                UserRoleAssignment.assignment_identifier == VerifiedStoreUser.assignment_identifier,
            )
            .where(*filters)
        )
    ).scalar_one()
    ordered = joined.where(*filters).order_by(
        func.lower(VerifiedStoreUser.display_name), VerifiedStoreUser.assignment_identifier
    )
    if format == "csv":
        row_limit = 50_000

        async def generate():
            yield csv_row(
                (
                    "assignment_identifier",
                    "user_identifier",
                    "display_name",
                    "signed_in_role",
                    "assigned_role",
                    "assigned_by",
                    "assigned_at",
                    "reason",
                    "last_seen_at",
                )
            )
            result = await db.stream(ordered.limit(row_limit).execution_options(yield_per=1000))
            try:
                async for person, assignment in result:
                    item = role_assignment_view(person, assignment)
                    yield csv_row(
                        (
                            item["assignment_identifier"],
                            item["user_identifier"],
                            item["display_name"],
                            item["signed_in_role"],
                            item["assigned_role"],
                            item["assigned_by"],
                            item["assigned_at"].isoformat() if item["assigned_at"] else None,
                            item["reason"],
                            item["last_seen_at"].isoformat() if item["last_seen_at"] else None,
                        )
                    )
            finally:
                await result.close()

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="business-role-assignments.csv"',
                "X-Role-Assignments-Truncated": str(total > row_limit).lower(),
            },
        )
    rows = (await db.execute(ordered.offset((page - 1) * page_size).limit(page_size))).all()
    return {
        "roles": [
            {"key": role, "label": access.ROLE_LABELS.get(role, role)} for role in ASSIGNABLE_ROLES
        ],
        "items": [role_assignment_view(person, assignment) for person, assignment in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, -(-total // page_size)),
        },
    }


@router.post("/api/access/role-assignments/import/preview")
async def preview_role_assignment_import(
    body: RoleAssignmentImportBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("admin")),
):
    rows, errors = parse_role_assignment_csv(body.csv_text)
    if not errors:
        preview, counts, identity_errors, token, _, _ = await preview_role_assignment_rows(db, rows)
        errors.extend(identity_errors)
    else:
        preview, counts, token = [], {"Create": 0, "Replace": 0, "Unchanged": 0}, None
    return {
        "valid": not errors and bool(rows),
        "preview_token": token if not errors else None,
        "rows": preview,
        "counts": counts,
        "errors": errors[:100],
        "errors_truncated": len(errors) > 100,
    }


@router.post("/api/access/role-assignments/import")
async def apply_role_assignment_import(
    body: RoleAssignmentImportBody,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    if not body.preview_token:
        raise HTTPException(status_code=422, detail="Preview this CSV before applying it")
    rows, errors = parse_role_assignment_csv(body.csv_text)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors[:100]})
    preview, counts, identity_errors, token, existing, people = await preview_role_assignment_rows(
        db, rows, lock=True
    )
    if identity_errors:
        raise HTTPException(status_code=422, detail={"errors": identity_errors[:100]})
    if token != body.preview_token:
        raise HTTPException(
            status_code=409,
            detail="Role assignments changed after preview. Preview the file again before applying it.",
        )
    actor = user.get("user") or "ontology.user"
    now = utcnow()
    applied = {"created": 0, "replaced": 0, "unchanged": counts["Unchanged"]}
    for item in preview:
        if item["action"] == "Unchanged":
            continue
        identifier = item["assignment_identifier"]
        person = people[identifier]
        row = existing.get(identifier)
        before = {"role": row.role, "reason": row.reason} if row else None
        if row is None:
            row = UserRoleAssignment(
                assignment_identifier=identifier,
                role=item["role"],
                reason=item["reason"],
                assigned_by=actor,
                assigned_at=now,
            )
            db.add(row)
            applied["created"] += 1
        else:
            row.role = item["role"]
            row.reason = item["reason"]
            row.assigned_by = actor
            row.assigned_at = now
            applied["replaced"] += 1
        db.add(
            AuditEvent(
                id=str(uuid.uuid4()),
                event_type="ROLE_ASSIGNMENT_SET",
                actor=actor,
                object_type="UserRoleAssignment",
                object_id=identifier,
                action_description=(
                    f"{person.display_name} assigned the "
                    f"{access.ROLE_LABELS.get(item['role'], item['role'])} role by CSV import: "
                    f"{item['reason']}"
                ),
                before_state=before,
                after_state={
                    "role": item["role"],
                    "reason": item["reason"],
                    "import_source": "CSV",
                },
            )
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A role assignment changed while the import was being applied. Preview the file again.",
        ) from exc
    return {"applied": applied}


@router.put("/api/access/role-assignments")
async def set_role_assignment(
    body: RoleAssignmentInput,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    """Give a verified person a business role. Administrators are never assigned; Studio decides those."""
    person = await db.get(VerifiedStoreUser, body.assignment_identifier)
    if person is None:
        raise HTTPException(
            status_code=422,
            detail="This identity has not signed in through Ontology Studio yet. Ask them to sign in first.",
        )
    if person.role == "admin":
        raise HTTPException(
            status_code=422,
            detail="Studio administrators keep their Studio role and cannot receive a Nimbus business role.",
        )
    actor = user.get("user") or "ontology.user"
    row = (
        await db.execute(
            select(UserRoleAssignment)
            .where(UserRoleAssignment.assignment_identifier == body.assignment_identifier)
            .with_for_update()
        )
    ).scalar_one_or_none()
    before = {"role": row.role} if row else None
    if row is None:
        row = UserRoleAssignment(
            assignment_identifier=body.assignment_identifier,
            role=body.role,
            reason=body.reason,
            assigned_by=actor,
        )
        db.add(row)
    else:
        row.role, row.reason, row.assigned_by, row.assigned_at = (
            body.role,
            body.reason,
            actor,
            utcnow(),
        )
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="ROLE_ASSIGNMENT_SET",
            actor=actor,
            object_type="UserRoleAssignment",
            object_id=body.assignment_identifier,
            action_description=(
                f"{person.display_name} assigned the {access.ROLE_LABELS.get(body.role, body.role)} "
                f"role: {body.reason}"
            ),
            before_state=before,
            after_state={"role": body.role, "reason": body.reason},
        )
    )
    await db.commit()
    await db.refresh(row)
    return role_assignment_view(person, row)


@router.delete("/api/access/role-assignments/{assignment_identifier}")
async def remove_role_assignment(
    assignment_identifier: str,
    reason: str = Query(min_length=10, max_length=2000),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("admin")),
):
    """Take a person's assigned role away, so their Ontology Studio role applies again."""
    key = assignment_identifier.strip().casefold()
    row = (
        await db.execute(
            select(UserRoleAssignment)
            .where(UserRoleAssignment.assignment_identifier == key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No role is assigned to this identity")
    actor = user.get("user") or "ontology.user"
    db.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type="ROLE_ASSIGNMENT_REMOVED",
            actor=actor,
            object_type="UserRoleAssignment",
            object_id=key,
            action_description=f"Assigned {row.role} role removed: {reason.strip()}",
            before_state={"role": row.role, "reason": row.reason},
            after_state={"reason": reason.strip()},
        )
    )
    await db.delete(row)
    await db.commit()
    return {"assignment_identifier": key, "removed": True}


@router.get("/api/stores")
async def store_choices(db: AsyncSession = Depends(get_db), user: dict = Depends(current_user)):
    """Stores to offer in any store field. A store manager gets only their own; everyone else gets every known store."""
    return await known_store_codes(db, user)


async def _lookup(
    db: AsyncSession, user: dict, category: str, txn_column, control_column, store_id: str | None
) -> list[dict]:
    """Registers or cashiers to offer in a search box: the governed list plus whatever has actually been seen."""
    options: dict[str, str | None] = {}
    if not scope.scoped(user):  # a store manager only sees what happened in their own stores
        for ref in (
            await db.execute(
                select(FoundationReference).where(
                    FoundationReference.category == category, FoundationReference.active.is_(True)
                )
            )
        ).scalars():
            options[ref.code] = ref.name
    for model, column in ((CanonicalTransaction, txn_column), (CashControl, control_column)):
        query = scope.restrict(
            select(column).where(column.is_not(None)).distinct(), user, model.store_id
        )
        if store_id:
            query = query.where(model.store_id == store_id)
        for code in (await db.execute(query)).scalars():
            options.setdefault(code, None)
    return [
        {"code": code, "name": name if name != code else None}
        for code, name in sorted(options.items())
    ]


@router.get("/api/lookups/registers")
async def register_choices(
    store_id: str | None = Query(default=None, max_length=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    return await _lookup(
        db, user, "Register", CanonicalTransaction.register_id, CashControl.register_id, store_id
    )


@router.get("/api/lookups/cashiers")
async def cashier_choices(
    store_id: str | None = Query(default=None, max_length=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
):
    return await _lookup(
        db, user, "Cashier", CanonicalTransaction.cashier_id, CashControl.cashier_id, store_id
    )

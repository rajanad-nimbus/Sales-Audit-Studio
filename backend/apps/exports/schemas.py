"""Validated request contracts for downstream export operations."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DestIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=20)
    format: str = Field(default="json", min_length=1, max_length=10)
    url: str | None = Field(default=None, max_length=2048)
    secret: str | None = Field(default=None, max_length=4096)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized


class RunIn(BaseModel):
    destination_id: str = Field(min_length=1, max_length=36)
    dataset: str = Field(min_length=1, max_length=30)
    dry_run: bool = False
    scope: str = Field(default="all", min_length=1, max_length=20)


class FailedExportResolution(BaseModel):
    outcome: Literal["received", "not_received"]
    evidence: str = Field(min_length=1, max_length=2000)


class InterruptedExportRecovery(BaseModel):
    worker_stopped: bool
    evidence: str = Field(min_length=1, max_length=2000)


class AckIn(BaseModel):
    reference: str | None = Field(default=None, max_length=255)

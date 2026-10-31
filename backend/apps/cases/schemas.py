"""HTTP request and response contracts owned by case management."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CaseCreate(BaseModel):
    case_type: str = Field(min_length=1, max_length=50)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    store_id: str = Field(min_length=1, max_length=50)
    priority: str = Field(default="Normal", max_length=20)

    @field_validator("business_date")
    @classmethod
    def validate_business_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("business_date must be a valid YYYY-MM-DD date") from exc
        return value


class CaseUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = Field(default=None, max_length=255)
    priority: Optional[str] = Field(default=None, max_length=20)
    currency: Optional[str] = Field(default=None, max_length=3)


class CaseAmountReviewItem(BaseModel):
    exception_id: str = Field(min_length=1, max_length=36)
    amount: Decimal = Field(max_digits=15, decimal_places=2)
    exposure: Decimal = Field(max_digits=15, decimal_places=2)


class CaseAmountReview(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)
    exceptions: list[CaseAmountReviewItem] = Field(min_length=1, max_length=500)


class Case(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_number: str
    status: str
    case_type: str
    business_date: str
    store_id: str
    currency: Optional[str] = None
    amount_needs_review: bool = False
    total_exception_amount: Decimal
    total_exposure: Decimal
    investigation_status: str
    evidence_completeness: int
    assigned_to: Optional[str]
    priority: str
    sla_due_at: Optional[datetime] = None
    snoozed_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ExceptionCreate(BaseModel):
    case_id: str = Field(min_length=1, max_length=36)
    exception_type: str = Field(min_length=1, max_length=100)
    exception_family: str = Field(min_length=1, max_length=100)
    source_system: str = Field(min_length=1, max_length=50)
    exception_amount: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    estimated_exposure: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    severity: str = Field(default="Medium", min_length=1, max_length=20)
    confidence: str = Field(default="Medium", min_length=1, max_length=20)


class Exception(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    exception_type: str
    exception_family: str
    source_system: str
    exception_amount: Decimal
    estimated_exposure: Decimal
    severity: str
    confidence: str
    close_impact: str
    status: str
    created_at: datetime


class FindingCreate(BaseModel):
    case_id: str = Field(min_length=1, max_length=36)
    investigation_id: str = Field(min_length=1, max_length=36)
    conclusion: str = Field(min_length=1, max_length=10_000)
    finding_type: str = Field(min_length=1, max_length=50)
    confidence: str = Field(min_length=1, max_length=20)


class Finding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    investigation_id: str
    conclusion: str
    finding_type: str
    confidence: str
    created_at: datetime


class RecommendationCreate(BaseModel):
    case_id: str = Field(min_length=1, max_length=36)
    investigation_id: str = Field(min_length=1, max_length=36)
    disposition_type: str = Field(min_length=1, max_length=50)
    action_class: str = Field(min_length=1, max_length=50)
    expected_workflow: str = Field(min_length=1, max_length=2000)
    financial_impact: Decimal = Field(default=Decimal("0"), max_digits=15, decimal_places=2)
    confidence: str = Field(min_length=1, max_length=20)


class Recommendation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    investigation_id: str
    disposition_type: str
    action_class: str
    expected_workflow: str
    financial_impact: Decimal
    confidence: str
    status: str
    created_at: datetime


class HumanDecisionCreate(BaseModel):
    case_id: str = Field(min_length=1, max_length=36)
    recommendation_id: str = Field(min_length=1, max_length=36)
    decision_type: str = Field(min_length=1, max_length=50)
    decision: str = Field(min_length=1, max_length=50)
    actor: str = Field(min_length=1, max_length=255)
    authority_check: str = Field(default="Pending", max_length=50)
    rationale: Optional[str] = Field(default=None, max_length=2000)


class HumanDecision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    recommendation_id: str
    decision_type: str
    decision: str
    actor: str
    authority_check: str
    override_reason: Optional[str] = None
    created_at: datetime


class ValidationRunRequest(BaseModel):
    """Require a reviewer to cite fresh source records and explain why they verify the action."""
    source_record_ids: list[str] = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=10, max_length=2000)

    @field_validator("source_record_ids")
    @classmethod
    def validate_source_record_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 36 for item in value):
            raise ValueError("source record ids must contain 1 to 36 characters")
        if len(value) != len(set(value)):
            raise ValueError("source record ids must be unique")
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("validation rationale must contain at least 10 non-whitespace characters")
        return normalized


class CaseNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case note cannot be blank")
        return value


class EvidenceRequestCreate(BaseModel):
    requirement: str = Field(min_length=1, max_length=100)
    requested_from: str = Field(min_length=1, max_length=255)


class EvidenceProvide(BaseModel):
    requirement: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=10_000)  # what was checked or supplied, in the person's own words
    reference: str | None = Field(default=None, max_length=255)  # document, ticket or system reference


class EvidenceReject(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)


class EvidenceOverride(BaseModel):
    rationale: str = Field(min_length=1, max_length=2000)
    financial_impact: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=2)


class CaseDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case: Case
    exceptions: list[Exception]
    findings: list[Finding]
    recommendations: list[Recommendation]
    decisions: list[HumanDecision]
    truncated: dict[str, bool]
    next_cursor: dict[str, str | None]

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CaseCreate(BaseModel):
    case_type: str
    business_date: str
    store_id: str
    priority: str = "Normal"


class CaseUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: Optional[str] = None


class Case(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_number: str
    status: str
    case_type: str
    business_date: str
    store_id: str
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
    case_id: str
    exception_type: str
    exception_family: str
    source_system: str
    exception_amount: Decimal
    estimated_exposure: Decimal
    severity: str = "Medium"
    confidence: str = "Medium"


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
    case_id: str
    investigation_id: str
    conclusion: str
    finding_type: str
    confidence: str


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
    case_id: str
    investigation_id: str
    disposition_type: str
    action_class: str
    confidence: str


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
    case_id: str
    recommendation_id: str
    decision_type: str
    decision: str
    actor: str
    authority_check: str = "Pending"
    rationale: Optional[str] = None


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
    """Validation is based on newly received source records, never a client success flag."""
    source_record_ids: list[str] = []


class CaseNoteCreate(BaseModel):
    body: str


class EvidenceRequestCreate(BaseModel):
    requirement: str
    requested_from: str


class EvidenceProvide(BaseModel):
    requirement: str
    note: str                      # what was checked or supplied, in the person's own words
    reference: str | None = None   # document, ticket or system reference


class EvidenceReject(BaseModel):
    rationale: str

class EvidenceOverride(BaseModel):
    rationale: str
    financial_impact: Decimal = Decimal("0")

class CaseDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case: Case
    exceptions: list[Exception]
    findings: list[Finding]
    recommendations: list[Recommendation]
    decisions: list[HumanDecision]


class AuditEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    actor: str
    object_type: str
    object_id: str
    case_id: Optional[str]
    action_description: str
    created_at: datetime
    seq: Optional[int] = None
    hash: Optional[str] = None

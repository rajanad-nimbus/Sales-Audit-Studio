"""Development-only demo data endpoints."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_role
from database import get_db
from models import AuditEvent, Case, Exception as DBException, Finding, Recommendation

router = APIRouter(tags=["Development"])


@router.post("/api/seed")
async def seed_demo_data(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("it")),
):
    """Load synthetic demo cases only into an empty case database."""
    if (await db.execute(select(func.count(Case.id)))).scalar():
        raise HTTPException(status_code=409, detail="Data already present")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    demo_cases = (
        ("Timing Difference", "STORE-014", "Ready for Decision", "In Review", "High",
         "TIMING_DIFFERENCE", "Settlement", 550, 0, "Medium",
         "Refund of $550 occurred after 23:00 cutoff; confirmed by processor settlement batch.",
         "Timing", "Disposition Only"),
        ("Duplicate Sales", "STORE-022", "In Progress", "In Investigation", "Normal",
         "DUPLICATE_SALES", "Transaction Audit", 1280, 1280, "High", None, None, None),
        ("Missing Refund", "STORE-007", "Pending", "Open", "Critical",
         "MISSING_REFUND", "Payment Reconciliation", 3420, 3420, "Critical", None, None, None),
        ("Connector Failure", "STORE-031", "Needs Intervention", "Open", "High",
         "CONNECTOR_TIMEOUT", "Ingestion", 0, 0, "Medium", None, None, None),
    )
    for index, values in enumerate(demo_cases, start=1):
        (case_type, store, status, investigation_status, priority, exception_type, family,
         amount, exposure, severity, conclusion, disposition, action_class) = values
        case_id = str(uuid.uuid4())
        db.add(Case(
            id=case_id,
            case_number=f"ZA-{today.replace('-', '')}-{index:04d}",
            status=status,
            case_type=case_type,
            business_date=today,
            store_id=store,
            total_exception_amount=amount,
            total_exposure=exposure,
            investigation_status=investigation_status,
            evidence_completeness=100 if conclusion else 40,
            priority=priority,
        ))
        await db.flush()
        db.add(DBException(
            id=str(uuid.uuid4()), case_id=case_id, exception_type=exception_type,
            exception_family=family, source_system="POS", detection_origin="Audit Rule",
            detection_rule_id=f"RULE-{exception_type}", exception_amount=amount,
            estimated_exposure=exposure, severity=severity, confidence="High",
            close_impact="Yes" if amount else "No",
        ))
        if conclusion:
            investigation_id = f"INV-{index:04d}"
            db.add(Finding(
                id=str(uuid.uuid4()), case_id=case_id, investigation_id=investigation_id,
                conclusion=conclusion, finding_type="Supported Finding", confidence="High",
                supporting_rationale="Evidence complete and consistent across sources.",
            ))
            db.add(Recommendation(
                id=str(uuid.uuid4()), case_id=case_id, investigation_id=investigation_id,
                disposition_type=disposition, action_class=action_class,
                expected_workflow="Apply classification -> Schedule validation -> Monitor -> Close",
                financial_impact=0, confidence="High",
            ))
        db.add(AuditEvent(
            id=str(uuid.uuid4()), event_type="CASE_CREATED", actor="system", object_type="Case",
            object_id=case_id, case_id=case_id, action_description=f"Case created: {case_type}",
        ))

    await db.commit()
    return {"seeded": len(demo_cases)}

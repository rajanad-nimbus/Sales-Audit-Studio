"""Development-only demo data endpoints."""

import uuid
from datetime import datetime, timezone

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.audit.models import AuditEvent
from apps.cases.models import (
    Case,
    Finding,
    Recommendation,
)
from apps.cases.models import (
    Exception as DBException,
)
from apps.security.authentication import require_role

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


SCALE_DEMO_CASE_TYPES = (
    "Timing Difference", "Duplicate Sales", "Missing Refund", "Amount Mismatch",
    "Orphan Payment", "Unmatched Sale", "Bank Deposit Variance", "GL Posting Variance",
    "Incomplete Feed", "Balancing Variance",
)


@router.post("/api/action-center/scale-demo")
async def scale_demo(
    cases: int = Query(50_000, ge=1_000, le=500_000),
    stores: int = Query(300, ge=10, le=1_000),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("it")),
):
    """Create tagged synthetic cases for action-center volume checks."""
    case_types_sql = "ARRAY[" + ",".join(f"'{case_type}'" for case_type in SCALE_DEMO_CASE_TYPES) + "]"
    tag = uuid.uuid4().hex[:4].upper()
    await db.execute(text(f"""
        INSERT INTO cases (id, case_number, status, case_type, business_date, store_id, total_exception_amount,
            total_exposure, investigation_status, evidence_completeness, assigned_to, priority, sla_due_at, created_at, updated_at)
        SELECT gen_random_uuid()::text, 'ZS-DEMO-{tag}-' || lpad(i::text, 7, '0'),
            CASE WHEN random() < 0.35 THEN 'In Review' WHEN random() < 0.5 THEN 'Open' WHEN random() < 0.4 THEN 'In Investigation'
                 WHEN random() < 0.25 THEN 'Resolving' WHEN random() < 0.2 THEN 'Pending Validation' ELSE 'Closed' END,
            ({case_types_sql})[1 + floor(random() * {len(SCALE_DEMO_CASE_TYPES)})::int],
            to_char(current_date - floor(random() * 14)::int, 'YYYY-MM-DD'),
            'STORE-' || lpad((1 + floor(random() * :stores)::int)::text, 3, '0'),
            round((power(random(), 3) * 6000 + 1)::numeric, 2), round((power(random(), 3) * 5000)::numeric, 2),
            'Pending', 100, NULL, 'Normal', now() + ((random() * 90 - 20) || ' hours')::interval,
            now() - ((random() * 14 * 24) || ' hours')::interval, now()
        FROM generate_series(1, :n) AS i
    """), {"n": cases, "stores": stores})
    await db.execute(text(f"""
        UPDATE cases SET priority = CASE WHEN total_exposure > 3000 THEN 'Critical' WHEN total_exposure > 1000 THEN 'High' ELSE 'Normal' END,
            investigation_status = CASE WHEN status = 'In Review' THEN 'Ready for Decision' WHEN status = 'Closed' THEN 'Resolved' ELSE 'Pending' END,
            evidence_completeness = CASE WHEN status IN ('Open') THEN 0 ELSE 100 END
        WHERE case_number LIKE 'ZS-DEMO-{tag}-%'
    """))
    await db.commit()
    total = (await db.execute(select(func.count()).select_from(Case))).scalar()
    return {"created": cases, "tag": tag, "total_cases": total}


@router.delete("/api/action-center/scale-demo")
async def clear_scale_demo(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("it")),
):
    """Delete only cases created in the reserved synthetic-data namespace."""
    result = await db.execute(delete(Case).where(Case.case_number.like("ZS-DEMO-%")))
    await db.commit()
    return {"deleted": result.rowcount or 0}

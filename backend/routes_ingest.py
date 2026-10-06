import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import access
import chat
import ingest
import llm
import ontology
from nimbus import audit
from ontology_integration import integration as ontology_integration
from auth import current_user, require_role
from database import get_db
from models import CanonicalTransaction, ReconciliationMatch, ReconciliationPolicy, SourceFeedProfile, SourceRecord, utcnow

router = APIRouter()


class IngestRequest(BaseModel):
    source_system: str
    delivery_id: str
    records: list[dict]


class CsvIngestRequest(BaseModel):
    source_system: str
    delivery_id: str
    csv_data: str
    profile_id: str | None = None


class SourceFeedProfileRequest(BaseModel):
    name: str
    source_system: str
    schedule: str | None = None
    column_mapping: dict[str, str] = {}
    enabled: bool = True


class SourceFeedProfilePatch(BaseModel):
    schedule: str | None = None
    column_mapping: dict[str, str] | None = None
    enabled: bool | None = None


class ReconciliationPolicyRequest(BaseModel):
    amount_tolerance: float = 0
    settlement_day_tolerance: int = 0


class ManualMatchRequest(BaseModel):
    transaction_ids: list[str]
    match_type: str = "Manual Match"
    rationale: str


def profile_view(profile: SourceFeedProfile) -> dict:
    return {"id": profile.id, "name": profile.name, "source_system": profile.source_system,
            "schedule": profile.schedule, "column_mapping": profile.column_mapping,
            "enabled": profile.enabled, "updated_at": profile.updated_at}


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


@router.get("/api/ingest/profiles")
async def list_source_profiles(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    rows = (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name))).scalars().all()
    return [profile_view(row) for row in rows]


@router.get("/api/ingest/deliveries")
async def delivery_history(limit: int = 25, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    """Operational history grouped by immutable source delivery ID."""
    rows = (await db.execute(select(SourceRecord).order_by(SourceRecord.received_at.desc()).limit(5000))).scalars().all()
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.source_system, row.delivery_id)
        item = grouped.setdefault(key, {"source_system": row.source_system, "delivery_id": row.delivery_id,
                                        "received_at": row.received_at, "records": 0, "processed": 0,
                                        "quarantined": 0, "duplicates": 0, "last_error": None})
        item["records"] += 1
        item["received_at"] = max(item["received_at"], row.received_at)
        if row.status == "Processed": item["processed"] += 1
        elif row.status == "Quarantined":
            item["quarantined"] += 1
            item["last_error"] = item["last_error"] or row.quarantine_reason
    return sorted(grouped.values(), key=lambda item: item["received_at"], reverse=True)[:min(limit, 100)]


@router.get("/api/ingest/feed-health")
async def feed_health(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it", "finance"))):
    """Freshness for enabled source profiles, based solely on received immutable deliveries."""
    profiles = (await db.execute(select(SourceFeedProfile).order_by(SourceFeedProfile.name))).scalars().all()
    result = []
    now = datetime.now(timezone.utc)
    for profile in profiles:
        newest = (await db.execute(select(func.max(SourceRecord.received_at)).where(
            SourceRecord.source_system == profile.source_system, SourceRecord.status.in_(("Archived", "Processed"))
        ))).scalar()
        window = schedule_window_hours(profile.schedule)
        age = round((now - newest).total_seconds() / 3600, 1) if newest else None
        state = "disabled" if not profile.enabled else "never" if not newest else "unknown" if not window else "overdue" if age > window else "fresh"
        result.append({"profile_id": profile.id, "name": profile.name, "source_system": profile.source_system,
                       "schedule": profile.schedule, "last_received_at": newest, "age_hours": age,
                       "expected_within_hours": window, "state": state})
    return result


@router.get("/api/reconciliation/policy")
async def get_reconciliation_policy(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active == True))).scalars().first()  # noqa: E712
    return {"source": "local-fallback", "amount_tolerance": policy.amount_tolerance if policy else 0,
            "settlement_day_tolerance": policy.settlement_day_tolerance if policy else 0,
            "updated_by": policy.updated_by if policy else None, "updated_at": policy.updated_at if policy else None}


@router.put("/api/reconciliation/policy")
async def update_reconciliation_policy(body: ReconciliationPolicyRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    if body.amount_tolerance < 0 or body.settlement_day_tolerance < 0 or body.settlement_day_tolerance > 31:
        raise HTTPException(status_code=422, detail="Tolerance values must be non-negative; settlement days may not exceed 31")
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active == True))).scalars().first()  # noqa: E712
    if not policy:
        policy = ReconciliationPolicy(id=str(uuid.uuid4()), name="Nimbus fallback reconciliation policy")
        db.add(policy)
    policy.amount_tolerance, policy.settlement_day_tolerance = body.amount_tolerance, body.settlement_day_tolerance
    policy.updated_by, policy.updated_at = user["user"] or "ontology.user", utcnow()
    await db.commit()
    return {"source": "local-fallback", "amount_tolerance": policy.amount_tolerance, "settlement_day_tolerance": policy.settlement_day_tolerance, "updated_by": policy.updated_by, "updated_at": policy.updated_at}


@router.get("/api/reconciliation/matches")
async def list_manual_matches(status: str | None = None, db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("finance", "it"))):
    query = select(ReconciliationMatch).order_by(ReconciliationMatch.created_at.desc()).limit(100)
    if status: query = query.where(ReconciliationMatch.status == status)
    rows = (await db.execute(query)).scalars().all()
    return [{"id": row.id, "transaction_ids": row.transaction_ids, "match_type": row.match_type, "status": row.status, "rationale": row.rationale, "proposed_by": row.proposed_by, "reviewed_by": row.reviewed_by, "created_at": row.created_at} for row in rows]


@router.post("/api/reconciliation/matches")
async def propose_manual_match(body: ManualMatchRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance", "it"))):
    ids = list(dict.fromkeys(body.transaction_ids))
    if len(ids) < 2 or not body.rationale.strip(): raise HTTPException(status_code=422, detail="At least two transactions and a rationale are required")
    rows = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id.in_(ids)))).scalars().all()
    if len(rows) != len(ids): raise HTTPException(status_code=404, detail="One or more transactions were not found")
    if len({row.store_id for row in rows}) != 1 or len({row.currency for row in rows}) != 1:
        raise HTTPException(status_code=422, detail="Manual matches must use one store and one currency")
    match = ReconciliationMatch(id=str(uuid.uuid4()), transaction_ids=ids, match_type=body.match_type.strip() or "Manual Match", rationale=body.rationale.strip(), proposed_by=user["user"] or "ontology.user")
    db.add(match); audit(db, "MANUAL_MATCH_PROPOSED", None, "ReconciliationMatch", match.id, "Manual reconciliation match proposed", actor=match.proposed_by); await db.commit()
    return {"id": match.id, "status": match.status}


@router.post("/api/reconciliation/matches/{match_id}/approve")
async def approve_manual_match(match_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    match = (await db.execute(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id))).scalar_one_or_none()
    if not match or match.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed manual match not found")
    rows = (await db.execute(select(CanonicalTransaction).where(CanonicalTransaction.id.in_(match.transaction_ids)))).scalars().all()
    if len(rows) != len(match.transaction_ids): raise HTTPException(status_code=409, detail="Match source records are no longer available")
    for row in rows: row.reconciliation_status, row.disposition = "Manually Matched", "Closed"
    match.status, match.reviewed_by, match.reviewed_at = "Approved", user["user"] or "ontology.user", utcnow()
    audit(db, "MANUAL_MATCH_APPROVED", None, "ReconciliationMatch", match.id, "Manual reconciliation match approved", actor=match.reviewed_by); await db.commit(); return {"id": match.id, "status": match.status}


@router.post("/api/reconciliation/matches/{match_id}/reject")
async def reject_manual_match(match_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("finance"))):
    match = (await db.execute(select(ReconciliationMatch).where(ReconciliationMatch.id == match_id))).scalar_one_or_none()
    if not match or match.status != "Proposed": raise HTTPException(status_code=409, detail="Proposed manual match not found")
    match.status, match.reviewed_by, match.reviewed_at = "Rejected", user["user"] or "ontology.user", utcnow()
    audit(db, "MANUAL_MATCH_REJECTED", None, "ReconciliationMatch", match.id, "Manual reconciliation match rejected", actor=match.reviewed_by); await db.commit(); return {"id": match.id, "status": match.status}


@router.post("/api/ingest/profiles")
async def create_source_profile(body: SourceFeedProfileRequest, db: AsyncSession = Depends(get_db),
                                _: dict = Depends(require_role("it"))):
    if body.source_system not in ingest.VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"source_system must be one of {sorted(ingest.VALID_TYPES)}")
    profile = SourceFeedProfile(id=str(uuid.uuid4()), name=body.name.strip(), source_system=body.source_system,
                                schedule=body.schedule, column_mapping=body.column_mapping, enabled=body.enabled)
    db.add(profile)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A source-feed profile with this name already exists")
    await db.refresh(profile)
    return profile_view(profile)


@router.patch("/api/ingest/profiles/{profile_id}")
async def update_source_profile(profile_id: str, body: SourceFeedProfilePatch, db: AsyncSession = Depends(get_db),
                                _: dict = Depends(require_role("it"))):
    profile = (await db.execute(select(SourceFeedProfile).where(SourceFeedProfile.id == profile_id))).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Source-feed profile not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile_view(profile)


@router.get("/api/ingest/contracts")
async def ingest_contracts(_: dict = Depends(require_role("it", "finance"))):
    """Published input contracts for initial read-only source deliveries."""
    return {
        "transaction_columns": ["source_record_id", "event_time", "record_type", "amount", "store_id",
                                "currency", "payment_reference", "register_id", "tender_type", "settled_on"],
        "pos_control_columns": ["source_record_id", "business_date", "store_id", "declared_count", "declared_total"],
        "sources": {source: sorted(types) for source, types in ingest.VALID_TYPES.items()},
        "note": "Use ISO-8601 timestamps, positive source amounts, canonical store IDs, and a unique delivery_id."}


@router.post("/api/ingest")
async def ingest_records(req: IngestRequest, db: AsyncSession = Depends(get_db),
                         _: dict = Depends(require_role("it"))):
    if req.source_system not in ingest.VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"source_system must be one of {sorted(ingest.VALID_TYPES)}")
    stats = await ingest.ingest(db, req.source_system, req.delivery_id, req.records)
    await db.commit()
    return stats


@router.post("/api/ingest/csv")
async def ingest_csv(req: CsvIngestRequest, db: AsyncSession = Depends(get_db),
                     _: dict = Depends(require_role("it"))):
    """Ingest a read-only source extract using the published CSV contract."""
    if req.source_system not in ingest.VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"source_system must be one of {sorted(ingest.VALID_TYPES)}")
    mapping = {}
    if req.profile_id:
        profile = (await db.execute(select(SourceFeedProfile).where(SourceFeedProfile.id == req.profile_id))).scalar_one_or_none()
        if not profile or not profile.enabled:
            raise HTTPException(status_code=404, detail="Enabled source-feed profile not found")
        if profile.source_system != req.source_system:
            raise HTTPException(status_code=422, detail="Profile source_system does not match delivery source_system")
        mapping = profile.column_mapping or {}
    try:
        rows = list(csv.DictReader(io.StringIO(req.csv_data)))
    except csv.Error as exc:
        raise HTTPException(status_code=422, detail=f"Invalid CSV: {exc}")
    if not rows:
        raise HTTPException(status_code=422, detail="CSV contains no data rows")
    if len(rows) > 50000:
        raise HTTPException(status_code=422, detail="CSV exceeds the 50,000-record delivery limit")
    if mapping:
        rows = [{canonical: row.get(source_column) for canonical, source_column in mapping.items()} for row in rows]
    stats = await ingest.ingest(db, req.source_system, req.delivery_id, rows)
    await db.commit()
    return {**stats, "delivery_id": req.delivery_id, "source_system": req.source_system}


@router.post("/api/ingest/simulate")
async def simulate_ingest(db: AsyncSession = Depends(get_db), _: dict = Depends(require_role("it"))):
    earliest = (await db.execute(select(func.min(CanonicalTransaction.business_date)))).scalar()
    day = (datetime.fromisoformat(earliest) - timedelta(days=1)).strftime("%Y-%m-%d") if earliest else None
    feeds = ingest.simulate_feed(day)
    delivery = f"SIM-{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
    result = {src: await ingest.ingest(db, src, delivery, recs) for src, recs in feeds.items()}
    await db.commit()
    return result


@router.post("/api/reconcile")
async def run_reconciliation(business_date: str | None = None, db: AsyncSession = Depends(get_db),
                             _: dict = Depends(require_role("it", "finance"))):
    result = await ingest.reconcile(db, business_date)
    await db.commit()
    return result


@router.get("/api/ingest/stats")
async def ingest_stats(db: AsyncSession = Depends(get_db)):
    src = (await db.execute(select(SourceRecord.status, func.count()).group_by(SourceRecord.status))).all()
    txn = (await db.execute(select(CanonicalTransaction.reconciliation_status, func.count())
                            .group_by(CanonicalTransaction.reconciliation_status))).all()
    bad = (await db.execute(select(SourceRecord).where(SourceRecord.status == "Quarantined")
                            .order_by(SourceRecord.received_at.desc()).limit(10))).scalars().all()
    return {
        "source_records": {k: v for k, v in src},
        "transactions": {k: v for k, v in txn},
        "quarantined": [{"id": b.id, "source_system": b.source_system, "source_record_id": b.source_record_id,
                         "reason": b.quarantine_reason, "received_at": b.received_at} for b in bad],
    }


@router.get("/api/ontology")
async def ontology_index():
    return {"version": ontology.ONTOLOGY_VERSION,
            "exception_types": {k: {"label": v["label"], "family": v["family"]} for k, v in ontology.EXCEPTION_TYPES.items()}}


@router.get("/api/ontology/integration/status")
async def ontology_integration_status(_: dict = Depends(require_role("it", "finance", "admin"))):
    """Show whether Nimbus is reading an approved ontology release; never exposes its key."""
    return ontology_integration.status()


@router.post("/api/ontology/integration/refresh")
async def refresh_ontology_integration(_: dict = Depends(require_role("it", "admin"))):
    """Refresh a complete release-pinned ontology context using the configured agent key."""
    await ontology_integration.refresh()
    return ontology_integration.status()


@router.get("/api/ontology/integration/context")
async def ontology_integration_context(_: dict = Depends(require_role("it", "finance", "admin"))):
    """Definitions visible to the configured Ontology Studio persona; contains no warehouse rows."""
    return ontology_integration.context()


@router.get("/api/ontology/exception-types/{exception_type}")
async def ontology_exception_type(exception_type: str):
    od = ontology.get(exception_type)
    if not od:
        raise HTTPException(status_code=404, detail="Unknown exception type")
    return {"version": ontology.ONTOLOGY_VERSION, "type": exception_type, **od}


@router.get("/api/me")
async def me(user: dict = Depends(current_user)):
    """Who you are and which screens your role may open. The UI builds its navigation and route guard from this."""
    return {**user, "role_label": access.ROLE_LABELS.get(user["role"], user["role"]), "screens": access.screens_for(user["role"])}


@router.get("/api/access/matrix")
async def access_matrix(user: dict = Depends(current_user)):
    return {**access.matrix(), "you": user["role"]}


class ChatRequest(BaseModel):
    message: str
    case_id: str | None = None
    persona: str = ""
    history: list[dict] = []


@router.post("/api/chat")
async def chat_endpoint(req: ChatRequest, db: AsyncSession = Depends(get_db), _: dict = Depends(current_user)):
    msg = req.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Empty message")
    ctx = await chat.build_context(db, req.case_id, msg)
    base = chat.rule_answer(msg, ctx, req.persona)
    text = await llm.chat(ctx, req.history, msg, req.persona)
    if text:
        return {**base, "answer": text, "source": "llm"}
    return base

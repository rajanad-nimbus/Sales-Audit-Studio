"""Requeue linked transaction exceptions after an approved amount correction."""
from datetime import date
from decimal import Decimal

from integrations.ontology.nimbus import audit
from sqlalchemy import or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.lineage import (
    audit_json_text_value,
    audit_json_value_is_string,
    case_creation_mentions_transaction,
    case_transaction_ids_table,
)
from apps.cases.models import (
    Case,
    Recommendation,
)
from apps.corrections.effects import effective_amounts
from apps.ingest.models import ReconciliationPolicy
from apps.transactions.detail import card_amount
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionTender,
)


async def linked_cases(db: AsyncSession, transaction: CanonicalTransaction) -> list[Case]:
    """Lock linked cases from parent to child, including follow-ups created while waiting.

    Follow-up creation locks its parent before its child. Matching that order avoids
    a deadlock when an approval and follow-up creation run concurrently.
    """
    locked: dict[str, Case] = {}
    while True:
        created_event = aliased(AuditEvent)
        parent_id = audit_json_text_value(db, created_event, "parent_case_id")
        rows = (await db.execute(select(created_event.case_id, parent_id).join(
            Case, Case.id == created_event.case_id
        ).where(
            Case.store_id == transaction.store_id, created_event.event_type == "CASE_CREATED",
            or_(parent_id.is_(None), audit_json_value_is_string(db, created_event, "parent_case_id")),
            case_creation_mentions_transaction(db, transaction.id, created_event),
        ))).all()
        parents = {case_id: parent_id for case_id, parent_id in rows if case_id}
        remaining = set(parents) - locked.keys()
        if not remaining:
            return list(locked.values())
        # A case is ready when its parent is already locked or is outside this set.
        ready = sorted(case_id for case_id in remaining if parents[case_id] not in remaining)
        if not ready:
            raise ValueError("Cycle in follow-up case lineage")
        ready_cases = (await db.execute(
            select(Case)
            .where(Case.id.in_(ready))
            .order_by(Case.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )).scalars().all()
        locked.update((case.id, case) for case in ready_cases)


async def invalidate_case_evidence(db: AsyncSession, cases: list[Case], adjustment_id: str, actor: str) -> None:
    case_ids = list(dict.fromkeys(case.id for case in cases if case.status != "Closed"))
    for offset in range(0, len(case_ids), 500):
        await db.execute(
            update(Recommendation)
            .where(
                Recommendation.case_id.in_(case_ids[offset:offset + 500]),
                Recommendation.status == "Proposed",
            )
            .values(status="Superseded")
        )

    for case in cases:
        case.amount_needs_review = True
        if case.status != "Closed":
            if case.status in {"In Review", "In Investigation"}:
                case.status = "Open"
            case.investigation_status = "Pending"
            case.evidence_completeness = 0
        audit(db, "CASE_EVIDENCE_STALE", case.id, "TransactionAdjustment", adjustment_id,
              "Approved correction changed transaction evidence; review the recorded case amount and exposure",
              actor=actor, after={"adjustment_id": adjustment_id, "case_closed": case.status == "Closed"})


async def related_exception_ids(db: AsyncSession, transaction: CanonicalTransaction) -> set[str]:
    """Use recorded case evidence and the payment key; never infer links from a reference alone."""
    pairs = {"Sale": "Capture", "Capture": "Sale", "Return": "Refund", "Refund": "Return"}
    counterpart = pairs.get(transaction.transaction_type)
    if transaction.reconciliation_status != "Exception" or not transaction.payment_reference or not counterpart:
        return {transaction.id}
    if transaction.source_lineage not in {"pos-map-v1", "processor-map-v1"}:
        return {transaction.id}
    created_event = aliased(AuditEvent)
    transaction_ids = case_transaction_ids_table(db, created_event)
    linked_counterparts = (
        select(CanonicalTransaction.id)
        .select_from(created_event)
        .join(transaction_ids, true())
        .join(Case, Case.id == created_event.case_id)
        .join(CanonicalTransaction, CanonicalTransaction.id == transaction_ids.c.value)
        .where(
            Case.store_id == transaction.store_id,
            created_event.event_type == "CASE_CREATED",
            case_creation_mentions_transaction(db, transaction.id, created_event),
            CanonicalTransaction.store_id == transaction.store_id,
            CanonicalTransaction.currency == transaction.currency,
            CanonicalTransaction.payment_reference == transaction.payment_reference,
            CanonicalTransaction.transaction_type == counterpart,
            CanonicalTransaction.source_lineage == (
                "processor-map-v1" if transaction.source_lineage == "pos-map-v1" else "pos-map-v1"
            ),
        )
        .distinct()
        .limit(2)
    )
    rows = (await db.execute(linked_counterparts)).scalars().all()
    return {transaction.id, *rows}


async def requeue_linked_exceptions(db: AsyncSession, target: CanonicalTransaction,
                                    locked: list[CanonicalTransaction], actor: str) -> None:
    """Requeue only the target and its documented, still-exceptional counterpart."""
    if target.reconciliation_status != "Exception" or len(locked) != 2:
        return
    candidates = [row for row in locked if row.reconciliation_status == "Exception"]
    if target not in candidates or len(candidates) != 2:
        return
    pos = next((row for row in candidates if row.source_lineage == "pos-map-v1"), None)
    processor = next((row for row in candidates if row.source_lineage == "processor-map-v1"), None)
    if not pos or not processor:
        return
    amounts = await effective_amounts(db, candidates)
    tenders = (await db.execute(select(TransactionTender).where(TransactionTender.transaction_id == pos.id))).scalars().all()
    # A header correction does not say which component of a split tender changed.
    if tenders and amounts[pos.id] != pos.signed_amount:
        return
    policy = (await db.execute(select(ReconciliationPolicy).where(ReconciliationPolicy.active.is_(True)))).scalars().first()
    tolerance = policy.amount_tolerance if policy and pos.currency == policy.amount_tolerance_currency else Decimal("0")
    days = policy.settlement_day_tolerance if policy else 0
    expected = card_amount([{"tender_type": item.tender_type, "amount": item.amount} for item in tenders], amounts[pos.id])
    if abs(amounts[processor.id] - expected) > tolerance:
        return
    if (date.fromisoformat(processor.settlement_date) - date.fromisoformat(pos.business_date)).days > days:
        return
    for row in candidates:
        row.reconciliation_status, row.disposition = "Unmatched", "Open"
    audit(db, "TRANSACTIONS_REQUEUED", None, "CanonicalTransaction", target.id,
          "Approved amount correction queued linked transactions for reconciliation", actor=actor,
          after={"transaction_ids": [row.id for row in candidates]})

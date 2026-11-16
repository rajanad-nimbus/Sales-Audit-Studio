"""Reusable SQL predicates for export eligibility evidence."""

from sqlalchemy import and_, cast, func, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import aliased

from apps.audit.models import AuditEvent
from apps.cases.lineage import case_creation_mentions_transaction
from apps.cases.models import Case, HumanDecision, Recommendation, ValidationObligation, Workflow
from apps.cases.models import Exception as ExceptionRow
from apps.totals.models import SalesAuditErrorDefinition


def completed_case_evidence():
    """Return a recommendation's matching approval, completed workflow, and validation."""
    return (
        select(Recommendation, HumanDecision, ValidationObligation)
        .join(
            HumanDecision,
            (HumanDecision.recommendation_id == Recommendation.id)
            & (HumanDecision.case_id == Recommendation.case_id),
        )
        .join(
            Workflow,
            (Workflow.recommendation_id == Recommendation.id)
            & (Workflow.case_id == Recommendation.case_id),
        )
        .join(
            ValidationObligation,
            (ValidationObligation.workflow_id == Workflow.id)
            & (ValidationObligation.case_id == Recommendation.case_id),
        )
        .where(
            Recommendation.status == "Approved",
            HumanDecision.decision == "Approved",
            Workflow.state == "Completed",
            ValidationObligation.status == "Complete",
        )
    )


def verified_adjustment_evidence():
    """Require the matched completed workflow to have verified financial impact."""
    return completed_case_evidence().where(
        Recommendation.financial_impact > 0,
        ValidationObligation.verification_result == "Verified",
    )


def transaction_export_invalid(db, destination_name: str, transaction):
    """Apply unresolved audit errors and their destination-specific impact to one transaction."""
    created_event = aliased(AuditEvent)
    linked = and_(
        created_event.event_type == "CASE_CREATED",
        case_creation_mentions_transaction(db, transaction.id, created_event),
    )
    linked_errors = (
        select(ExceptionRow.id)
        .join(Case, Case.id == ExceptionRow.case_id)
        .join(created_event, created_event.case_id == Case.id)
        .where(linked)
    )
    has_linked_error = linked_errors.exists()
    active = and_(
        Case.status != "Closed",
        ExceptionRow.status.not_in(("Overridden", "Closed", "Resolved")),
    )
    active_errors = linked_errors.where(active)
    blocking_error = _blocking_error_exists(db, active_errors, destination_name)
    not_reconciled = transaction.reconciliation_status.not_in(
        ("Matched", "Manually Matched", "Exception")
    )
    return or_(
        not_reconciled,
        transaction.detail_status == "Unbalanced",
        and_(
            transaction.reconciliation_status == "Exception",
            or_(not_(has_linked_error), blocking_error),
        ),
    )


def store_day_export_invalid(db, destination_name: str, store_id, business_date):
    """Hold totals for a store day when an active error affects this destination."""
    active = and_(
        Case.status != "Closed",
        ExceptionRow.status.not_in(("Overridden", "Closed", "Resolved")),
    )
    base = (
        select(ExceptionRow.id)
        .join(Case, Case.id == ExceptionRow.case_id)
        .where(Case.store_id == store_id, Case.business_date == business_date, active)
    )
    return _blocking_error_exists(db, base, destination_name)


def _blocking_error_exists(db, base, destination_name: str):
    if db.get_bind().dialect.name == "postgresql":
        block_destination = cast(
            SalesAuditErrorDefinition.export_block_destinations, JSONB
        ).contains([destination_name])
    else:
        configured_destinations = func.json_each(
            SalesAuditErrorDefinition.export_block_destinations
        ).table_valued("key", "value")
        block_destination = (
            select(1)
            .select_from(configured_destinations)
            .where(configured_destinations.c.value == destination_name)
            .exists()
        )
    return (
        base.outerjoin(
            SalesAuditErrorDefinition,
            SalesAuditErrorDefinition.code == ExceptionRow.exception_type,
        )
        .where(
            or_(
                SalesAuditErrorDefinition.code.is_(None),
                SalesAuditErrorDefinition.export_impact_configured.is_(False),
                and_(
                    SalesAuditErrorDefinition.export_impact_configured.is_(True),
                    block_destination,
                ),
            )
        )
        .exists()
    )

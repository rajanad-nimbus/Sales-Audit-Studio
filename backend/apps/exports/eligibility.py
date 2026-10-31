"""Reusable SQL predicates for export eligibility evidence."""

from sqlalchemy import select

from apps.cases.models import HumanDecision, Recommendation, ValidationObligation, Workflow


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

"""Variance tolerances: whether a total's variance is acceptable, needs a reason, or needs a case.

Outcomes are worked out when they are read, from the tolerance rules and the explanations
on file, so changing a rule changes every open store day's outcome at once.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.store_days.models import AuditTotal, StoreDay, VarianceExplanation, VarianceTolerance
from apps.totals.calculation import store_location_traits

BALANCED = "Balanced"
WITHIN = "Within tolerance"
NEEDS_REASON = "Needs reason"
NEEDS_CASE = "Needs case"


@dataclass(frozen=True, slots=True)
class Tolerance:
    within_amount: Decimal
    case_amount: Decimal
    rule_id: str | None
    total_name: str | None
    location_trait: str | None


# With no rule, every cent of variance needs a case: the behaviour before tolerances existed.
NO_TOLERANCE = Tolerance(Decimal("0"), Decimal("0"), None, None, None)


def tolerance_for(rules: list[VarianceTolerance], total_name: str, traits: set[str]) -> Tolerance:
    """The most specific active rule for a total at a store; the tighter one breaks a tie."""
    matches = [
        rule
        for rule in rules
        if rule.active
        and rule.total_name in (None, total_name)
        and (rule.location_trait is None or rule.location_trait in traits)
    ]
    if not matches:
        return NO_TOLERANCE
    best = min(
        matches,
        key=lambda rule: (
            rule.total_name is None,
            rule.location_trait is None,
            rule.within_amount,
            rule.case_amount,
        ),
    )
    return Tolerance(
        best.within_amount, best.case_amount, best.id, best.total_name, best.location_trait
    )


def outcome(variance: Decimal | None, tolerance: Tolerance) -> str | None:
    if variance is None:
        return None
    size = abs(variance)
    if size == 0:
        return BALANCED
    if size <= tolerance.within_amount:
        return WITHIN
    if size > tolerance.case_amount:
        return NEEDS_CASE
    return NEEDS_REASON


def explains(explanation: VarianceExplanation, required: str) -> bool:
    if required == NEEDS_CASE:
        return bool(explanation.case_id or explanation.correction_id)
    return bool(explanation.reason.strip())


def explanation_key(total) -> tuple:
    return (total.total_name, total.level, total.dimension_key, Decimal(total.variance_amount))


async def safe_traits(db: AsyncSession, store_id: str) -> set[str]:
    """A store without a usable reference has no traits; only store-wide rules apply."""
    try:
        return await store_location_traits(db, store_id)
    except ValueError:
        return set()


async def active_rules(db: AsyncSession) -> list[VarianceTolerance]:
    return list(
        (await db.execute(select(VarianceTolerance).where(VarianceTolerance.active.is_(True))))
        .scalars()
        .all()
    )


async def evaluate(db: AsyncSession, day: StoreDay, totals: list[AuditTotal]) -> dict[str, dict]:
    """Outcome, tolerance and explanation for each total, keyed by the total's id."""
    return (await evaluate_days(db, [day], {day.id: totals}))[day.id]


async def evaluate_days(
    db: AsyncSession, days: list[StoreDay], totals_by_day: dict[str, list[AuditTotal]]
) -> dict[str, dict[str, dict]]:
    """Evaluate many store days with one read of the rules and of the explanations."""
    rules = await active_rules(db)
    traits: dict[str, set[str]] = {}
    if rules:
        for store_id in {day.store_id for day in days}:
            traits[store_id] = await safe_traits(db, store_id)
    explanations: dict[str, dict[tuple, VarianceExplanation]] = {}
    day_ids = [day.id for day in days]
    for offset in range(0, len(day_ids), 500):
        rows = (
            await db.execute(
                select(VarianceExplanation)
                .where(VarianceExplanation.store_day_id.in_(day_ids[offset : offset + 500]))
                .order_by(VarianceExplanation.created_at)
            )
        ).scalars()
        for row in rows:
            explanations.setdefault(row.store_day_id, {})[explanation_key(row)] = row
    result = {}
    for day in days:
        latest = explanations.get(day.id, {})
        evaluations = {}
        for total in totals_by_day.get(day.id, []):
            tolerance = tolerance_for(rules, total.total_name, traits.get(day.store_id, set()))
            found = outcome(total.variance_amount, tolerance)
            explanation = (
                latest.get(explanation_key(total)) if total.variance_amount is not None else None
            )
            needs = found in (NEEDS_REASON, NEEDS_CASE)
            evaluations[total.id] = {
                "outcome": found,
                "tolerance": {
                    "within_amount": tolerance.within_amount,
                    "case_amount": tolerance.case_amount,
                    "rule_id": tolerance.rule_id,
                    "total_name": tolerance.total_name,
                    "location_trait": tolerance.location_trait,
                },
                "resolved": not needs or (explanation is not None and explains(explanation, found)),
                "explanation": explanation_view(explanation) if explanation else None,
            }
        result[day.id] = evaluations
    return result


def explanation_view(row: VarianceExplanation) -> dict:
    return {
        "id": row.id,
        "audit_version": row.audit_version,
        "variance_amount": row.variance_amount,
        "outcome": row.outcome,
        "reason": row.reason,
        "case_id": row.case_id,
        "correction_id": row.correction_id,
        "explained_by": row.explained_by,
        "created_at": row.created_at,
    }


def unresolved(evaluations: dict[str, dict]) -> list[str]:
    return [total_id for total_id, item in evaluations.items() if not item["resolved"]]

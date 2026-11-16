"""Configured audit-rule evaluation for a calculated store-day totals version."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from integrations.ontology.nimbus import audit, sla_due
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.cases.identifiers import generate_case_number
from apps.cases.models import Case
from apps.cases.models import Exception as DBException
from apps.controls.rules.models import ConfiguredAuditRule
from apps.totals.calculation import TotalKey, store_location_traits
from apps.totals.models import AuditTotalDefinition


@dataclass(slots=True)
class RuleEvaluation:
    """Rules selected and validated before a totals version is persisted."""

    rules: list[ConfiguredAuditRule]
    observations: dict[str, list[tuple[str | None, Decimal]]]
    errors: dict[str, HTTPException]


def add_configured_totals(
    calculated: dict[TotalKey, Decimal],
    definitions: Sequence[AuditTotalDefinition],
    configured_totals: Mapping[str, Mapping[str | None, Decimal]],
) -> dict[TotalKey, AuditTotalDefinition]:
    """Merge configured totals into a version and map each value to its definition."""
    definition_by_key: dict[TotalKey, AuditTotalDefinition] = {}
    for definition in definitions:
        for dimension, amount in configured_totals.get(definition.id, {}).items():
            key = (f"Configured: {definition.name}", definition.level, dimension)
            calculated[key] = amount
            definition_by_key[key] = definition
    return definition_by_key


async def prepare_rule_evaluation(
    db: AsyncSession,
    calculated: Mapping[TotalKey, Decimal],
    definitions: Sequence[AuditTotalDefinition],
    currency: str,
    business_date: str,
    store_id: str,
) -> RuleEvaluation:
    """Select relevant rules once, validate targets, and capture balancing observations."""
    totals_by_name: dict[str, dict[tuple[str, str | None], Decimal]] = {}
    for (name, level, dimension), amount in calculated.items():
        level_totals = totals_by_name.setdefault(name, {})
        key = (level, dimension)
        level_totals[key] = level_totals.get(key, Decimal("0")) + amount

    definitions_by_total = {f"Configured: {item.name}": item for item in definitions}
    for total_name, definition in definitions_by_total.items():
        # Every configured total is known, so a rule aimed at the wrong level is reported even on a day with no data.
        levels = totals_by_name.setdefault(total_name, {})
        if definition.level == "Store":
            levels.setdefault(("Store", None), Decimal("0"))
    applicable_totals = set(totals_by_name)
    if not applicable_totals:
        return RuleEvaluation(rules=[], observations={}, errors={})

    rules = list(
        (
            await db.execute(
                select(ConfiguredAuditRule)
                .where(
                    ConfiguredAuditRule.enabled.is_(True),
                    ConfiguredAuditRule.status == "Active",
                    ConfiguredAuditRule.total_name.in_(applicable_totals),
                    (ConfiguredAuditRule.start_date.is_(None))
                    | (ConfiguredAuditRule.start_date <= business_date),
                    (ConfiguredAuditRule.end_date.is_(None))
                    | (ConfiguredAuditRule.end_date >= business_date),
                )
                .order_by(
                    ConfiguredAuditRule.execution_order,
                    ConfiguredAuditRule.name,
                    ConfiguredAuditRule.id,
                )
            )
        ).scalars()
    )
    if any(rule.location_traits for rule in rules):
        store_traits = await store_location_traits(db, store_id)
        rules = [
            rule
            for rule in rules
            if not rule.location_traits or set(rule.location_traits).intersection(store_traits)
        ]

    observations: dict[str, list[tuple[str | None, Decimal]]] = {}
    errors: dict[str, HTTPException] = {}
    for rule in rules:
        if not rule_applies_to_currency(rule, currency):
            continue
        available_levels = totals_by_name.get(rule.total_name)
        if available_levels is None:
            continue
        balance_level = rule.balance_level or "Store"
        definition = definitions_by_total.get(rule.total_name)
        valid_levels = (
            {definition.level}
            if definition is not None
            else {"Store", "Register"}
            if rule.total_name == "POS Net"
            else {"Store"}
        )
        if balance_level not in valid_levels:
            errors[rule.id] = HTTPException(
                status_code=422,
                detail=f"Rule {rule.name} targets {balance_level}, but {rule.total_name} is not available at that level",
            )
            continue
        level_observations = [
            (dimension, amount)
            for (level, dimension), amount in available_levels.items()
            if level == balance_level
        ]
        if rule.threshold_currency is None:
            if definition is None or definition.aggregation != "Count":
                errors[rule.id] = HTTPException(
                    status_code=422,
                    detail=f"Currency-free rule {rule.name} requires a Count total",
                )
                continue
        observations[rule.id] = level_observations
    return RuleEvaluation(rules=rules, observations=observations, errors=errors)


def raise_first_rule_error(evaluation: RuleEvaluation) -> None:
    """Refuse a re-total before it changes anything when an active rule cannot be evaluated."""
    for rule in evaluation.rules:
        if error := evaluation.errors.get(rule.id):
            raise error


async def evaluate_configured_rules(
    db: AsyncSession,
    evaluation: RuleEvaluation,
    store_id: str,
    business_date: str,
    currency: str,
) -> None:
    """Create cases for prevalidated rules whose store totals exceed their thresholds."""
    for rule in evaluation.rules:
        if error := evaluation.errors.get(rule.id):
            raise error
        observed = evaluation.observations.get(rule.id, [])
        breaches = [
            (dimension, amount)
            for dimension, amount in observed
            if rule_breaches(amount, rule.threshold, rule.comparison)
        ]
        if breaches:
            await create_rule_breach(db, rule, breaches, store_id, business_date, currency)
            if rule.stop_processing:
                break


def rule_breaches(observed: Decimal, threshold: Decimal, comparison: str) -> bool:
    """Evaluate the configured signed or absolute comparison deterministically."""
    if comparison == "ABS_GT":
        return abs(observed) > threshold
    if comparison == "GT":
        return observed > threshold
    if comparison == "GTE":
        return observed >= threshold
    if comparison == "LT":
        return observed < threshold
    if comparison == "LTE":
        return observed <= threshold
    if comparison == "EQ":
        return observed == threshold
    if comparison == "NE":
        return observed != threshold
    raise ValueError(f"Unsupported audit-rule comparison: {comparison}")


def rule_applies_to_currency(rule: ConfiguredAuditRule, currency: str) -> bool:
    """Currency-scoped rules apply only when their currency matches the store day."""
    return rule.threshold_currency is None or rule.threshold_currency == currency


async def create_rule_breach(
    db: AsyncSession,
    rule: ConfiguredAuditRule,
    breaches: list[tuple[str | None, Decimal]],
    store_id: str,
    business_date: str,
    currency: str,
) -> None:
    """Persist one configured-rule exception unless an open breach already exists."""
    case_type = f"Rule breach: {rule.name}"[:50]
    existing = (
        (
            await db.execute(
                select(Case.id)
                .join(DBException, DBException.case_id == Case.id)
                .where(
                    Case.store_id == store_id,
                    Case.business_date == business_date,
                    DBException.detection_rule_id == rule.id,
                    Case.status.not_in(("Closed",)),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return

    case_id = str(uuid.uuid4())
    amount = sum((abs(observed) for _, observed in breaches), Decimal("0"))
    exposure = amount if rule.threshold_currency is not None else Decimal("0")
    priority = (
        "Critical"
        if rule.severity == "Critical"
        else "High"
        if rule.severity == "High"
        else "Normal"
    )
    db.add(
        Case(
            id=case_id,
            case_number=generate_case_number(business_date),
            status="Open",
            case_type=case_type,
            business_date=business_date,
            store_id=store_id,
            currency=currency if rule.threshold_currency is not None else None,
            total_exception_amount=amount,
            total_exposure=exposure,
            investigation_status="Pending",
            evidence_completeness=0,
            assigned_to=rule.owner,
            priority=priority,
            sla_due_at=sla_due(priority),
        )
    )
    await db.flush()
    for dimension, observed in breaches:
        exception_amount = abs(observed)
        db.add(
            DBException(
                id=str(uuid.uuid4()),
                case_id=case_id,
                exception_type="CONFIGURED_RULE_VARIANCE",
                exception_family="Configured Control",
                source_system="Configured Rule",
                detection_origin="Configured Rule",
                detection_rule_id=rule.id,
                detection_rule_name=rule.name,
                balance_level=rule.balance_level or "Store",
                balance_dimension=dimension,
                exception_amount=exception_amount,
                estimated_exposure=exception_amount if rule.threshold_currency else Decimal("0"),
                severity=rule.severity,
                confidence="High",
                close_impact="Yes" if rule.threshold_currency else "No",
            )
        )
    await db.flush()
    audit(
        db,
        "CONFIGURED_RULE_BREACH",
        case_id,
        "ConfiguredAuditRule",
        rule.id,
        f"{rule.name} breached at {len(breaches)} {rule.balance_level or 'Store'} balance(s); "
        f"{rule.comparison} threshold {rule.threshold}",
        actor="total-engine",
    )

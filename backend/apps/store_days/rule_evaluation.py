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
from apps.totals.calculation import TotalKey
from apps.totals.models import AuditTotalDefinition


@dataclass(slots=True)
class RuleEvaluation:
    """Rules selected and validated before a totals version is persisted."""

    rules: list[ConfiguredAuditRule]
    observations: dict[str, Decimal]


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
) -> RuleEvaluation:
    """Select relevant rules once, validate their targets, and capture store totals."""
    totals_by_name: dict[str, dict[str, Decimal]] = {}
    for (name, level, _dimension), amount in calculated.items():
        level_totals = totals_by_name.setdefault(name, {})
        level_totals[level] = level_totals.get(level, Decimal("0")) + amount

    definitions_by_total = {f"Configured: {item.name}": item for item in definitions}
    for total_name, definition in definitions_by_total.items():
        totals_by_name.setdefault(total_name, {}).setdefault(definition.level, Decimal("0"))
    applicable_totals = set(totals_by_name)
    if not applicable_totals:
        return RuleEvaluation(rules=[], observations={})

    rules = list(
        (
            await db.execute(
                select(ConfiguredAuditRule)
                .where(
                    ConfiguredAuditRule.enabled.is_(True),
                    ConfiguredAuditRule.total_name.in_(applicable_totals),
                )
                .order_by(ConfiguredAuditRule.id)
            )
        ).scalars()
    )

    observations: dict[str, Decimal] = {}
    for rule in rules:
        if not rule_applies_to_currency(rule, currency):
            continue
        available_levels = totals_by_name.get(rule.total_name)
        if available_levels is None:
            continue
        observed = available_levels.get("Store")
        if observed is None:
            raise HTTPException(
                status_code=422,
                detail=f"Rule {rule.name} requires a Store-level total",
            )
        if rule.threshold_currency is None:
            definition = definitions_by_total.get(rule.total_name)
            if definition is None or definition.aggregation != "Count":
                raise HTTPException(
                    status_code=422,
                    detail=f"Currency-free rule {rule.name} requires a Count total",
                )
        observations[rule.id] = observed
    return RuleEvaluation(rules=rules, observations=observations)


async def evaluate_configured_rules(
    db: AsyncSession,
    evaluation: RuleEvaluation,
    store_id: str,
    business_date: str,
    currency: str,
) -> None:
    """Create cases for prevalidated rules whose store totals exceed their thresholds."""
    for rule in evaluation.rules:
        observed = evaluation.observations.get(rule.id)
        if observed is not None and abs(observed) > rule.threshold:
            await create_rule_breach(db, rule, observed, store_id, business_date, currency)


def rule_applies_to_currency(rule: ConfiguredAuditRule, currency: str) -> bool:
    """Currency-scoped rules apply only when their currency matches the store day."""
    return rule.threshold_currency is None or rule.threshold_currency == currency


async def create_rule_breach(
    db: AsyncSession,
    rule: ConfiguredAuditRule,
    observed: Decimal,
    store_id: str,
    business_date: str,
    currency: str,
) -> None:
    """Persist one configured-rule exception unless an open breach already exists."""
    case_type = f"Rule breach: {rule.name}"
    existing = (
        (
            await db.execute(
                select(Case.id).where(
                    Case.store_id == store_id,
                    Case.business_date == business_date,
                    Case.case_type == case_type,
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
    amount = abs(observed)
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
    db.add(
        DBException(
            id=str(uuid.uuid4()),
            case_id=case_id,
            exception_type="CONFIGURED_RULE_VARIANCE",
            exception_family="Configured Control",
            source_system="Configured Rule",
            detection_origin="Configured Rule",
            detection_rule_id=rule.id,
            exception_amount=amount,
            estimated_exposure=exposure,
            severity=rule.severity,
            confidence="High",
            close_impact="Yes" if exposure else "No",
        )
    )
    await db.flush()
    audit(
        db,
        "CONFIGURED_RULE_BREACH",
        case_id,
        "ConfiguredAuditRule",
        rule.id,
        f"{rule.name} observed {observed} over threshold {rule.threshold}",
        actor="total-engine",
    )

"""Shared calculation of versioned total definitions for previews and store days."""

import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.controls.foundation.models import FoundationReference
from apps.controls.rules.transaction_logic import condition_matches
from apps.corrections.effects import effective_amounts, effective_taxes
from apps.totals.combined import calculate_combined
from apps.totals.models import AuditTotalDefinition
from apps.totals.schemas import (
    REALM_FIELDS,
    TotalParameterBody,
    TotalRestrictionBody,
    TotalRollupBody,
)
from apps.totals.sources import source_lineage_for
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)

TRANSACTION_BATCH_SIZE = 500
TotalKey = tuple[str, str, str | None]
SOURCE_TOTALS = {
    "pos-map-v1": "POS Net",
    "processor-map-v1": "Processor Net",
    "bank-map-v1": "Bank Net",
    "erp-map-v1": "ERP Net",
}


DRAFT_STATUS = "Worksheet"
PENDING_APPROVAL_STATUS = "Pending Approval"
# Revisions in these states are stored and audited but never used in calculation.
NOT_LIVE_STATUSES = (DRAFT_STATUS, PENDING_APPROVAL_STATUS)


def applies_on(definition: AuditTotalDefinition, business_date: str) -> bool:
    """Whether a revision's effective dates include a business date."""
    return (definition.start_date is None or definition.start_date <= business_date) and (
        definition.end_date is None or definition.end_date >= business_date
    )


@dataclass(slots=True)
class StoreDayCalculation:
    source_totals: dict[TotalKey, Decimal]
    configured_totals: dict[str, dict[str | None, Decimal]]
    configured_rollups: dict[str, dict[tuple[str | None, str | None], Decimal]]
    definition_counts: dict[str, int]
    currencies: set[str]
    transaction_count: int


async def effective_definitions(
    db: AsyncSession,
    business_date: str,
    store_id: str,
    *,
    validate_all_combined: bool = False,
    draft: AuditTotalDefinition | None = None,
) -> list[AuditTotalDefinition]:
    """Return the live revisions for a store day.

    Worksheet and Pending Approval revisions never take effect. Passing ``draft`` answers
    "what would be effective if this draft were live": it replaces the live revision
    of the same name, subject to the same date, active-flag and trait rules.
    """
    revisions = (
        select(AuditTotalDefinition.name, func.max(AuditTotalDefinition.revision).label("revision"))
        .where(
            AuditTotalDefinition.status.not_in(NOT_LIVE_STATUSES),
            (AuditTotalDefinition.start_date.is_(None))
            | (AuditTotalDefinition.start_date <= business_date),
            (AuditTotalDefinition.end_date.is_(None))
            | (AuditTotalDefinition.end_date >= business_date),
        )
        .group_by(AuditTotalDefinition.name)
        .subquery()
    )
    definitions = list(
        (
            await db.execute(
                select(AuditTotalDefinition)
                .join(
                    revisions,
                    (AuditTotalDefinition.name == revisions.c.name)
                    & (AuditTotalDefinition.revision == revisions.c.revision),
                )
                .where(AuditTotalDefinition.active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    if draft is not None:
        definitions = [row for row in definitions if row.name != draft.name]
        if draft.active and applies_on(draft, business_date):
            definitions.append(draft)
    if any(definition.location_traits for definition in definitions):
        traits = await store_location_traits(db, store_id)
        definitions = keep_trait_applicable(definitions, traits)
    if validate_all_combined:
        # Validate dependencies even when a store day has no transactions to calculate.
        validation_values = {
            definition.id: {}
            for definition in definitions
            if definition.calculation_basis != "Combined"
        }
        calculate_combined(definitions, validation_values)
    return definitions


def trait_excluded(definition: AuditTotalDefinition) -> bool:
    """True for a total this store's traits exclude, kept only so combined totals can count it as zero."""
    return bool(getattr(definition, "trait_excluded", False))


def keep_trait_applicable(
    definitions: list[AuditTotalDefinition], traits: set[str]
) -> list[AuditTotalDefinition]:
    """Drop totals limited to traits the store lacks, except those a kept combined total still adds up.

    An excluded operand counts as zero in its combined total rather than invalidating it, so a
    BSI-only store can still evaluate a combined total that subtracts an Outlet-only one.
    """
    kept = [
        definition
        for definition in definitions
        if not definition.location_traits or set(definition.location_traits).intersection(traits)
    ]
    kept_ids = {definition.id for definition in kept}
    by_id = {definition.id: definition for definition in definitions}
    needed: list[AuditTotalDefinition] = []
    pending = [
        by_id[operand]
        for definition in kept
        if definition.calculation_basis == "Combined"
        for operand in definition.combined_total_ids
        if operand in by_id and operand not in kept_ids
    ]
    while pending:
        definition = pending.pop()
        if definition.id in kept_ids:
            continue
        kept_ids.add(definition.id)
        definition.trait_excluded = True
        needed.append(definition)
        if definition.calculation_basis == "Combined":
            pending.extend(
                by_id[operand]
                for operand in definition.combined_total_ids
                if operand in by_id and operand not in kept_ids
            )
    return [*kept, *needed]


async def store_location_traits(db: AsyncSession, store_id: str) -> set[str]:
    """Read trait membership from the active Store reference's location_traits attribute."""
    store = (
        await db.execute(
            select(FoundationReference).where(
                FoundationReference.category == "Store",
                FoundationReference.code == store_id,
                FoundationReference.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if store is None:
        raise ValueError(
            f"An active store reference is required to resolve location traits for {store_id}"
        )
    traits = (
        store.attributes.get("location_traits", []) if isinstance(store.attributes, dict) else None
    )
    if not isinstance(traits, list) or any(
        not isinstance(trait, str) or not trait.strip() for trait in traits
    ):
        raise ValueError(f"Store {store_id} has invalid location_traits")
    return set(traits)


async def calculate_definitions(
    db: AsyncSession,
    definitions: list[AuditTotalDefinition],
    rows: list[CanonicalTransaction],
    amounts: dict[str, Decimal] | None = None,
) -> tuple[
    dict[str, dict[str | None, Decimal]],
    dict[str, int],
    dict[str, dict[tuple[str | None, str | None], Decimal]],
]:
    taxes: dict[str, Decimal] = {}
    if any(
        definition.measure == "Tax" and definition.calculation_basis != "Combined"
        for definition in definitions
    ):
        taxes = await effective_taxes(db, rows)
    await attach_store_areas(db, definitions, rows)
    related_rows = await load_restricted_realms(db, definitions, rows)
    grouped_values: dict[str, dict[tuple[str | None, str | None], Decimal]] = {}
    counts: dict[str, int] = {}
    for definition in definitions:
        if definition.calculation_basis == "Combined":
            continue
        if trait_excluded(definition):
            grouped_values[definition.id], counts[definition.id] = {}, 0
            continue
        candidates = matching_transactions(definition, rows)
        candidates = apply_restrictions(definition, candidates, related_rows)
        validate_definition_transactions(definition, candidates)
        grouped_values[definition.id], counts[definition.id] = aggregate_parameter_definition(
            definition, candidates, related_rows, amounts, taxes
        )
    calculate_combined(definitions, grouped_values)
    values: dict[str, dict[str | None, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for identifier, grouped in grouped_values.items():
        for (dimension, _rollup_key), amount in grouped.items():
            values[identifier][dimension] += amount
    excluded = {definition.id for definition in definitions if trait_excluded(definition)}
    return (
        {
            identifier: dict(items)
            for identifier, items in values.items()
            if identifier not in excluded
        },
        counts,
        {
            identifier: items
            for identifier, items in grouped_values.items()
            if identifier not in excluded
        },
    )


def uses_store_area(definitions: list[AuditTotalDefinition]) -> bool:
    return any(
        item.get("realm") == "Transaction" and item.get("field") == "store_area"
        for definition in definitions
        for item in (definition.restrictions or [])
    )


async def attach_store_areas(db, definitions, rows) -> None:
    """Give each transaction its store's configured area; a store with none stays empty and matches no area."""
    if not rows or not uses_store_area(definitions):
        return
    store_ids = {row.store_id for row in rows}
    stores = (
        await db.execute(
            select(FoundationReference.code, FoundationReference.attributes).where(
                FoundationReference.category == "Store",
                FoundationReference.code.in_(store_ids),
                FoundationReference.active.is_(True),
            )
        )
    ).all()
    areas = {
        code: attributes.get("area")
        for code, attributes in stores
        if isinstance(attributes, dict) and isinstance(attributes.get("area"), int)
    }
    for row in rows:
        row.store_area = areas.get(row.store_id)


def matching_transactions(definition: AuditTotalDefinition, rows: list[CanonicalTransaction]):
    """Select transactions included by a definition's source, type, and tender filters."""
    source_lineage = source_lineage_for(definition.source_system)
    return [
        row
        for row in rows
        if (source_lineage is None or row.source_lineage == source_lineage)
        and (
            not definition.transaction_types or row.transaction_type in definition.transaction_types
        )
        and (not definition.tender_type or row.tender_type == definition.tender_type)
    ]


async def load_restricted_realms(db, definitions, rows):
    """Load each needed related realm once for this bounded transaction page."""
    requested_realms = set()
    for definition in definitions:
        if definition.calculation_basis == "Combined":
            continue
        for restriction in definition.restrictions or []:
            try:
                validated = TotalRestrictionBody.model_validate(restriction)
            except (ValidationError, TypeError) as exc:
                raise ValueError("A live total has an invalid realm restriction") from exc
            if validated.realm != "Transaction":
                requested_realms.add(validated.realm)
        for parameter in definition.parameters or []:
            try:
                validated_parameter = TotalParameterBody.model_validate(parameter)
            except (ValidationError, TypeError) as exc:
                raise ValueError("A live total has an invalid measure parameter") from exc
            if (
                definition.aggregation == "Sum"
                and REALM_FIELDS[validated_parameter.realm][validated_parameter.field] != "number"
            ):
                raise ValueError("A summed total requires a numeric measure parameter")
            if validated_parameter.realm != "Transaction":
                requested_realms.add(validated_parameter.realm)
        validated_parameters = []
        for parameter in definition.parameters or []:
            try:
                validated_parameters.append(TotalParameterBody.model_validate(parameter))
            except (ValidationError, TypeError) as exc:
                raise ValueError("A live total has an invalid measure parameter") from exc
        measure_realm = validated_parameters[0].realm if validated_parameters else "Transaction"
        seen_rollups = set()
        for rollup in definition.rollups or []:
            try:
                validated_rollup = TotalRollupBody.model_validate(rollup)
            except (ValidationError, TypeError) as exc:
                raise ValueError("A live total has an invalid roll-up field") from exc
            key = (validated_rollup.realm, validated_rollup.field)
            if key in seen_rollups:
                raise ValueError("A live total has duplicate roll-up fields")
            seen_rollups.add(key)
            if definition.calculation_basis != "Combined" and validated_rollup.realm not in {
                "Transaction",
                measure_realm,
            }:
                raise ValueError("A live total has a roll-up outside its measure realm")
            if validated_rollup.realm != "Transaction":
                requested_realms.add(validated_rollup.realm)
    if not requested_realms or not rows:
        return {}

    models = {
        "Line": TransactionLine,
        "Tax": TransactionTax,
        "Tender": TransactionTender,
        "Discount": TransactionDiscount,
    }
    transaction_ids = [row.id for row in rows]
    loaded = {}
    for realm in sorted(requested_realms):
        entities = (
            await db.execute(
                select(models[realm])
                .where(models[realm].transaction_id.in_(transaction_ids))
                .order_by(models[realm].transaction_id, models[realm].id)
            )
        ).scalars()
        grouped_entities: dict[str, list] = defaultdict(list)
        for entity in entities:
            grouped_entities[entity.transaction_id].append(entity)
        loaded[realm] = grouped_entities
    return loaded


def apply_restrictions(definition: AuditTotalDefinition, rows, related_rows):
    """Apply ANDed header and related-record predicates without multiplying totals."""
    restrictions = definition.restrictions or []
    if not restrictions or not rows:
        return rows
    grouped: dict[str, list[dict]] = defaultdict(list)
    for restriction in restrictions:
        grouped[restriction["realm"]].append(restriction)
    transaction_conditions = grouped.pop("Transaction", [])
    selected = [
        row
        for row in rows
        if all(condition_matches(condition, row) for condition in transaction_conditions)
    ]
    if not selected or not grouped:
        return selected
    matches_by_realm: dict[str, set[str]] = {}
    for realm, conditions in grouped.items():
        found = related_rows.get(realm, {})
        matches_by_realm[realm] = {
            transaction_id
            for transaction_id, entities_for_transaction in found.items()
            if any(
                all(condition_matches(condition, entity) for condition in conditions)
                for entity in entities_for_transaction
            )
        }
    return [
        row
        for row in selected
        if all(row.id in matched_ids for matched_ids in matches_by_realm.values())
    ]


def aggregate_parameter_definition(definition, rows, related_rows, amounts, taxes):
    """Sum or count selected values into balancing dimensions and roll-up groups."""
    if definition.parameters:
        parameter = TotalParameterBody.model_validate(definition.parameters[0]).model_dump()
        realm, field = parameter["realm"], parameter["field"]
    else:
        realm = "Transaction"
        field = "$tax" if definition.measure == "Tax" else "signed_amount"
    restrictions_for_realm = [
        item for item in (definition.restrictions or []) if item["realm"] == realm
    ]
    entities_by_transaction = related_rows.get(realm, {}) if realm != "Transaction" else None
    values: dict[tuple[str | None, str | None], Decimal] = defaultdict(Decimal)
    count = 0
    for transaction in rows:
        entities = (
            [transaction]
            if realm == "Transaction"
            else entities_by_transaction.get(transaction.id, [])
        )
        for entity in entities:
            if restrictions_for_realm and not all(
                condition_matches(condition, entity) for condition in restrictions_for_realm
            ):
                continue
            if realm == "Transaction" and field == "$tax":
                value = taxes.get(transaction.id, Decimal(0))
            elif realm == "Transaction" and field == "signed_amount":
                value = (amounts or {}).get(transaction.id, transaction.signed_amount)
            else:
                value = getattr(entity, field)
            if value is None:
                continue
            count += 1
            dimension = parameter_dimension(definition, transaction, entity, realm)
            rollup_key = rollup_dimension_key(definition, transaction, entity)
            values[(dimension, rollup_key)] += (
                Decimal("1") if definition.aggregation == "Count" else Decimal(str(value))
            )
    return dict(values), count


def rollup_dimension_key(definition, transaction, entity):
    """Encode grouping parameters separately from the Store/Register/Cashier dimension."""
    if not definition.rollups:
        return None
    values = []
    for item in definition.rollups:
        rollup = TotalRollupBody.model_validate(item)
        source = transaction if rollup.realm == "Transaction" else entity
        values.append([rollup.realm, rollup.field, getattr(source, rollup.field)])
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) > 2048:
        raise ValueError("A calculated roll-up key exceeds the supported length")
    return encoded


def parameter_dimension(definition, transaction, entity, realm):
    """Roll child measurements up to their transaction's balancing dimension."""
    if definition.level == "Tender" and realm == "Tender":
        return entity.tender_type or "Unspecified"
    return definition_dimension(definition, transaction)


def validate_definition_transactions(
    definition: AuditTotalDefinition, rows: list[CanonicalTransaction]
) -> None:
    """Validate transaction attributes required by a definition's balancing level."""
    if definition.level == "Cashier" and any(not row.cashier_id for row in rows):
        raise ValueError(
            f"Total {definition.name} requires cashier IDs on all matching transactions"
        )


def aggregate_definition(
    definition: AuditTotalDefinition,
    rows: list[CanonicalTransaction],
    taxes: dict[str, Decimal],
    amounts: dict[str, Decimal] | None,
) -> dict[str | None, Decimal]:
    """Aggregate matching transactions by the definition's dimension."""
    groups: dict[str | None, Decimal] = defaultdict(Decimal)
    for row in rows:
        dimension = definition_dimension(definition, row)
        if definition.aggregation == "Count":
            groups[dimension] += 1
        elif definition.measure == "Tax":
            groups[dimension] += taxes.get(row.id, Decimal(0))
        else:
            groups[dimension] += amounts[row.id] if amounts is not None else row.signed_amount
    return dict(groups)


def definition_dimension(definition: AuditTotalDefinition, row: CanonicalTransaction) -> str | None:
    """Return the balancing dimension for a transaction under one definition."""
    if definition.level == "Store":
        return None
    if definition.level == "Register":
        return row.register_id or "Unspecified"
    if definition.level == "Cashier":
        return row.cashier_id
    return row.tender_type or "Unspecified"


async def calculate_store_day(
    db: AsyncSession,
    store_id: str,
    business_date: str,
    definitions: list[AuditTotalDefinition],
) -> StoreDayCalculation:
    """Aggregate one store day in bounded pages, including approved corrections and configured totals."""
    totals: dict[tuple[str, str, str | None], Decimal] = defaultdict(Decimal)
    definition_totals: dict[str, dict[str | None, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    definition_rollups: dict[str, dict[tuple[str | None, str | None], Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    counts: dict[str, int] = defaultdict(int)
    currencies: set[str] = set()
    transaction_count = 0
    last_id: str | None = None
    for definition in definitions:
        if definition.calculation_basis != "Combined" and not trait_excluded(definition):
            definition_totals[definition.id]
            counts[definition.id] = 0

    while True:
        rows = await load_transaction_batch(db, store_id, business_date, last_id)
        if not rows:
            break
        transaction_count += len(rows)
        last_id = rows[-1].id
        amounts = await effective_amounts(db, rows)
        currencies.update(aggregate_source_totals(rows, amounts, totals))
        await aggregate_configured_totals(
            db,
            definitions,
            rows,
            amounts,
            definition_totals,
            definition_rollups,
            counts,
        )

    # Avoid persisting an empty adjustment total, matching the store-day output contract.
    if totals.get(("Approved Adjustment Net", "Store", None)) == 0:
        totals.pop(("Approved Adjustment Net", "Store", None), None)
    return StoreDayCalculation(
        source_totals=totals,
        configured_totals={
            identifier: dict(values) for identifier, values in definition_totals.items()
        },
        configured_rollups={
            identifier: dict(values) for identifier, values in definition_rollups.items()
        },
        definition_counts=dict(counts),
        currencies=currencies,
        transaction_count=transaction_count,
    )


async def load_transaction_batch(
    db: AsyncSession,
    store_id: str,
    business_date: str,
    last_id: str | None,
) -> list[CanonicalTransaction]:
    """Fetch the next stable, bounded page of transactions for one store day."""
    query = select(CanonicalTransaction).where(
        CanonicalTransaction.store_id == store_id,
        CanonicalTransaction.business_date == business_date,
    )
    if last_id is not None:
        query = query.where(CanonicalTransaction.id > last_id)
    result = await db.execute(query.order_by(CanonicalTransaction.id).limit(TRANSACTION_BATCH_SIZE))
    return list(result.scalars().all())


def aggregate_source_totals(rows, amounts, totals) -> set[str]:
    """Accumulate standard source totals and return currencies represented in a page."""
    currencies = set()
    for transaction in rows:
        currencies.add(transaction.currency)
        source_total = SOURCE_TOTALS.get(transaction.source_lineage)
        if source_total:
            totals[(source_total, "Store", None)] += amounts[transaction.id]
        if transaction.source_lineage == "pos-map-v1":
            totals[("POS Net", "Tender", transaction.tender_type or "Unspecified")] += amounts[
                transaction.id
            ]
            totals[("POS Net", "Register", transaction.register_id or "Unspecified")] += amounts[
                transaction.id
            ]
        totals[("Approved Adjustment Net", "Store", None)] += (
            amounts[transaction.id] - transaction.signed_amount
        )
    return currencies


async def aggregate_configured_totals(
    db: AsyncSession,
    definitions: list[AuditTotalDefinition],
    rows: list[CanonicalTransaction],
    amounts: dict[str, Decimal],
    totals: dict[str, dict[str | None, Decimal]],
    rollups: dict[str, dict[tuple[str | None, str | None], Decimal]],
    counts: dict[str, int],
) -> None:
    """Calculate one page of configured totals and add it to the running aggregates."""
    definition_values, batch_counts, grouped_values = await calculate_definitions(
        db, definitions, rows, amounts
    )
    for definition_id, dimensions in definition_values.items():
        for dimension, amount in dimensions.items():
            totals[definition_id][dimension] += amount
    for definition_id, groups in grouped_values.items():
        for key, amount in groups.items():
            rollups[definition_id][key] += amount
    for definition_id, count in batch_counts.items():
        counts[definition_id] += count


def dependencies_for_preview(
    target: AuditTotalDefinition,
    effective: list[AuditTotalDefinition],
) -> list[AuditTotalDefinition]:
    """Include only the target and its effective, explicitly pinned dependencies."""
    available = {row.id: row for row in effective if row.name != target.name}
    available[target.id] = target
    found: dict[str, AuditTotalDefinition] = {}

    def visit(row: AuditTotalDefinition) -> None:
        if row.id in found:
            return
        found[row.id] = row
        if row.calculation_basis == "Combined":
            for identifier in row.combined_total_ids:
                if identifier in available:
                    visit(available[identifier])

    visit(target)
    dependencies = list(found.values())
    validation_values = {
        definition.id: {}
        for definition in dependencies
        if definition.calculation_basis != "Combined"
    }
    calculate_combined(dependencies, validation_values)
    return dependencies

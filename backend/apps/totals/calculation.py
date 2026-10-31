"""Shared calculation of versioned total definitions for previews and store days."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.corrections.effects import effective_amounts, effective_taxes
from apps.totals.combined import calculate_combined
from models import CanonicalTransaction, FoundationReference
from apps.totals.models import AuditTotalDefinition

TRANSACTION_BATCH_SIZE = 500
SOURCE_TOTALS = {
    "pos-map-v1": "POS Net",
    "processor-map-v1": "Processor Net",
    "bank-map-v1": "Bank Net",
    "erp-map-v1": "ERP Net",
}


@dataclass(slots=True)
class StoreDayCalculation:
    source_totals: dict[tuple[str, str, str | None], Decimal]
    configured_totals: dict[str, dict[str | None, Decimal]]
    definition_counts: dict[str, int]
    currencies: set[str]
    transaction_count: int


async def effective_definitions(
    db: AsyncSession,
    business_date: str,
    store_id: str,
    *,
    validate_all_combined: bool = False,
) -> list[AuditTotalDefinition]:
    revisions = (select(AuditTotalDefinition.name, func.max(AuditTotalDefinition.revision).label("revision"))
        .where((AuditTotalDefinition.start_date.is_(None)) | (AuditTotalDefinition.start_date <= business_date),
               (AuditTotalDefinition.end_date.is_(None)) | (AuditTotalDefinition.end_date >= business_date))
        .group_by(AuditTotalDefinition.name).subquery())
    definitions = list((await db.execute(select(AuditTotalDefinition).join(
        revisions, (AuditTotalDefinition.name == revisions.c.name)
        & (AuditTotalDefinition.revision == revisions.c.revision))
        .where(AuditTotalDefinition.active.is_(True)))).scalars().all())
    if any(definition.location_traits for definition in definitions):
        traits = await store_location_traits(db, store_id)
        definitions = [definition for definition in definitions
                       if not definition.location_traits or set(definition.location_traits).intersection(traits)]
    if validate_all_combined:
        # Validate dependencies even when a store day has no transactions to calculate.
        validation_values = {
            definition.id: {}
            for definition in definitions
            if definition.calculation_basis != "Combined"
        }
        calculate_combined(definitions, validation_values)
    return definitions


async def store_location_traits(db: AsyncSession, store_id: str) -> set[str]:
    """Read trait membership from the active Store reference's location_traits attribute."""
    store = (await db.execute(select(FoundationReference).where(
        FoundationReference.category == "Store", FoundationReference.code == store_id,
        FoundationReference.active.is_(True)))).scalar_one_or_none()
    if store is None:
        raise ValueError(f"An active store reference is required to resolve location traits for {store_id}")
    traits = store.attributes.get("location_traits", []) if isinstance(store.attributes, dict) else None
    if not isinstance(traits, list) or any(not isinstance(trait, str) or not trait.strip() for trait in traits):
        raise ValueError(f"Store {store_id} has invalid location_traits")
    return set(traits)


async def calculate_definitions(
    db: AsyncSession, definitions: list[AuditTotalDefinition], rows: list[CanonicalTransaction],
    amounts: dict[str, Decimal] | None = None,
) -> tuple[dict[str, dict[str | None, Decimal]], dict[str, int]]:
    taxes: dict[str, Decimal] = {}
    if any(definition.measure == "Tax" and definition.calculation_basis != "Combined" for definition in definitions):
        taxes = await effective_taxes(db, rows)
    values: dict[str, dict[str | None, Decimal]] = {}
    counts: dict[str, int] = {}
    for definition in definitions:
        if definition.calculation_basis == "Combined":
            continue
        candidates = rows
        if definition.source_system:
            candidates = [row for row in candidates if row.source_lineage.startswith(definition.source_system.lower())]
        if definition.transaction_types:
            candidates = [row for row in candidates if row.transaction_type in definition.transaction_types]
        if definition.tender_type:
            candidates = [row for row in candidates if row.tender_type == definition.tender_type]
        if definition.level == "Cashier" and any(not row.cashier_id for row in candidates):
            raise ValueError(f"Total {definition.name} requires cashier IDs on all matching transactions")
        counts[definition.id] = len(candidates)
        groups: dict[str | None, Decimal] = defaultdict(Decimal)
        for row in candidates:
            if definition.level == "Store":
                key = None
            elif definition.level == "Register":
                key = row.register_id or "Unspecified"
            elif definition.level == "Cashier":
                key = row.cashier_id
            else:
                key = row.tender_type or "Unspecified"
            if definition.aggregation == "Count":
                groups[key] += 1
            elif definition.measure == "Tax":
                groups[key] += taxes.get(row.id, Decimal(0))
            else:
                groups[key] += amounts[row.id] if amounts is not None else row.signed_amount
        values[definition.id] = dict(groups)
    calculate_combined(definitions, values)
    return values, counts


async def calculate_store_day(
    db: AsyncSession, store_id: str, business_date: str,
    definitions: list[AuditTotalDefinition],
) -> StoreDayCalculation:
    """Aggregate one store day in bounded pages, including approved corrections and configured totals."""
    totals: dict[tuple[str, str, str | None], Decimal] = defaultdict(Decimal)
    definition_totals: dict[str, dict[str | None, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    counts: dict[str, int] = defaultdict(int)
    currencies: set[str] = set()
    transaction_count = 0
    last_id: str | None = None
    for definition in definitions:
        if definition.calculation_basis != "Combined":
            definition_totals[definition.id]
            counts[definition.id] = 0

    while True:
        query = select(CanonicalTransaction).where(
            CanonicalTransaction.store_id == store_id,
            CanonicalTransaction.business_date == business_date,
        )
        if last_id is not None:
            query = query.where(CanonicalTransaction.id > last_id)
        rows = (await db.execute(query.order_by(CanonicalTransaction.id).limit(
            TRANSACTION_BATCH_SIZE))).scalars().all()
        if not rows:
            break
        transaction_count += len(rows)
        last_id = rows[-1].id
        amounts = await effective_amounts(db, rows)

        for transaction in rows:
            currencies.add(transaction.currency)
            source_total = SOURCE_TOTALS.get(transaction.source_lineage)
            if source_total:
                totals[(source_total, "Store", None)] += amounts[transaction.id]
            if transaction.source_lineage == "pos-map-v1":
                totals[("POS Net", "Tender", transaction.tender_type or "Unspecified")] += amounts[transaction.id]
                totals[("POS Net", "Register", transaction.register_id or "Unspecified")] += amounts[transaction.id]
            totals[("Approved Adjustment Net", "Store", None)] += (
                amounts[transaction.id] - transaction.signed_amount
            )

        definition_values, batch_counts = await calculate_definitions(db, definitions, rows, amounts)
        for definition_id, dimensions in definition_values.items():
            for dimension, amount in dimensions.items():
                definition_totals[definition_id][dimension] += amount
        for definition_id, count in batch_counts.items():
            counts[definition_id] += count

    # Avoid persisting an empty adjustment total, matching the store-day output contract.
    if totals.get(("Approved Adjustment Net", "Store", None)) == 0:
        totals.pop(("Approved Adjustment Net", "Store", None), None)
    return StoreDayCalculation(
        source_totals=totals,
        configured_totals={identifier: dict(values) for identifier, values in definition_totals.items()},
        definition_counts=dict(counts),
        currencies=currencies,
        transaction_count=transaction_count,
    )


def dependencies_for_preview(
    target: AuditTotalDefinition, effective: list[AuditTotalDefinition],
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

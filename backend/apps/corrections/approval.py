"""Transactional work used to approve a proposed transaction correction."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import HTTPException
from model_base import utcnow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.corrections.effects import effective_amounts
from apps.corrections.reconciliation import (
    invalidate_case_evidence,
    linked_cases,
    related_exception_ids,
    requeue_linked_exceptions,
)
from apps.exports.models import ExportItem
from apps.ingest.common import LINEAGE, SIGN
from apps.ingest.models import RegisterEvent, SourceRecord
from apps.security import scope
from apps.security.sensitive_data import mask_pan_value, redact_sensitive_fields
from apps.store_days.freshness import invalidate_totals, lock_store_day
from apps.system.options import credit_card_number_mask
from apps.transactions import detail as transaction_detail
from apps.transactions.customer_attributes import (
    normalize_customer_attributes,
    redact_customer_attributes,
)
from apps.transactions.detail import MAX_MONEY
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from apps.transactions.references import normalize_user_references


@dataclass
class ApprovalContext:
    """Validated, locked state required to apply an approval atomically."""

    store_id: str
    business_date: str
    values: dict
    transaction: CanonicalTransaction | None = None
    day: object | None = None
    cases: list = field(default_factory=list)
    locked_transactions: list | None = None
    missing_transaction: tuple | None = None
    detail: dict | None = None
    mask_character: str = "•"
    item_line: TransactionLine | None = None
    discount_line: TransactionDiscount | None = None
    tax_item_line: TransactionLine | None = None
    tender_line: TransactionTender | None = None
    tax_line: TransactionTax | None = None


async def prepare_approval(
    db: AsyncSession, adjustment, user: dict, missing_transaction: tuple | None = None
) -> ApprovalContext:
    """Validate scope, lock affected rows, and check dependent workflows."""
    mask_character = await credit_card_number_mask(db)
    if adjustment.adjustment_type == "MissingTransaction":
        amount, transaction_type, business_date, event_at, settlement_date, source, detail = (
            missing_transaction
        )
        context = ApprovalContext(
            store_id=str(adjustment.proposed_values["store_id"]),
            business_date=business_date,
            values=adjustment.proposed_values,
            missing_transaction=(amount, transaction_type, event_at, settlement_date, source),
            detail=detail,
            mask_character=mask_character,
            cases=[],
        )
    else:
        transaction = (
            await db.execute(
                select(CanonicalTransaction)
                .where(CanonicalTransaction.id == adjustment.transaction_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if transaction is None:
            raise HTTPException(status_code=409, detail="Adjustment transaction no longer exists")
        context = ApprovalContext(
            store_id=transaction.store_id,
            business_date=transaction.business_date,
            values=adjustment.proposed_values,
            transaction=transaction,
            mask_character=mask_character,
            cases=[],
        )

    scope.require_store(user, context.store_id)
    context.day = await lock_store_day(db, context.store_id, context.business_date)
    if adjustment.adjustment_type == "MissingTransaction":
        register_id = context.values.get("register_id")
        transaction_number = context.values.get("transaction_number")
        if register_id and transaction_number:
            existing_transaction = (
                await db.execute(
                    select(CanonicalTransaction.id)
                    .where(
                        CanonicalTransaction.store_id == context.store_id,
                        CanonicalTransaction.business_date == context.business_date,
                        CanonicalTransaction.register_id == register_id,
                        CanonicalTransaction.transaction_number == transaction_number,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            existing_event = (
                await db.execute(
                    select(RegisterEvent.id)
                    .where(
                        RegisterEvent.store_id == context.store_id,
                        RegisterEvent.business_date == context.business_date,
                        RegisterEvent.register_id == register_id,
                        RegisterEvent.transaction_number == transaction_number,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing_transaction or existing_event:
                raise HTTPException(
                    status_code=409,
                    detail="This register transaction number has already arrived",
                )
    if context.transaction is not None:
        context.cases = await linked_cases(db, context.transaction)
        await ensure_linked_workflows_are_idle(db, context.cases)
        if adjustment.adjustment_type == "Item":
            values = context.values
            line_no = values.get("line_no")
            if line_no is None:
                lines = (
                    (
                        await db.execute(
                            select(TransactionLine)
                            .where(TransactionLine.transaction_id == context.transaction.id)
                            .order_by(TransactionLine.line_no)
                            .limit(2)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(lines) != 1:
                    raise HTTPException(
                        status_code=409,
                        detail="This older item correction needs an explicit line and must be proposed again",
                    )
                item_line = lines[0]
                values["line_no"] = item_line.line_no
            else:
                item_line = (
                    await db.execute(
                        select(TransactionLine)
                        .where(
                            TransactionLine.transaction_id == context.transaction.id,
                            TransactionLine.line_no == line_no,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if item_line is None:
                    raise HTTPException(
                        status_code=409, detail="The corrected item line no longer exists"
                    )
            before_item = values.get("before_item_code")
            if before_item is not None and item_line.item_code != before_item:
                raise HTTPException(
                    status_code=409,
                    detail="This item changed after the correction was proposed; propose it again against the current item",
                )
            if before_item is None:
                values["before_item_code"] = item_line.item_code
            adjustment.proposed_values = values
            context.item_line = item_line
        elif adjustment.adjustment_type == "Discount":
            values = context.values
            operation = values.get("operation", "update")
            if operation in {"update", "delete"}:
                discount_line = (
                    await db.execute(
                        select(TransactionDiscount)
                        .where(
                            TransactionDiscount.id == values["discount_id"],
                            TransactionDiscount.transaction_id == context.transaction.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if discount_line is None:
                    raise HTTPException(
                        status_code=409,
                        detail="The corrected discount no longer exists",
                    )
                for field in ("kind", "code", "description", "line_no"):
                    if getattr(discount_line, field) != values.get(f"before_{field}"):
                        raise HTTPException(
                            status_code=409,
                            detail="Discount details changed after the correction was proposed; propose it again",
                        )
                if str(discount_line.amount) != values.get("before_amount"):
                    raise HTTPException(
                        status_code=409,
                        detail="Discount amount changed after the correction was proposed; propose it again",
                    )
                if operation == "update":
                    amount = Decimal(str(values["amount"]))
                    if amount != 0 and (
                        context.transaction.signed_amount == 0
                        or (amount > 0) != (context.transaction.signed_amount > 0)
                    ):
                        raise HTTPException(
                            status_code=422,
                            detail="Discount amount must use the transaction's financial sign",
                        )
                context.discount_line = discount_line
                line_no = values["line_no"] if operation == "update" else None
            else:
                line_no = values["line_no"]
                amount = Decimal(str(values["amount"]))
                if (
                    amount == 0
                    or context.transaction.signed_amount == 0
                    or (amount > 0) != (context.transaction.signed_amount > 0)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="New discount amount must use the transaction's financial sign",
                    )
            if line_no is not None:
                line_exists = await db.scalar(
                    select(TransactionLine.id).where(
                        TransactionLine.transaction_id == context.transaction.id,
                        TransactionLine.line_no == line_no,
                    )
                )
                if line_exists is None:
                    raise HTTPException(
                        status_code=409, detail="The selected item line no longer exists"
                    )
            adjustment.proposed_values = values
        elif adjustment.adjustment_type == "TaxLine":
            values = context.values
            tax_item_line = (
                await db.execute(
                    select(TransactionLine)
                    .where(
                        TransactionLine.transaction_id == context.transaction.id,
                        TransactionLine.line_no == values["line_no"],
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if tax_item_line is None:
                raise HTTPException(
                    status_code=409, detail="The corrected item line no longer exists"
                )
            for field, attribute in (
                ("tax_code", "tax_code"),
                ("rate", "tax_rate"),
                ("tax_amount", "tax_amount"),
            ):
                current = getattr(tax_item_line, attribute)
                current_value = current if field == "tax_code" else str(current)
                if current_value != values.get(f"before_{field}"):
                    raise HTTPException(
                        status_code=409,
                        detail="Line tax changed after the correction was proposed; propose it again",
                    )
            context.tax_item_line = tax_item_line
        elif adjustment.adjustment_type == "TaxDetail":
            values = context.values
            tax_line = (
                await db.execute(
                    select(TransactionTax)
                    .where(
                        TransactionTax.id == values["tax_id"],
                        TransactionTax.transaction_id == context.transaction.id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if tax_line is None:
                raise HTTPException(
                    status_code=409, detail="The corrected tax row no longer exists"
                )
            for field in ("tax_code", "tax_name", "rate", "taxable_amount", "tax_amount"):
                before_field = f"before_{field}"
                current = str(getattr(tax_line, field))
                if current != values.get(before_field):
                    raise HTTPException(
                        status_code=409,
                        detail="Tax details changed after the correction was proposed; propose it again",
                    )
            context.tax_line = tax_line
        elif adjustment.adjustment_type == "Customer":
            values = context.values
            for field in ("customer_id", "customer_identification_type"):
                before_field = f"before_{field}"
                if field in values:
                    current = getattr(context.transaction, field)
                    before_value = values.get(before_field, current)
                    if current != before_value:
                        raise HTTPException(
                            status_code=409,
                            detail="Customer information changed after the correction was proposed; propose it again",
                        )
                    values[before_field] = before_value
            adjustment.proposed_values = values
        elif adjustment.adjustment_type == "Employee":
            values = context.values
            for field in ("cashier_id", "salesperson_id"):
                if field not in values:
                    continue
                before_field = f"before_{field}"
                current = getattr(context.transaction, field)
                before_value = values.get(before_field, current)
                if current != before_value:
                    raise HTTPException(
                        status_code=409,
                        detail="Employee details changed after the correction was proposed; propose it again",
                    )
                values[before_field] = before_value
            adjustment.proposed_values = values
        elif adjustment.adjustment_type == "CustomerAttribute":
            values = context.values
            attribute_type = values["attribute_type"]
            attributes = context.transaction.customer_attributes or {}
            current = attributes.get(attribute_type)
            before_value = values.get("before_value")
            if current != before_value:
                raise HTTPException(
                    status_code=409,
                    detail="Customer attributes changed after the correction was proposed; propose it again",
                )
            values["before_value"] = before_value
            adjustment.proposed_values = values
        elif adjustment.adjustment_type == "UserReference":
            values = context.values
            field = values["reference_field"]
            current = mask_pan_value(
                (context.transaction.user_references or {}).get(field), context.mask_character
            )
            before_value = values.get("before_value", current)
            if current != before_value:
                raise HTTPException(
                    status_code=409,
                    detail="The transaction reference changed after the correction was proposed; propose it again",
                )
            values["before_value"] = before_value
            adjustment.proposed_values = values
        elif adjustment.adjustment_type in {"Tender", "Reference"}:
            values = context.values
            tender_count = await db.scalar(
                select(func.count(TransactionTender.id)).where(
                    TransactionTender.transaction_id == context.transaction.id
                )
            )
            if values.get("tender_count", tender_count) != tender_count:
                raise HTTPException(
                    status_code=409,
                    detail="Tender details changed after the correction was proposed; propose it again",
                )
            tender_id = values.get("tender_id")
            if tender_id is None and tender_count > 1:
                raise HTTPException(
                    status_code=409,
                    detail="This older tender correction needs a tender selection and must be proposed again",
                )
            if tender_id:
                tender_line = (
                    await db.execute(
                        select(TransactionTender)
                        .where(
                            TransactionTender.id == tender_id,
                            TransactionTender.transaction_id == context.transaction.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if tender_line is None:
                    raise HTTPException(
                        status_code=409, detail="The corrected tender no longer exists"
                    )
                field = "tender_type" if adjustment.adjustment_type == "Tender" else "reference"
                before_field = f"before_{field}"
                before_value = values.get(before_field, getattr(tender_line, field))
                if getattr(tender_line, field) != before_value:
                    raise HTTPException(
                        status_code=409,
                        detail="This tender changed after the correction was proposed; propose it again",
                    )
                values[before_field] = before_value
                context.tender_line = tender_line
            else:
                field = "tender_type" if adjustment.adjustment_type == "Tender" else "reference"
                before_field = f"before_{field}"
                current = (
                    context.transaction.tender_type
                    if adjustment.adjustment_type == "Tender"
                    else context.transaction.payment_reference
                )
                before_value = values.get(before_field, current)
                if current != before_value:
                    raise HTTPException(
                        status_code=409,
                        detail="This transaction value changed after the correction was proposed; propose it again",
                    )
                values[before_field] = before_value
            adjustment.proposed_values = values
        if adjustment.adjustment_type == "Amount":
            context.locked_transactions = await lock_related_transactions(db, context.transaction)
            context.transaction = next(
                row for row in context.locked_transactions if row.id == adjustment.transaction_id
            )
            validate_effective_amount(
                context.transaction,
                context.values,
                await effective_amounts(db, [context.transaction]),
            )
    return context


async def ensure_linked_workflows_are_idle(db: AsyncSession, cases: list) -> None:
    """Prevent changing evidence while a case workflow or export is in flight."""
    if any(case.status in {"Resolving", "Pending Validation"} for case in cases):
        raise HTTPException(
            status_code=409,
            detail="A linked case has an active workflow; resolve it before approving this correction",
        )
    if not cases:
        return
    pending = (
        await db.execute(
            select(ExportItem.id)
            .where(
                ExportItem.object_id.in_([case.id for case in cases]),
                ExportItem.dataset.in_(["resolutions", "adjustments"]),
                ExportItem.state == "Pending",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if pending:
        raise HTTPException(
            status_code=409,
            detail="A linked case has a pending export; retry or cancel the batch before approving this correction",
        )


async def lock_related_transactions(db: AsyncSession, transaction: CanonicalTransaction) -> list:
    """Lock the complete linked exception set in stable order for amount checks."""
    transaction_ids = await related_exception_ids(db, transaction)
    return (
        (
            await db.execute(
                select(CanonicalTransaction)
                .where(CanonicalTransaction.id.in_(transaction_ids))
                .order_by(CanonicalTransaction.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


def validate_effective_amount(
    transaction: CanonicalTransaction, values: dict, amounts: dict
) -> None:
    """Reject effective amounts that overflow or reverse transaction direction."""
    current = amounts[transaction.id]
    result = (
        Decimal(str(values["corrected_amount"]))
        if "corrected_amount" in values
        else current + Decimal(str(values["amount_delta"]))
    )
    if abs(result) > MAX_MONEY:
        raise HTTPException(
            status_code=422, detail="Adjusted amount exceeds supported monetary precision"
        )
    expected_sign = SIGN.get(transaction.transaction_type)
    if expected_sign is None or result * expected_sign < 0:
        raise HTTPException(
            status_code=422,
            detail="Adjusted amount cannot reverse the transaction's financial sign",
        )


async def apply_approval_effects(
    db: AsyncSession, adjustment, context: ApprovalContext, actor: str
) -> None:
    """Apply reconciliation, freshness, and missing-transaction effects in this transaction."""
    await invalidate_case_evidence(db, context.cases, adjustment.id, actor)
    if adjustment.adjustment_type == "Amount":
        await requeue_linked_exceptions(db, context.transaction, context.locked_transactions, actor)
    if context.day:
        await invalidate_totals(db, context.day, actor)
    if context.missing_transaction:
        adjustment.transaction_id = await create_missing_transaction(db, adjustment, context)
    elif adjustment.adjustment_type == "Item":
        if context.item_line is None:
            raise HTTPException(
                status_code=409, detail="The corrected item line is no longer available"
            )
        context.item_line.item_code = context.values["item"]
    elif adjustment.adjustment_type == "Discount":
        if context.transaction is None:
            raise HTTPException(
                status_code=409, detail="The corrected transaction is no longer available"
            )
        operation = context.values.get("operation", "update")
        if operation == "add":
            context.discount_line = TransactionDiscount(
                id=str(uuid.uuid4()),
                transaction_id=context.transaction.id,
                kind=context.values["kind"],
                code=context.values["code"],
                description=context.values["description"],
                line_no=context.values["line_no"],
                amount=Decimal(str(context.values["amount"])),
            )
            db.add(context.discount_line)
            context.values["created_discount_id"] = context.discount_line.id
            adjustment.proposed_values = dict(context.values)
        elif operation == "delete":
            if context.discount_line is None:
                raise HTTPException(
                    status_code=409, detail="The corrected discount is no longer available"
                )
            await db.delete(context.discount_line)
        else:
            if context.discount_line is None:
                raise HTTPException(
                    status_code=409, detail="The corrected discount is no longer available"
                )
            for field in ("kind", "code", "description", "line_no"):
                setattr(context.discount_line, field, context.values[field])
            context.discount_line.amount = Decimal(str(context.values["amount"]))
        line_count, gross = (
            await db.execute(
                select(
                    func.count(TransactionLine.id),
                    func.coalesce(func.sum(TransactionLine.gross_amount), 0),
                ).where(TransactionLine.transaction_id == context.transaction.id)
            )
        ).one()
        line_tax = await db.scalar(
            select(func.coalesce(func.sum(TransactionLine.tax_amount), 0)).where(
                TransactionLine.transaction_id == context.transaction.id
            )
        )
        tax_total = await db.scalar(
            select(func.coalesce(func.sum(TransactionTax.tax_amount), 0)).where(
                TransactionTax.transaction_id == context.transaction.id
            )
        )
        discount_total = await db.scalar(
            select(func.coalesce(func.sum(TransactionDiscount.amount), 0)).where(
                TransactionDiscount.transaction_id == context.transaction.id
            )
        )
        tender_count, tender_total = (
            await db.execute(
                select(
                    func.count(TransactionTender.id),
                    func.coalesce(func.sum(TransactionTender.amount), 0),
                ).where(TransactionTender.transaction_id == context.transaction.id)
            )
        ).one()
        context.transaction.detail_status = transaction_detail.check_totals(
            Decimal(gross),
            Decimal(discount_total),
            Decimal(line_tax if line_count else tax_total),
            Decimal(tender_total),
            context.transaction.signed_amount,
            has_lines=line_count > 0,
            has_tenders=tender_count > 0,
        )["status"]
    elif adjustment.adjustment_type == "TaxLine":
        if context.tax_item_line is None:
            raise HTTPException(
                status_code=409, detail="The corrected item line tax is no longer available"
            )
        context.tax_item_line.tax_code = context.values["tax_code"]
        context.tax_item_line.tax_rate = Decimal(str(context.values["rate"]))
        context.tax_item_line.tax_amount = Decimal(str(context.values["tax_amount"]))
    elif adjustment.adjustment_type == "TaxDetail":
        if context.tax_line is None:
            raise HTTPException(
                status_code=409, detail="The corrected tax row is no longer available"
            )
        context.tax_line.tax_code = context.values["tax_code"]
        context.tax_line.tax_name = context.values["tax_name"]
        context.tax_line.rate = Decimal(str(context.values["rate"]))
        context.tax_line.taxable_amount = Decimal(str(context.values["taxable_amount"]))
        context.tax_line.tax_amount = Decimal(str(context.values["tax_amount"]))
    elif adjustment.adjustment_type == "Customer":
        if context.transaction is None:
            raise HTTPException(
                status_code=409, detail="The corrected transaction is no longer available"
            )
        for field in ("customer_id", "customer_identification_type"):
            if field in context.values:
                setattr(context.transaction, field, context.values[field])
    elif adjustment.adjustment_type == "Employee":
        if context.transaction is None:
            raise HTTPException(
                status_code=409, detail="The corrected transaction is no longer available"
            )
        for field in ("cashier_id", "salesperson_id"):
            if field in context.values:
                setattr(context.transaction, field, context.values[field])
    elif adjustment.adjustment_type == "CustomerAttribute":
        if context.transaction is None:
            raise HTTPException(
                status_code=409, detail="The corrected transaction is no longer available"
            )
        attributes = dict(context.transaction.customer_attributes or {})
        attribute_type = context.values["attribute_type"]
        value = context.values["value"]
        if value:
            attributes[attribute_type] = value
        else:
            attributes.pop(attribute_type, None)
        context.transaction.customer_attributes = redact_customer_attributes(
            attributes, context.mask_character
        )
    elif adjustment.adjustment_type == "UserReference":
        if context.transaction is None:
            raise HTTPException(
                status_code=409, detail="The corrected transaction is no longer available"
            )
        references = redact_sensitive_fields(
            context.transaction.user_references or {}, context.mask_character
        )
        field = context.values["reference_field"]
        value = context.values["value"]
        if value:
            references[field] = value
        else:
            references.pop(field, None)
        context.transaction.user_references = references or None
    elif adjustment.adjustment_type in {"Tender", "Reference"}:
        if context.transaction is None:
            raise HTTPException(
                status_code=409, detail="The corrected transaction is no longer available"
            )
        field = "tender_type" if adjustment.adjustment_type == "Tender" else "reference"
        value_field = field if field == "tender_type" else "reference"
        value = context.values[value_field]
        if context.tender_line is not None:
            setattr(context.tender_line, field, value)
            tender_count = await db.scalar(
                select(func.count(TransactionTender.id)).where(
                    TransactionTender.transaction_id == context.transaction.id
                )
            )
            if context.values.get("updates_header", tender_count <= 1):
                if adjustment.adjustment_type == "Tender":
                    context.transaction.tender_type = value
                else:
                    context.transaction.payment_reference = value
        elif adjustment.adjustment_type == "Tender":
            context.transaction.tender_type = value
        else:
            context.transaction.payment_reference = value


async def create_missing_transaction(db: AsyncSession, adjustment, context: ApprovalContext) -> str:
    """Create immutable source lineage and its canonical transaction together."""
    amount, transaction_type, event_at, settlement_date, source = context.missing_transaction
    values = context.values
    source_id = str(uuid.uuid4())
    payload = json.dumps(
        {
            "adjustment_id": adjustment.id,
            "values": redact_sensitive_fields(values, context.mask_character),
            "rationale": adjustment.rationale,
        },
        sort_keys=True,
        default=str,
    ).encode()
    now = utcnow()
    db.add(
        SourceRecord(
            id=source_id,
            source_system="ManualAdjustment",
            source_version="1.0",
            source_record_id=f"adjustment-{adjustment.id}",
            delivery_id=adjustment.id,
            payload=payload,
            payload_hash=hashlib.sha256(payload).hexdigest(),
            received_at=now,
            source_event_time=event_at,
            status="Processed",
            extracted_at=now,
        )
    )
    await db.flush()
    signed_amount = -amount if transaction_type in {"Return", "Refund"} else amount
    transaction_id = str(uuid.uuid4())
    db.add(
        CanonicalTransaction(
            id=transaction_id,
            business_date=context.business_date,
            transaction_date=event_at,
            event_timestamp=event_at,
            processing_timestamp=utcnow(),
            settlement_date=settlement_date,
            transaction_type=transaction_type,
            transaction_subtype=None,
            source_lineage=LINEAGE[source],
            currency=str(values.get("currency") or "USD"),
            signed_amount=signed_amount,
            quantity=values.get("quantity"),
            store_id=context.store_id,
            customer_id=values.get("customer_id") or None,
            customer_identification_type=values.get("customer_identification_type") or None,
            cashier_id=values.get("cashier_id") or None,
            salesperson_id=values.get("salesperson_id") or None,
            user_references=redact_sensitive_fields(
                normalize_user_references(values.get("user_references")), context.mask_character
            ),
            customer_attributes=redact_customer_attributes(
                normalize_customer_attributes(values.get("customer_attributes")),
                context.mask_character,
            ),
            register_id=values.get("register_id"),
            transaction_number=values.get("transaction_number"),
            channel_id=str(values.get("channel_id") or "store"),
            tender_type=(
                context.detail["tenders"][0]["tender_type"]
                if context.detail and context.detail["tenders"]
                else values.get("tender_type")
            ),
            payment_reference=mask_pan_value(
                context.detail["tenders"][0]["reference"] or values.get("payment_reference")
                if context.detail and context.detail["tenders"]
                else values.get("payment_reference"),
                context.mask_character,
            ),
            settlement_reference=mask_pan_value(
                values.get("settlement_reference"), context.mask_character
            ),
            reconciliation_status="Unmatched",
            source_record_id=source_id,
            disposition="Open",
            original_reference=mask_pan_value(
                context.detail["original_reference"] if context.detail else None,
                context.mask_character,
            ),
            return_reason=context.detail["return_reason"] if context.detail else None,
            detail_status=transaction_detail.check(context.detail, signed_amount)["status"]
            if context.detail
            else ("Balanced" if values.get("tender_type") else "No detail"),
        )
    )
    if context.detail:
        for line in context.detail["lines"]:
            line["source_attributes"] = redact_sensitive_fields(
                line["source_attributes"], context.mask_character
            )
            db.add(TransactionLine(id=str(uuid.uuid4()), transaction_id=transaction_id, **line))
        for tax in context.detail["taxes"]:
            db.add(TransactionTax(id=str(uuid.uuid4()), transaction_id=transaction_id, **tax))
        for discount in context.detail["discounts"]:
            db.add(
                TransactionDiscount(id=str(uuid.uuid4()), transaction_id=transaction_id, **discount)
            )
        for tender in context.detail["tenders"]:
            tender["reference"] = mask_pan_value(tender["reference"], context.mask_character)
            tender["authorization"] = mask_pan_value(
                tender["authorization"], context.mask_character
            )
            tender["source_attributes"] = redact_sensitive_fields(
                tender["source_attributes"], context.mask_character
            )
            db.add(TransactionTender(id=str(uuid.uuid4()), transaction_id=transaction_id, **tender))
    if values.get("tender_type") and not (context.detail and context.detail["tenders"]):
        db.add(
            TransactionTender(
                id=str(uuid.uuid4()),
                transaction_id=transaction_id,
                tender_type=values["tender_type"],
                amount=signed_amount,
                reference=mask_pan_value(values.get("payment_reference"), context.mask_character),
                authorization=None,
            )
        )
    await db.flush()
    return transaction_id

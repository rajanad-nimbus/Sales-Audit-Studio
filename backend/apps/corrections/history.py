"""Field-level change history for a transaction: what each correction changed, before and after.

Source records remain unchanged. Approved item replacements update only the canonical item line;
other corrections are replayed in effective order. Proposed and rejected corrections show their
captured before/after values but do not move the running state.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from apps.corrections.models import TransactionAdjustment
from apps.security.sensitive_data import mask_pan_value
from apps.transactions.customer_attributes import redact_customer_attribute_value
from apps.transactions.detail import cent_value
from apps.transactions.views import mask_customer_id

# adjustment type -> (field shown, the stored keys that set it)
FIELDS = {
    "Amount": ("Amount", ("amount_delta", "corrected_amount")),
    "Tax": ("Tax", ("tax_delta",)),
    "Discount": ("Discount", ("kind", "code", "description", "line_no", "amount")),
    "Tender": ("Tender", ("tender_type",)),
    "Reference": ("Payment reference", ("reference",)),
    "Item": ("Item", ("item",)),
    "Customer": ("Customer information", ("customer_id", "customer_identification_type")),
    "Employee": ("Employee details", ("cashier_id", "salesperson_id")),
    "CustomerAttribute": ("Customer attribute", ("value",)),
    "UserReference": ("Retailer reference", ("value",)),
}
MONEY_FIELDS = {"Amount", "Tax"}
ORDER_FALLBACK = datetime.min


def applied_order(adjustment: TransactionAdjustment) -> tuple:
    when = adjustment.reviewed_at or adjustment.created_at
    return (when.replace(tzinfo=None) if when else ORDER_FALLBACK, adjustment.id)


def build_history(initial: dict[str, object], adjustments: Iterable[TransactionAdjustment]) -> dict:
    """``initial`` holds the recorded amount, tax, tender and payment reference."""
    running = dict(initial)
    if isinstance(initial.get("items"), dict):
        running["items"] = dict(initial["items"])
    if isinstance(initial.get("discounts"), dict):
        running["discounts"] = {
            discount_id: dict(values) for discount_id, values in initial["discounts"].items()
        }
    if isinstance(initial.get("tenders"), dict):
        running["tenders"] = {
            tender_id: dict(values) for tender_id, values in initial["tenders"].items()
        }
    if isinstance(initial.get("user_references"), dict):
        running["user_references"] = dict(initial["user_references"])
    if isinstance(initial.get("customer_attributes"), dict):
        running["customer_attributes"] = dict(initial["customer_attributes"])
    changes = []
    for adjustment in sorted(adjustments, key=applied_order):
        entry = {
            "id": adjustment.id,
            "adjustment_type": adjustment.adjustment_type,
            "status": adjustment.status,
            "applied": adjustment.status == "Approved",
            "rationale": adjustment.rationale,
            "proposed_by": adjustment.proposed_by,
            "proposed_at": adjustment.created_at,
            "reviewed_by": adjustment.reviewed_by,
            "reviewed_at": adjustment.reviewed_at,
        }
        shown = FIELDS.get(adjustment.adjustment_type)
        values = adjustment.proposed_values if isinstance(adjustment.proposed_values, dict) else {}
        if adjustment.adjustment_type == "TaxDetail":
            fields = ("tax_code", "tax_name", "rate", "taxable_amount", "tax_amount")
            changes.append(
                {
                    **entry,
                    "field": f"Tax row {values.get('tax_code', '')}",
                    "before": {field: values.get(f"before_{field}") for field in fields},
                    "after": {field: values.get(field) for field in fields},
                    "change": None,
                }
            )
            continue
        if adjustment.adjustment_type == "TaxLine":
            fields = ("tax_code", "rate", "tax_amount")
            changes.append(
                {
                    **entry,
                    "field": f"Line {values.get('line_no')} tax",
                    "before": {field: values.get(f"before_{field}") for field in fields},
                    "after": {field: values.get(field) for field in fields},
                    "change": None,
                }
            )
            continue
        if adjustment.adjustment_type == "Discount":
            fields = ("kind", "code", "description", "line_no", "amount")
            operation = values.get("operation", "update")
            discount_id = (
                values.get("discount_id") or values.get("created_discount_id") or adjustment.id
            )
            state = running.setdefault("discounts", {})
            discount_state = state.get(discount_id, {})
            if operation == "add":
                before = None
                after = {key: values.get(key) for key in fields}
                label = values.get("code") or values.get("kind")
            elif operation == "delete":
                before = {
                    key: values.get(f"before_{key}", discount_state.get(key)) for key in fields
                }
                after = None
                label = values.get("before_code") or values.get("before_kind")
            else:
                before = {
                    key: values.get(f"before_{key}", discount_state.get(key)) for key in fields
                }
                after = {key: values.get(key) for key in fields}
                label = values.get("before_code") or values.get("code") or values.get("kind")
            changes.append(
                {
                    **entry,
                    "field": f"Discount {label}",
                    "before": before,
                    "after": after,
                    "change": None,
                }
            )
            if entry["applied"]:
                if operation == "add":
                    state[discount_id] = after
                elif operation == "delete":
                    state.pop(discount_id, None)
                else:
                    state[discount_id] = after
            continue
        if shown is None:
            # A missing transaction is new data, not a change to a field of this one.
            changes.append(
                {
                    **entry,
                    "field": adjustment.adjustment_type,
                    "before": None,
                    "after": values or None,
                    "change": None,
                }
            )
            continue
        field, keys = shown
        key = next((k for k in keys if k in values), None)
        if key is None:
            changes.append(
                {
                    **entry,
                    "field": field,
                    "before": None,
                    "after": None,
                    "change": None,
                    "invalid": True,
                }
            )
            continue
        if adjustment.adjustment_type == "Item":
            line_no = values.get("line_no")
            state = running.setdefault("items", {})
            line_key = str(line_no) if line_no is not None else ""
            before = values.get("before_item_code", state.get(line_key))
            after = values[key]
            changes.append(
                {
                    **entry,
                    "field": f"Item line {line_no}" if line_no is not None else field,
                    "before": before,
                    "after": after,
                    "change": None,
                }
            )
            if entry["applied"] and line_key:
                state[line_key] = after
            continue
        if adjustment.adjustment_type == "Customer":
            before = {
                "customer_id": mask_customer_id(values.get("before_customer_id")),
                "customer_identification_type": values.get("before_customer_identification_type"),
            }
            after = {
                "customer_id": mask_customer_id(
                    values.get("customer_id", values.get("before_customer_id"))
                ),
                "customer_identification_type": values.get(
                    "customer_identification_type",
                    values.get("before_customer_identification_type"),
                ),
            }
            changes.append(
                {
                    **entry,
                    "field": field,
                    "before": before,
                    "after": after,
                    "change": None,
                }
            )
            if entry["applied"]:
                if "customer_id" in values:
                    running["customer_id"] = after["customer_id"]
                if "customer_identification_type" in values:
                    running["customer_identification_type"] = after["customer_identification_type"]
            continue
        if adjustment.adjustment_type == "Employee":
            fields = ("cashier_id", "salesperson_id")
            before = {
                field: values.get(f"before_{field}", running.get(field))
                for field in fields
                if field in values
            }
            after = {field: values.get(field) for field in before}
            changes.append(
                {
                    **entry,
                    "field": "Employee details",
                    "before": before,
                    "after": after,
                    "change": None,
                }
            )
            if entry["applied"]:
                running.update(after)
            continue
        if adjustment.adjustment_type == "UserReference":
            reference_field = values.get("reference_field")
            state = running.setdefault("user_references", {})
            before = mask_pan_value(values.get("before_value", state.get(reference_field)))
            after = mask_pan_value(values.get("value"))
            changes.append(
                {
                    **entry,
                    "field": f"{field} {reference_field}",
                    "before": before,
                    "after": after,
                    "change": None,
                }
            )
            if entry["applied"] and reference_field:
                if values.get("value"):
                    state[reference_field] = after
                else:
                    state.pop(reference_field, None)
            continue
        if adjustment.adjustment_type == "CustomerAttribute":
            attribute_type = values.get("attribute_type")
            state = running.setdefault("customer_attributes", {})
            before = values.get("before_value", state.get(attribute_type))
            after = values.get("value")
            safe_before = redact_customer_attribute_value(attribute_type, before)
            safe_after = (
                None
                if after in (None, "")
                else redact_customer_attribute_value(attribute_type, after)
            )
            changes.append(
                {
                    **entry,
                    "field": f"Customer attribute {attribute_type}",
                    "before": safe_before,
                    "after": safe_after,
                    "change": None,
                }
            )
            if entry["applied"] and attribute_type:
                if after:
                    state[attribute_type] = safe_after
                else:
                    state.pop(attribute_type, None)
            continue
        if adjustment.adjustment_type in {"Tender", "Reference"} and values.get("tender_id"):
            tender_id = values["tender_id"]
            state = running.setdefault("tenders", {})
            tender_state = state.setdefault(tender_id, {})
            state_key = "tender_type" if adjustment.adjustment_type == "Tender" else "reference"
            before_key = f"before_{state_key}"
            before = values.get(before_key, tender_state.get(state_key))
            after = values[key]
            changes.append(
                {
                    **entry,
                    "field": f"{state_key.replace('_', ' ').title()} tender {tender_id[:8]}",
                    "before": before,
                    "after": after,
                    "change": None,
                }
            )
            if entry["applied"]:
                tender_state[state_key] = after
                if values.get("updates_header"):
                    running["tender" if state_key == "tender_type" else "reference"] = after
            continue
        state_key = {
            "Amount": "amount",
            "Tax": "tax",
            "Tender": "tender",
            "Reference": "reference",
        }[adjustment.adjustment_type]
        before_key = f"before_{state_key}"
        before = values[before_key] if before_key in values else running.get(state_key)
        try:
            if adjustment.adjustment_type in MONEY_FIELDS:
                value = cent_value(values[key], key)
                after = before + value if key.endswith("_delta") else value
            else:
                after = values[key]
        except (ValueError, TypeError, ArithmeticError):
            changes.append(
                {
                    **entry,
                    "field": field,
                    "before": before,
                    "after": None,
                    "change": None,
                    "invalid": True,
                }
            )
            continue
        change = (
            (after - before) if isinstance(after, Decimal) and isinstance(before, Decimal) else None
        )
        changes.append(
            {**entry, "field": field, "before": before, "after": after, "change": change}
        )
        if entry["applied"]:
            running[state_key] = after
    return {
        "original": initial,
        "current": running,
        "changes": changes,
    }

"""Field-level change history for a transaction: what each correction changed, before and after.

The source transaction is never edited. Each correction is replayed in the order the effective
amounts apply them (when it was reviewed, else when it was proposed), so a field's "before" is
what an earlier approved correction left it as. Proposed and rejected corrections are listed with
the value they would have set but do not move the running value.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from apps.corrections.models import TransactionAdjustment
from apps.transactions.detail import cent_value

# adjustment type -> (field shown, the stored keys that set it)
FIELDS = {
    "Amount": ("Amount", ("amount_delta", "corrected_amount")),
    "Tax": ("Tax", ("tax_delta",)),
    "Tender": ("Tender", ("tender_type",)),
    "Reference": ("Payment reference", ("reference",)),
    "Item": ("Item", ("item",)),
}
MONEY_FIELDS = {"Amount", "Tax"}
ORDER_FALLBACK = datetime.min


def applied_order(adjustment: TransactionAdjustment) -> tuple:
    when = adjustment.reviewed_at or adjustment.created_at
    return (when.replace(tzinfo=None) if when else ORDER_FALLBACK, adjustment.id)


def build_history(initial: dict[str, object], adjustments: Iterable[TransactionAdjustment]) -> dict:
    """``initial`` holds the recorded amount, tax, tender and payment reference."""
    running = dict(initial)
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
        state_key = {
            "Amount": "amount",
            "Tax": "tax",
            "Tender": "tender",
            "Reference": "reference",
            "Item": "item",
        }[adjustment.adjustment_type]
        before = running.get(state_key)
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

"""Dataset catalog shared by export APIs and delivery tracking."""

DATASETS = {
    "resolutions": "Closed, validated cases with decision, disposition and validation result",
    "adjustments": "Approved financial adjustments from validated cases (journal lines for ERP/GL)",
    "audited_totals": "Current versioned totals selected for each destination from closed, reconciled store days",
    "audited_transactions": "Canonical transaction, item, tax, discount, and tender data from closed store days",
    "transaction_corrections": "Approved, governed corrections to canonical transactions",
    "audit": "Audit trail events since the last delivery (with hashes)",
}

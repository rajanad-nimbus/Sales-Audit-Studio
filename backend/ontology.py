"""Enterprise ontology stand-in: authoritative definitions served over REST and read by the agents."""

ONTOLOGY_VERSION = "ontology-v1.0"

DEFAULT_EVIDENCE = ["Source record", "Settlement status"]

EXCEPTION_TYPES = {
    "CONFIGURED_RULE_VARIANCE": {
        "label": "Configured Audit Rule Breach", "family": "Configured Control", "exposure": "amount",
        "evidence": ["Calculated total", "Rule definition", "Source transaction lineage"],
        "known_causes": ["Policy threshold breach", "Source data anomaly", "Configuration change"],
        "disposition": ["Escalation", "Manual Review", "amount", "Review configured rule evidence and resolve or adjust policy"],
    },
    "TIMING_DIFFERENCE": {
        "label": "Timing Difference", "family": "Settlement", "exposure": "zero",
        "evidence": ["Refund confirmation", "Timestamp", "Settlement status"],
        "known_causes": ["Late refund", "Processor delay", "Cutoff timing"],
        "disposition": ["Timing", "Disposition Only", "zero",
                        "Apply timing classification -> Schedule validation -> Monitor settlement -> Close"],
    },
    "DUPLICATE_SALES": {
        "label": "Duplicate Sales", "family": "Transaction Audit", "exposure": "amount",
        "evidence": ["POS journal", "Payment authorization", "Settlement status"],
        "known_causes": ["POS retransmission", "Operator double-entry"],
        "disposition": ["Correction", "Governed Adjustment", "amount",
                        "Reverse duplicate -> Validate processor and GL -> Close"],
    },
    "MISSING_REFUND": {
        "label": "Missing Refund", "family": "Payment Reconciliation", "exposure": "amount",
        "evidence": ["POS return record", "Processor refund status", "Bank posting"],
        "known_causes": ["Refund rejected by processor", "Refund not submitted", "Late refund"],
        "disposition": ["Recovery", "Source Reprocess", "amount",
                        "Reprocess refund -> Validate bank posting -> Close"],
    },
    "UNMATCHED_SALE": {
        "label": "Unmatched Sale", "family": "Payment Reconciliation", "exposure": "amount",
        "evidence": ["POS journal", "Processor capture status", "Authorization record"],
        "known_causes": ["Capture not submitted", "Processor file missing"],
        "disposition": ["Recovery", "Source Reprocess", "amount",
                        "Request capture file -> Replay record -> Validate -> Close"],
    },
    "AMOUNT_MISMATCH": {
        "label": "Amount Mismatch", "family": "Payment Reconciliation", "exposure": "amount",
        "evidence": ["POS journal", "Processor capture detail", "Tender breakdown"],
        "known_causes": ["Tip or surcharge not in POS", "Partial capture", "Keying error"],
        "disposition": ["Correction", "Governed Adjustment", "amount",
                        "Adjust difference -> Validate processor and GL -> Close"],
    },
    "ORPHAN_PAYMENT": {
        "label": "Orphan Payment", "family": "Payment Reconciliation", "exposure": "amount",
        "evidence": ["Processor record", "POS journal search", "Customer reference"],
        "known_causes": ["POS record not delivered", "Wrong merchant account"],
        "disposition": ["Escalation", "Manual Review", "amount",
                        "Route to payments specialist with documented findings"],
    },
    "CONNECTOR_TIMEOUT": {
        "label": "Connector Failure", "family": "Ingestion", "exposure": "zero",
        "evidence": ["Connector log", "Delivery manifest"],
        "known_causes": ["Source endpoint timeout", "Credential expiry"],
        "disposition": ["Recovery", "Connector Retry", "zero",
                        "Retry delivery -> Validate completeness -> Close"],
    },
    "INCOMPLETE_FEED": {
        "label": "Incomplete Feed", "family": "Completeness", "exposure": "zero",
        "evidence": ["POS control manifest", "Delivery manifest", "Register sequence log"],
        "known_causes": ["Record rejected at ingestion", "Delivery truncated", "Register offline"],
        "disposition": ["Recovery", "Source Reprocess", "zero",
                        "Request missing records -> Replay -> Re-run control -> Close"],
    },
    "BALANCING_VARIANCE": {
        "label": "Balancing Variance", "family": "Completeness", "exposure": "amount",
        "evidence": ["POS control manifest", "POS journal", "Register totals"],
        "known_causes": ["Retransmitted records", "Declared total error", "Late voids"],
        "disposition": ["Escalation", "Manual Review", "amount",
                        "Route to store audit with control totals and journal"],
    },
    "BANK_VARIANCE": {
        "label": "Bank Deposit Variance", "family": "Bank Reconciliation", "exposure": "amount",
        "evidence": ["Bank deposit detail", "Processor settlement batch", "Fee schedule"],
        "known_causes": ["Processor fee deduction", "Chargeback", "Partial settlement"],
        "disposition": ["Escalation", "Manual Review", "amount",
                        "Route to treasury with settlement and deposit evidence"],
    },
    "MISSING_DEPOSIT": {
        "label": "Missing Bank Deposit", "family": "Bank Reconciliation", "exposure": "amount",
        "evidence": ["Bank statement", "Processor settlement batch", "Deposit schedule"],
        "known_causes": ["Deposit delayed", "Wrong account", "Settlement hold"],
        "disposition": ["Escalation", "Manual Review", "amount",
                        "Route to treasury; monitor next banking day"],
    },
    "GL_VARIANCE": {
        "label": "GL Posting Variance", "family": "GL Reconciliation", "exposure": "amount",
        "evidence": ["GL posting reference", "POS matched total", "Period status"],
        "known_causes": ["Mapping error", "Partial posting", "Rounding policy"],
        "disposition": ["Correction", "Governed Adjustment", "amount",
                        "Post adjustment -> Validate GL acknowledgment -> Close"],
    },
    "MISSING_GL_POSTING": {
        "label": "Missing GL Posting", "family": "GL Reconciliation", "exposure": "amount",
        "evidence": ["ERP acknowledgment", "Export record", "Period status"],
        "known_causes": ["Export not run", "ERP rejected posting", "Period closed"],
        "disposition": ["Recovery", "Export Reprocess", "amount",
                        "Re-export -> Validate ERP acknowledgment -> Close"],
    },
    "SHOPIFY_UNMATCHED_REFUND": {
        "label": "Shopify Unmatched Refund", "family": "Payment Reconciliation", "exposure": "amount",
        "evidence": ["Shopify refund record", "Order details", "Refund timeline"],
        "known_causes": ["Order not synchronized", "Refund issued for deleted order"],
        "disposition": ["Escalation", "Manual Review", "amount",
                        "Route to merchant with refund and order search results"],
    },
    "SHOPIFY_PARTIAL_REFUND": {
        "label": "Shopify Partial Refund", "family": "Payment Reconciliation", "exposure": "zero",
        "evidence": ["Shopify order", "Refund details", "Line items"],
        "known_causes": ["Customer requested partial return", "Item unavailable"],
        "disposition": ["Timing", "Disposition Only", "zero",
                        "Verify refund reason -> Match to order -> Close"],
    },
    "SHOPIFY_OVERAGE_REFUND": {
        "label": "Shopify Overage Refund", "family": "Payment Reconciliation", "exposure": "amount",
        "evidence": ["Shopify order", "Refund record", "Line item breakdown"],
        "known_causes": ["Refund includes discount or fees", "Multiple refunds per order"],
        "disposition": ["Escalation", "Manual Review", "amount",
                        "Route to merchant with refund breakdown -> Investigate -> Adjust"],
    },
}


# Where each evidence requirement is actually retrieved from. Explicit on purpose: guessing from the wording
# of the requirement mis-routed "Bank posting" to POS and let requirements with no match count ANY record.
#   {"source": X}            records from source system X for the case's store and business date
#   {"source": X, "type": T} ... of one transaction type
#   {"any": True}            the store day's transactions from any source (only where the requirement means exactly that)
#   {"totals": True}         the store day's audit totals
#   {"ontology": True}       the ontology's own definition for the exception type
#   None                     not held in any ingested data: always "Unavailable" until a person supplies it
EVIDENCE_SOURCES: dict[str, dict | None] = {
    "Source record": {"source": "POS"},
    "Settlement status": {"source": "Processor"},
    "Calculated total": {"totals": True},
    "Rule definition": {"ontology": True},
    "Source transaction lineage": {"any": True},
    "Refund confirmation": {"source": "Processor", "type": "Refund"},
    "Timestamp": {"any": True},
    "POS journal": {"source": "POS"},
    "Payment authorization": {"source": "Processor"},
    "POS return record": {"source": "POS"},
    "Processor refund status": {"source": "Processor"},
    "Bank posting": {"source": "Bank"},
    "Processor capture status": {"source": "Processor"},
    "Authorization record": {"source": "Processor"},
    "Processor capture detail": {"source": "Processor"},
    "Tender breakdown": {"source": "POS"},
    "Processor record": {"source": "Processor"},
    "POS journal search": {"source": "POS"},
    "Customer reference": None,
    "Connector log": None,
    "Delivery manifest": None,
    "POS control manifest": {"source": "POS"},
    "Register sequence log": {"source": "POS"},
    "Register totals": {"source": "POS"},
    "Bank deposit detail": {"source": "Bank"},
    "Processor settlement batch": {"source": "Processor"},
    "Fee schedule": None,
    "Bank statement": {"source": "Bank"},
    "Deposit schedule": None,
    "GL posting reference": {"source": "ERP"},
    "POS matched total": {"source": "POS"},
    "Period status": None,
    "ERP acknowledgment": {"source": "ERP"},
    "Export record": {"source": "ERP"},
    "Shopify refund record": {"source": "Shopify"},
    "Order details": {"source": "Shopify"},
    "Refund timeline": {"source": "Shopify"},
    "Shopify order": {"source": "Shopify"},
    "Refund details": {"source": "Shopify"},
    "Line items": {"source": "Shopify"},
    "Refund record": {"source": "Shopify"},
    "Line item breakdown": {"source": "Shopify"},
}


def get(exception_type: str) -> dict | None:
    return EXCEPTION_TYPES.get(exception_type)

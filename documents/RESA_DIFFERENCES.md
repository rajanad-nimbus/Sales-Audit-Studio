# ZeTSA compared with Oracle ReSA: behavioural differences

[RESA_PARITY.md](RESA_PARITY.md) answers "which ReSA workflows does ZeTSA cover?". This document answers a
different question: **where does ZeTSA behave differently from ReSA, and why?** It is meant for product,
audit and engineering readers who need to know what changes for an auditor moving from ReSA to ZeTSA.

## Sources and confidence

| Tag | Source | Reliability |
| --- | --- | --- |
| **Guide** | [Oracle Retail Sales Audit User Guide 15.0.1](resa-1501-ug.pdf) | Quoted from the guide. It describes release 15.0.1, so newer releases may differ. |
| **Schema** | Oracle RMS physical data model (`SA_STORE_DAY`, `SA_ERROR_CODES`, and related tables) | Column names, valid values and constraints only. No behaviour. |
| **Code** | ZeTSA backend and frontend as of commit `c10ac23` | Verified by reading or running it. |

Anything not tagged is an inference. Batch programs, the RIB interfaces and the later ReSA releases were not
available, so batch behaviour is not compared here.

## Summary

ZeTSA keeps ReSA's vocabulary (store day, totals, audit rules, errors, over/short, missing transactions,
overall, data and audit status) but changes **who is allowed to do what**:

1. Corrections need a **proposal and an independent approver**. ReSA lets an auditor edit a transaction directly.
2. Errors become **cases** with evidence, recommendations and decisions. ReSA has an error list with fix or override.
3. A store day has an explicit **Closed** state with sign-off, and an **agent** signs off and closes days that match perfectly. ReSA has no equivalent of either.
4. Audit rules are **authored in Ontology Studio**, not in ZeTSA. ReSA has an in-product Rule Maintenance wizard.
5. Identity belongs to an **external identity provider**. ReSA manages employees itself.
6. ZeTSA adds **Shopify** as a source, an **assistant** and **agents**. ReSA has POS and OMS only.

## 1. Store day lifecycle and status

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Status fields | Three stored fields: store status (worksheet, closed, fuel closed), data status, audit status. **Schema** | Lifecycle status (Open, Auditing, Closed) is stored. Data status and audit status are **derived** from the data on every read. **Code** |
| Audit status values | Unaudited, Store Errors Pending, HQ Errors Pending, Re-Totalling/Auditing Required, Totaled, Audited. **Guide** | The same names are offered as filters. There is no separate HQ-errors state. **Code** |
| Overall status | Sales Audit in Progress or Complete. **Guide** | Same two values, derived. **Code** |
| Reaching Audited | The store/day is set to Audited **automatically** once all errors are corrected or overridden. **Guide** | Audited needs totals that are current and clean, and a **recorded sign-off** for that totals version. A perfectly matching day is signed off by the agent. **Code** |
| Closing | The store/day is closed in the POS system. After that, store data cannot be updated. **Schema** | Closing is a ZeTSA action with a closure request. It is blocked by open cases, unexplained variances, missing transactions and an earlier unclosed day. A person can **reopen** a day. The agent then leaves it alone. **Code** |
| Creating a store day | Created by a batch at the end of the previous day. **Guide** | Opened by the agent when data arrives, or when the previous day closes. With the "worked in order" option, a store's next day is held back while an earlier day is unclosed. **Code** |
| Deleting | An auditor can delete a store day from More Actions. **Guide** | Only a store day with **no data** can be deleted. **Code** |
| Day-close marker | The POS day-close (`DCLOSE`) transaction defines when the business day ends. **Guide** | The marker is recorded and shown but is **not required** to close. **Code** |
| Locks | Read and write locks per store day, with at most one writable lock. **Schema** | Database-level locks serialise reconciliation, totalling and closing. There is no user-visible lock. **Code** |
| Late data | Not described in the guide. | Late data marks the totals stale, clears the sign-off, and the agent re-totals and may sign off again. **Code** |

## 2. Who audits

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Normal path | The auditor reviews every exception for their stores, then corrects or overrides it. **Guide** | An automatic path exists. The store-day agent totals, signs off and closes a day whose data matches the control total, with clean totals and no open cases. Anything else is left for a person. **Code** |
| Audit trail of that decision | Audit trail screens for transactions and totals. **Guide** | The agent's sign-off records a rationale and an audit event under the actor `store-day-agent`. **Code** |
| Today's day | Not described. | Signed off as soon as it matches, but closed only after its business date has passed. **Code** |
| Turning it off | Not applicable. | A system option turns automatic sign-off and close off. **Code** |

## 3. Importing data and duplicates

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Sources | POS and OMS, received as RTLOG files. **Guide** | POS (including RTLOG), OMS and **Shopify**, each with its own lineage, feed profile and quarantine review. **Code** |
| Duplicates | Duplicate records from OMS and POS are **filtered on receipt**, before loading. **Guide** | Both copies are stored. A `DUPLICATE_SALES` finding is raised at reconciliation, including repeats across days when a lookback is configured. **Code** |
| Resolving a duplicate | Not an auditor task, because the duplicate never loads. | The agent resolves an **exact copy** (same amount, tenders, lines, reference and cashier as the original): the copy is marked `Duplicate`, excluded from totals, readiness counts, register summary and cash, and the case is closed as Auto-Resolved. A copy that differs is left for a person. **Code** |
| Replayed deliveries | Not described. | Replaying the same delivery is rejected by idempotency before any of this applies. **Code** |
| Import validation | Validated against reference data and configuration options during import. **Guide** | Quarantine review, delivery history and source configuration. Reference-data checks run as audit rules. **Code** |
| Transaction types | `SALE`, `RETURN`, `VOID`, `PVOID`, `EEXCH`, `PAIDIN`, `PAIDOU`, `NOSALE`, `TOTAL`, `DCLOSE`, `PUMPT`, `TANKDP` and others. **Guide** | The RTLOG parser recognises these, including `PUMPT` and `TANKDP`, as register events. Sales and returns are canonical transactions. Coverage is "partial" per RESA_PARITY. **Code** |

## 4. Errors, rules and overrides

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Error definitions | `SA_ERROR_CODES`: description, target form and tab, recommended solution, **store override** and **HQ override** flags, required flag. **Schema** | `sales_audit_error_definitions`: description, form, tab, recommended solution, active flag and export-block destinations. **No store/HQ override flags and no required flag.** **Code** |
| Error handling | An error is fixed in Transaction Detail, or acknowledged and overridden. **Guide** | An error becomes a **case**. The case has evidence, a recommendation, a finance decision (approve or escalate), a workflow and a validation step. **Code** |
| Overriding | Allowed per error code according to who may override. **Schema** | An evidence override needs a reason and a **second finance approver**. **Code** |
| Error impact | Errors can block export to a system until corrected, or block one transaction. **Guide** | Export eligibility is checked against each error's blocking destinations. **Code** |
| Authoring rules | A Rule Maintenance wizard: Worksheet, then submit, then approve. Worksheet errors go to an error worksheet table. **Guide** | **Read-only.** Audit rules come from an approved Ontology Studio release. The in-product rule routes only list rules. With no approved release, totalling is **refused** rather than falling back to defaults. **Code** |
| New error codes | Defined in ReSA before a rule uses them. **Guide** | Rules can raise codes defined in the error code table even when the code is not built in. Case creation falls back to that table. **Code** |
| When rules run | Each time re-total or audit is run. **Guide** | During reconciliation and totalling, including automatically after import when that option is on. **Code** |
| Mass resolution | A mass-resolution pop-up type per error code. **Schema** | Bulk case actions with limits: finance role, a financial-movement ceiling, and policy checks. **Code** |

## 5. Transactions and corrections

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Editing | The auditor edits items, tenders, discounts, taxes, customer, reference and paid-out details directly, and adds comments. **Guide** | Field-level changes are **proposed** and need independent approval before they apply. Missing transactions are created the same way. **Code** |
| History | Revisions: the older version moves to a revision table. **Guide** | Approved adjustments form a history that can be replayed. **Code** |
| Reversal on export | If an earlier revision was exported, the reversal is included in the next transmission. **Guide** | Correction datasets, retries and acknowledgements per destination batch. **Code** |
| Missing transactions | Managed from the store-day summary. **Guide** | Sequence gaps are detected by transaction number. Recovery rechecks that the gap is current and asks which source owns the sale. **Code** |
| Immutability | Not described. | The audit history is **append-only** in the database. **Code** |

## 6. Totals and balancing

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Definitions | Created, edited and status-changed in ReSA: basic information, attributes, realms and joins, parameters and roll-ups, restrictions, combined totals, location traits, usages. **Guide** | The same shape, with revisions, an impact preview, and activation that needs a **second approver**. Realms and joins are a constrained, approved set. **Code** |
| Combined totals | Combine other totals. **Schema** | Supported. A trait-excluded total counts as zero inside a combined total instead of invalidating it. **Code** |
| Balancing | Balancing level and totals compared with POS or store values, with over/short and miscellaneous totals. **Guide** | Calculated, declared and variance values per total and version. **POS to GL Variance** is a **required** close comparison. **POS Control Declared** and **Processor to Bank Variance** are optional. Tolerances are configurable. **Code** |
| Re-totalling | Run from the interface. **Guide** | Automatic when data changes, or manual. Stale totals block close. **Code** |
| Reports | Flash Sales and Flash Totals. **Guide** | Same two, plus variance, error, tax and store-day reports. **Code** |

## 7. Export

| Topic | ReSA | ZeTSA |
| --- | --- | --- |
| Targets | RMS, SIM, ReIM, RA and third-party systems through staging tables. **Guide** | Configured destinations with batches. **Code** |
| What is exported | Transactions with status Passed, Non-fatal or Delete. **Guide** | Eligibility rules per destination, including error blocking. **Code** |
| Delivery | Batch programs write interface files. **Guide** | Stable idempotency keys, retries, acknowledgements and searchable history. **Code** |

## 8. Platform differences with no ReSA equivalent

| Area | ReSA | ZeTSA |
| --- | --- | --- |
| Identity | Employees and store assignment are managed in ReSA. **Guide** | Identity comes from an external provider. ZeTSA stores only store and trait assignments. **Code** |
| Separation of duties | Not enforced by the guide's workflows. | Independent approvers for corrections, total activation, evidence overrides and rule changes. **Code** |
| Agents | None. | The store-day agent, plus case investigation, bulk actions and an assistant. **Code** |
| Vouchers | Escheatment by Store, Issuer, Recipient, HQ hierarchy. **Guide** | Voucher aging, jurisdiction routing, retention and review, with stronger card defaults. **Code** |
| Card data | Not described. | A configurable security level and a card-number mask. **Code** |

## Open items

- The ReSA behaviour for HQ errors pending and store errors pending is not modelled separately in ZeTSA. Confirm whether auditors need the split.
- ZeTSA has no per-error-code **store or HQ override** permission today. The decision to add it (store, HQ or both, default HQ only) is in [AUDIT_EVIDENCE_DECISION_LOG.md](AUDIT_EVIDENCE_DECISION_LOG.md), K3. It is not built.
- Whether "POS to GL Variance" as a **required** comparison matches how Oracle treats GL is unconfirmed. ReSA exports to GL after the audit instead of comparing before it.
- Release 15.0.1 is the only guide read in full. A web search of later releases (summaries only) is recorded in [AUDIT_FAILURE_EVIDENCE_SCENARIOS.md](AUDIT_FAILURE_EVIDENCE_SCENARIOS.md) section 9. It confirms the per-code store and HQ override flags exist.

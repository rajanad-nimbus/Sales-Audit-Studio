# General Audit Process: how ZeTSA implements it

Each step of the sales audit process, the part of the app that does it, and who does it.

## Data collection and import
| Step | Where it happens |
|---|---|
| POS and OMS data from each selling location arrives daily | Connectors and feeds (`/imports`, `/diagnostics/feeds`); Shopify sync (`backend/apps/ingest/shopify`, dated by `processed_at`). Canonical transactions retain the POS-assigned business date (including RTLOG's store-day date) even when the event timestamp falls on a different calendar date; generic feeds can supply `business_date`, otherwise the event's local date is used. IT can optionally reconcile each standalone delivery immediately; scheduled batches still reconcile after all configured feeds have loaded. |
| The system creates a store day and reference files | An IT Support Admin or Sales Auditor can open a day (`POST /api/store-days`). After a successful scheduled batch for the prior business day, ZeTSA automatically opens today's day for every active Store in Foundation Data; the operation is idempotent and audited. Its opened, already-open, skipped or failed outcome appears in Imports → Batch runs. Reference data lives in Foundation Data (`/foundation`). |
| Remove a Store Day opened by mistake | IT can remove an unclosed placeholder only while it has no transactions, register events, cases, cash/ACH activity, calculated totals, variance explanations, or closure requests. The Store Day page requires a reason and typed store/date confirmation (`POST /api/store-days/{store}/{date}/delete`). The deletion event remains in the append-only audit stream; any day with business or audit history is retained and the API explains what blocks removal. |
| Unaudited transactions are imported with validation and error generation | Ingest normalises into canonical transactions and raises errors for anomalies |

## Validation and rule-based checks
| Step | Where it happens |
|---|---|
| User-defined rules calculate expected totals, discrepancies are flagged | Total definitions sum or count selected transaction, line, tax, tender, or discount parameters, apply governed realm restrictions, and roll up to Store/Register/Cashier/Tender. Finance can test a saved revision against a selected store and date before reviewing its multi-day impact; variance tolerances and Store Day totals live at (`/store-days/{store}/{date}`), with "Explain…" for variances. |
| Audit rules detect errors or suspicious patterns | Audit error catalogue (`/audit-errors`, one page per error at `/audit-errors/{code}`). IT/Admin can set each defined error's downstream impact there: an unresolved error can hold transactions or store-day totals from selected export destinations while allowing other destinations to receive them. Unconfigured errors keep the all-destination hold. |
| Retailer-defined transaction rules evaluate record attributes or Store Day presence | Settings → Controls → Transaction rules: Finance/Admin create condition worksheets against an allowlist of canonical POS and e-commerce fields, associate an active Sales Audit error code, preview up to 366 days, and submit for independent approval. Each-transaction rules can trigger on match or non-match per record. Store Day absence rules raise once for an open Store Day when no POS/e-commerce record matches the conditions. Active rules run in order during reconciliation; a finding is linked to the configured rule and uses the selected error's override and export-impact workflow. |
| Retailer-defined thresholds are governed before affecting totals | Settings → Controls → Threshold rules: Finance/Admin create and edit Worksheet revisions, Finance submits them, and a different Finance approver or Admin activates them. Rules select a total, Store/Register/Cashier balancing level, signed or absolute comparison, threshold, currency, severity, execution order, optional stop-on-breach behavior, and owner. Register and Cashier rules require a total defined at that same level. Each breached balance is retained on its exception. Activation invalidates calculated totals so store days are recalculated with the approved rule. |

## Exception handling
| Step | Where it happens |
|---|---|
| Failed transactions are flagged for auditor review | Exceptions grouped into Cases (`/cases`, `/actions`) |
| Audit errors may be overridden with independent review | On the case, a Sales Auditor requests an override with a rationale. Finance can approve or reject with a reason; the requester cannot review their own request. Each decision is recorded in the append-only Audit Trail and appears in the transaction's scoped audit history when the transaction is linked to the case. Approval marks the exception overridden and closes its case when all exceptions are resolved or overridden. |
| Auditors correct or re-process, then re-export | Transaction Corrections (`/corrections`). An approved correction marks the day's totals stale (the day must be re-totalled before closure) and marks open cases for review. Use **Refresh audit errors** on the transaction detail page to re-run reconciliation for that store and business date; eligible transactions then use the configured automatic export path. A case whose evidence a correction changed is withheld from export until Finance reviews its amounts (`POST /api/cases/{id}/amount-review`; a closed case needs a follow-up case). Approved transaction corrections are exported through `transaction_corrections`; verified case adjustments use `adjustments`. For a missing transaction, the proposal can include item lines, line tax, discounts and split tenders from the receipt. Finance approval writes the canonical transaction and its detail rows together; any detail-to-header or tender-to-header variance is retained as Unbalanced evidence for audit review. |
| Review transaction errors and revisions | The transaction detail page lists errors in cases raised from that transaction, links each error code to its audit rule, and includes a paginated audit trail of immutable transaction comments, related case decisions and correction lifecycle events. Case errors can cover additional transactions in the same case. |
| Review transaction employee attribution | Transaction details show the source salesperson ID when the POS feed provides it; the transaction list can be filtered by salesperson; approved transaction exports retain the same identifier. |

## Reporting and reconciliation
| Step | Where it happens |
|---|---|
| Cleaned data is exported to financial systems | Exports (`/exports`): destinations, batches, delivery and acknowledgement tracking. Approved transaction corrections are available in the Transaction Corrections dataset with reviewer, rationale and back-post tracking. |
| Daily totals are matched against the general ledger and control reports | Every re-total writes **POS to GL Variance** (POS net vs the ERP net) and **Processor to Bank Variance**. POS-to-GL and POS-control declared variances block store-day close when unresolved; processor-to-bank settlement can arrive later and is retained for follow-up without blocking close. Finance and IT can open the prefilled General Ledger View directly from the POS-to-GL total to drill into that store day's journal lines. Over/short and POS control declared totals are balanced the same way. |
| Review outstanding voucher liability | Cash Office lists voucher tender codes, escheat eligibility dates and destinations. IT/Admin configures tender aging and optional Store → Issuer → Recipient → HQ jurisdiction routing under Settings → Integrations & readiness → Voucher policy. Finance reviews eligible vouchers shown, inspects the decision path, adds an optional note, and confirms escheatment. Eligibility and routing are rechecked at confirmation; stale destination previews reject the whole batch. Audit events retain the applied window and date; the voucher and audit event retain the final destination and evidence. Configured windows also cover non-expiring vouchers; tenders without a policy retain expiry-based eligibility. Outstanding voucher jurisdiction corrections are audited and store scoped; completed routing history is preserved. |

## Ongoing monitoring

Voucher retention runs use per-tender purge windows and process only settled vouchers. IT previews and archives a bounded batch, then explicitly confirms any purge. Cash Office retains the permanent identities of purged vouchers under **Retired**, including their final status and archive reference; voucher numbers remain reserved. Outstanding liabilities are excluded, and archive failures prevent deletion. Run history appears in Data retention and the Audit Trail.

| Step | Where it happens |
|---|---|
| KPIs: transaction accuracy, exception rate, timeliness | Command Center, "Ongoing monitoring, last 30 days" (`GET /api/command-center`, `kpis`). **Exception rate** is exceptions raised per 1,000 transactions. **Transaction accuracy** is the share of transactions not flagged "Exception" by validation. **Timeliness** is the share of store days, from those due, that were closed within the close target of their business date. The window (`NIMBUS_KPI_WINDOW_DAYS`, default 30) and the target (`NIMBUS_CLOSE_TARGET_DAYS`, default 3) are settings |
| Findings feed process changes | Error patterns (`/audit-errors`) and the Audit Trail (`/audit`) |

## Roles
| Role in the process | In ZeTSA |
|---|---|
| Store / Location Sales Staff: record transactions, transmit them | They record sales in the POS or Shopify; that feed is what ZeTSA ingests. **Store Sales Staff** (`store_staff`) is a store-scoped, read-only login to follow Store Days & Totals, so they can see whether their store's data arrived on time |
| Store Audit / Operations Staff: review local files, resolve immediate discrepancies, confirm the data is complete | **Store Manager** (`store_manager`), limited to their own stores: reads their store's Transactions before audit, follows Store Days & Totals (data completeness, errors, cases) and works Cash Office |
| Sales Audit Analysts: run validation, investigate exceptions, adjust | **Sales Auditor** (`analyst`) |
| Financial Accountants: reconcile to the ledger, ensure compliance | **Finance Approver** (`finance`) |
| IT / Data Management: POS/OMS integrations, secure transfer, configuration | **IT Support Admin** (`it`) |
| Management / Compliance: oversee, approve corrective actions, enforce controls | **Administrator** (`admin`) approves; **Internal Auditor** (`auditor`) has read-only oversight |

Screen access per role is defined in `backend/apps/security/access.py` and enforced by the API.

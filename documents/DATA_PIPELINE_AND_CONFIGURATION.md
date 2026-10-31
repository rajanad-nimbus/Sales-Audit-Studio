# Data Pipeline and Configuration

How data gets into Nimbus and becomes cases, and where each part of that behaviour is configured.

- **Data Pipeline** (`/pipeline`) is the operational view: is my data current, and what happened to it?
- **Configuration** (`/settings/*`) is where you change how Nimbus behaves.

Sources: `backend/apps/`, `backend/nimbus.py`, `backend/access.py`, `backend/main.py`, `frontend/app/pipeline/page.tsx`, `frontend/app/settings/`.

## Contents

1. [Data Pipeline](#part-1-data-pipeline)
2. [Configuration](#part-2-configuration)
3. [Environment variables](#environment-variables)
4. [API quick reference](#api-quick-reference)
5. [Common tasks](#common-tasks)
6. [Troubleshooting](#troubleshooting)
7. [Notes and limits](#notes-and-limits)

---

## Part 1: Data Pipeline

### End-to-end flow

```
Source feeds ──► Ingest & archive ──► Validate ──► Normalize ──► Reconcile ──► Cases ──► Investigate (optional)
 POS, Processor,   immutable           bad rows      canonical     match,        priority    LLM or rules,
 Bank, ERP,        SourceRecord        go to         transaction   tolerate,     and SLA     read-only
 Shopify, Control                      quarantine                  flag breaks
```

Transactions arrive **once a day**. The four counters at the top of the Pipeline page follow this flow:

| Stage          | Meaning                                                      |
| -------------- | ------------------------------------------------------------ |
| Source records | Records received from configured or simulated source systems |
| Normalized     | Archived records turned into canonical transactions          |
| Matched        | Reconciled with no exception                                 |
| Exceptions     | Records that became cases (links to Cases)                   |

### 1. Sources and input contract

Accepted `source_system` values and their record types (`VALID_TYPES` in `ingest.py`):

| Source     | Record types                              | Lineage tag        | Amount sign         |
| ---------- | ----------------------------------------- | ------------------ | ------------------- |
| POS        | Sale, Return                              | `pos-map-v1`       | Sale +, Return −    |
| Processor  | Capture, Refund                           | `processor-map-v1` | Capture +, Refund − |
| Bank       | Deposit                                   | `bank-map-v1`      | +                   |
| ERP        | Posting                                   | `erp-map-v1`       | +                   |
| Shopify    | Order, Refund                             | `shopify-map-v1`   | Order +, Refund −   |
| POSControl | Control manifest (no transaction records) | none               | n/a                 |

Source amounts are always sent **positive**. Nimbus applies the sign from the record type, so `signed_amount` is negative for returns and refunds.

**Transaction columns**

| Column                                     | Required        | Notes                                                                                |
| ------------------------------------------ | --------------- | ------------------------------------------------------------------------------------ |
| `source_record_id`                         | Yes             | Unique per source. Drives duplicate detection                                        |
| `event_time`                               | Yes             | ISO-8601. A missing timezone is treated as UTC. The business date is taken from this |
| `record_type`                              | Yes             | Must be valid for the source (table above)                                           |
| `amount`                                   | Yes             | Positive decimal, rounded to 2 places                                                |
| `store_id`                                 | Yes             | Canonical store ID                                                                   |
| `currency`                                 | No              | Defaults to`USD`                                                                     |
| `payment_reference`                        | Needed to match | The key that joins POS to Processor and Shopify refunds to orders                    |
| `register_id`, `tender_type`, `channel_id` | No              | `channel_id` defaults to `store`                                                     |
| `settled_on`                               | No              | Settlement date. Defaults to the business date                                       |
| `settlement_reference`                     | No              | Groups processor and bank records into a settlement batch                            |

**POS control columns:** `source_record_id`, `business_date`, `store_id`, `declared_count`, `declared_total`. A manifest declares how many POS transactions and what total a store reported for the day.

**Transaction detail.** POS records can also carry lines, taxes, discounts and tenders. Nimbus stores them and records `detail_status`, which says whether the detail adds up to the transaction amount. Returns can carry `original_reference` and `return_reason`, and are linked back to the sale they reverse once that sale has arrived.

Other rules: unique `delivery_id`, canonical store IDs. The published contract is `GET /api/ingest/contracts`.

### 2. Ingest and archive

`ingest.ingest()` handles each record in a delivery:

1. Compute a SHA-256 hash of the payload. Shopify uses an identity payload, so volatile fields don't count as changes.
2. Look up earlier hashes for the same source and `source_record_id`.
3. **Same hash already seen:** counted as a duplicate and skipped. Re-delivering a file is safe.
4. **Same id, different hash:** quarantined with the reason "Conflicting resubmission: same record id, different payload".
5. **New id:** parsed and validated. A parse failure quarantines the record with the reason.
6. Valid records are archived as an immutable `SourceRecord`, then normalized into a `CanonicalTransaction` with status `Unmatched / Open`. The source record becomes `Processed`.
7. After the loop, returns are linked to their original sales.

Each delivery returns: `received`, `archived`, `duplicates_skipped`, `quarantined`, `canonical_created`, `returns_linked`.

**Source record statuses:** `Archived` (valid, waiting to be normalized or, for manifests, to be used by controls), `Processed`, `Quarantined`.

**Validation failures that cause quarantine**

| Check                                        | Reason shown                    |
| -------------------------------------------- | ------------------------------- |
| Unknown source system                        | `unknown source_system …`       |
| Missing required fields                      | `missing fields: …`             |
| `record_type` not valid for that source      | `invalid record_type … for …`   |
| Amount not a number                          | `amount is not a decimal`       |
| Amount zero or negative                      | `amount must be positive`       |
| Bad timestamp                                | `event_time is not ISO-8601`    |
| Line, tax, discount or tender detail invalid | `invalid transaction detail: …` |
| Bad manifest values                          | `invalid manifest values`       |

**Quarantine** holds records instead of loading them. Nothing is deleted. Fix the source and re-deliver under a new record id, or re-deliver once the source is corrected. Quarantined records appear in the Quarantine tab, in the delivery table's Quarantined column, and as a chip in the health banner.

**Ways data can arrive**

| Method          | Endpoint                                | Role                  | Notes                                                          |
| --------------- | --------------------------------------- | --------------------- | -------------------------------------------------------------- |
| JSON records    | `POST /api/ingest`                      | IT                    | `source_system`, `delivery_id`, `records`                      |
| CSV             | `POST /api/ingest/csv`                  | IT                    | Max 50,000 rows. Optional`profile_id` applies a column mapping |
| Nightly batch   | `POST /api/batch/run` or `batch_job.py` | IT                    | Loads feeds and reconciles                                     |
| Shopify sync    | `POST /api/ingest/shopify/sync`         | Finance, IT           | Date-range pull from the Shopify API                           |
| Shopify webhook | `POST /api/ingest/shopify/webhook`      | Shopify (HMAC-signed) | Real-time orders and refunds                                   |
| Synthetic day   | `POST /api/ingest/simulate`             | IT                    | Demo only                                                      |

### 3. Reconcile

`ingest.reconcile()` works on `Unmatched` transactions, for one business date or all. It does five things in order.

**a) POS to Processor matching**

- Duplicate sales (same store, register, amount and payment reference) are flagged `DUPLICATE_SALES`.
- Each remaining POS record is looked up by store, currency, payment reference and expected type (Capture for a sale, Refund for a return).
- **Part-paid sales:** only the card portion is expected at the processor. A sale paid entirely by voucher or cash has no processor leg and is closed as matched.
- Exactly one candidate must exist. Zero or several becomes `UNMATCHED_SALE` or `MISSING_REFUND`.
- The matched amount must be within the **amount tolerance**, otherwise `AMOUNT_MISMATCH`.
- The settlement date must be within the **settlement-day tolerance** of the business date, otherwise `TIMING_DIFFERENCE`.
- Processor records nobody claimed become `ORPHAN_PAYMENT`.

**b) Shopify.** Refunds are matched to their order by store, currency and order id, and the order may belong to an earlier date. Refunded more than the order gives `SHOPIFY_OVERAGE_REFUND`, less gives `SHOPIFY_PARTIAL_REFUND`, and no order found gives `SHOPIFY_UNMATCHED_REFUND`. A refund equal to the order matches. Paid orders with no refund are closed.

**c) Completeness and balancing controls.** Each POS control manifest is compared with the POS transactions actually ingested for that store and day. Fewer transactions than declared gives `INCOMPLETE_FEED`. A different count or total gives `BALANCING_VARIANCE`.

**d) Settlement controls.**

- Bank deposits with a `settlement_reference` are compared batch by batch with matched processor records. A zero deposit gives `MISSING_DEPOSIT`, any other difference beyond tolerance gives `BANK_VARIANCE`.
- Bank deposits without a reference are compared per store per day against matched processor totals.
- ERP postings are compared per store per day against matched POS totals, giving `GL_VARIANCE` or `MISSING_GL_POSTING`.

**e) Case creation.** Findings are grouped by exception type, store and business date. Each group becomes one case with one exception record.

| Case amount | Priority | SLA      |
| ----------- | -------- | -------- |
| Over 3,000  | Critical | 4 hours  |
| Over 1,000  | High     | 24 hours |
| Otherwise   | Normal   | 72 hours |

Exposure is the amount, except for exception types the ontology defines as zero-exposure. Severity is High above 1,000, otherwise Medium. Case numbers look like `ZA-YYYYMMDD-XXXXXXXXXXXX` with a 12-character uppercase hexadecimal suffix. Every case writes a `CASE_CREATED` audit event by `reconciliation-engine`, with the transaction ids involved.

Matched records are set to `Matched / Closed`. Records in a break are set to `Exception / Awaiting`.

**All break types**

| Type                       | Meaning                                                   |
| -------------------------- | --------------------------------------------------------- |
| `UNMATCHED_SALE`           | POS sale with no single processor capture                 |
| `MISSING_REFUND`           | POS return with no single processor refund                |
| `DUPLICATE_SALES`          | Same sale appears twice at a register                     |
| `AMOUNT_MISMATCH`          | POS and processor amounts differ beyond tolerance         |
| `TIMING_DIFFERENCE`        | Settled later than the settlement-day tolerance           |
| `ORPHAN_PAYMENT`           | Processor record with no POS record                       |
| `SHOPIFY_UNMATCHED_REFUND` | Refund with no order                                      |
| `SHOPIFY_OVERAGE_REFUND`   | Refunds exceed the order                                  |
| `SHOPIFY_PARTIAL_REFUND`   | Refunds are less than the order                           |
| `INCOMPLETE_FEED`          | Fewer POS transactions than the control manifest declared |
| `BALANCING_VARIANCE`       | POS count or total differs from the manifest              |
| `MISSING_DEPOSIT`          | Expected settlement has no bank deposit                   |
| `BANK_VARIANCE`            | Bank deposit differs from expected settlement             |
| `GL_VARIANCE`              | ERP posting differs from matched POS total                |
| `MISSING_GL_POSTING`       | No ERP posting for a store and day that has matched sales |

### 4. Nightly batch

`run_daily_batch()` in `batch.py` chains load, reconcile and optional investigation.

1. **Guard:** if the date already has a `Succeeded` run, it returns HTTP 409 unless `rerun=true`.
2. **Record:** creates a `BatchRun` with status `Running` and commits, so a crash stays visible.
3. **Load:** uses the feeds supplied, or the simulated feed. If `SHOPIFY_STORE_URL` is set, the Shopify feed for that day is added.
4. **Ingest** each source under a delivery id `BATCH-<date>-<run id prefix>`.
5. **Reconcile** that business date.
6. **Record the outcome:** transactions loaded, matched pairs, cases created, per-source counts. Status becomes `Succeeded` and `BATCH_COMPLETED` is audited.
7. **Investigate:** if `auto_investigate` is on, the Investigation Agent runs (see below).
8. **On any error:** rolls back, marks the run `Failed` with the error (first 500 characters), and audits `BATCH_FAILED`. A Shopify fetch failure fails the batch on purpose, so a configured connector is never skipped silently.

**Triggering**

| Way       | Command                                                                                                                                               |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI        | "Run nightly batch" on the Pipeline page (IT)                                                                                                         |
| API       | `POST /api/batch/run` with optional `business_date`, `rerun`, `feeds`                                                                                 |
| Scheduler | `PYTHONPATH=backend python -m apps.operations.batch_job --date YYYY-MM-DD [--rerun]`. It exits 0 on success and 1 on failure, so cron can alert on it |

If no date is given, the API uses the day before the earliest loaded business date (or yesterday if nothing is loaded). `apps.operations.batch_job` defaults to yesterday in UTC.

**Auto-investigation.** After a successful batch, open cases for that date with a pending investigation are taken highest exposure first. The LLM agent handles up to `NIMBUS_AGENT_BATCH_LIMIT` of them (default 25) so an unattended run has bounded time and cost. The rest get the deterministic investigation and can be run on demand. A failure on one case never fails the batch.

### 5. Freshness and health

`GET /api/batch/status` drives the banner on every page. It is open to any signed-in user.

| State             | Condition                                                         |
| ----------------- | ----------------------------------------------------------------- |
| Never             | No successful batch yet                                           |
| Fresh             | Last success within 26 hours                                      |
| Stale             | Last success more than 26 hours ago                               |
| Last batch failed | The most recent run failed. Shown in red regardless of the others |

**Per-feed freshness** (`GET /api/ingest/feed-health`) compares the newest archived or processed delivery for the profile's source with the schedule text.

| Schedule text               | Expected within                     |
| --------------------------- | ----------------------------------- |
| Contains "daily"            | 26 hours                            |
| Contains "weekly" or "week" | 8 days                              |
| Anything else or empty      | No window, so the state is`unknown` |

| Feed state | Meaning                              |
| ---------- | ------------------------------------ |
| `fresh`    | Received within its window           |
| `overdue`  | Older than its window                |
| `never`    | Nothing received yet                 |
| `unknown`  | Profile has no recognizable schedule |
| `disabled` | Profile is switched off              |

### 6. Pipeline page

| Area                 | Shows                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Health banner        | One-line answer to "is my data OK?", with chips for overdue feeds and quarantined records                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Stage counters       | Source records, normalized, matched, exceptions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Sources** tab      | One row per source (POS, POSControl, Processor, Bank, ERP, Shopify) with three switches: _Accept data_ (master switch), _Nightly batch_ (the scheduled batch pulls it) and _Manual sync / upload_ (a person may sync or upload it). Settings are stored in the database, change straight away and are audited. Shopify also has a **Sync now** button here. Only IT can change them. A source with no saved setting behaves as before: on, in the batch (Shopify only when its connector is configured), manual allowed. While Shopify is off, its webhook is acknowledged and ignored. Shopify also has **Sync now** (an ad-hoc pull for the last 24 hours, 7 days, 30 days or any dates you choose, up to 90 days) and **Run on a schedule** (every day, or every week on a chosen day, at a time of day in your time zone). The application checks every minute and pulls recent orders and refunds when a run is due, showing the next run and the last result. A schedule counts from when it is saved and never backfills moments already past. Each run covers a window of two periods (at least a day, at most a week), and already-loaded orders are skipped. Set `NIMBUS_SCHEDULER=0` to turn the in-app scheduler off, for example when an external cron does the job |
| **Batch runs** tab   | Last 10 runs: business date, status, started, loaded, matched, cases, trigger, error                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Source feeds** tab | Freshness per profile, and recent deliveries with records, processed, quarantined, latest error                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Quarantine** tab   | Up to 10 most recent held records with source, record id and reason                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Demo tools** tab   | Synthetic ingest and reconcile step by step, and clearing of sample data. Testing only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

Access: Analyst, IT and Auditor can open the page. Running ingest, batch or simulate needs IT. Running reconcile needs IT or Finance.

---

## Part 2: Configuration

Everything that sets how Nimbus behaves lives under `/settings`. The left menu shows only sections the signed-in role may open. Administrators can open all of them.

| Section                    | Path                     | Controls                                                      | Roles                         |
| -------------------------- | ------------------------ | ------------------------------------------------------------- | ----------------------------- |
| General                    | `/settings`              | Theme and account                                             | Everyone                      |
| Integrations & readiness   | `/settings/integrations` | Source feeds, Shopify, Ontology policy, tolerance, readiness  | Analyst, Finance, IT, Auditor |
| Totals, rules & GL mapping | `/settings/controls`     | Totals and audit rules (read-only, from Ontology), GL mapping | Analyst, Finance, Auditor     |
| Data retention             | `/settings/retention`    | How long each dataset is kept                                 | IT, Auditor                   |
| Agents                     | `/settings/agents`       | LLM investigator and per-agent permissions                    | Analyst, Finance, IT, Auditor |
| Export destinations        | `/settings/destinations` | Where exports are delivered                                   | Finance, IT, Auditor          |
| Roles & access             | `/settings/access`       | Which role opens which screen                                 | Everyone                      |

Seeing a section does not mean you can change it. Each write endpoint has its own role check, listed below.

### General

- **Appearance:** Light, Dark or System. Saved in this browser only.
- **Account:** the signed-in user and role. Authentication and roles come from Ontology Studio.

### Integrations & readiness

This page has five tabs: source feeds and integrations, reconciliation tolerance, and production readiness (plus appearance and account on the General page).

**Source-feed profiles**

A profile is a read-only description of one expected feed.

| Field          | Purpose                                                              |
| -------------- | -------------------------------------------------------------------- |
| Name           | Unique. Creating a duplicate returns 409                             |
| Source system  | One of POS, POSControl, Processor, Bank, ERP, Shopify                |
| Schedule       | Free text such as`daily` or `weekly`. Drives freshness checks        |
| Column mapping | JSON of`{canonical column: source column}`. Used for CSV deliveries  |
| Enabled        | Disabled profiles show as`disabled` and can't be used for CSV ingest |

Example mapping for a file with different column names:

```json
{
  "source_record_id": "TxnId",
  "event_time": "Timestamp",
  "record_type": "Type",
  "amount": "Amt",
  "store_id": "Location",
  "payment_reference": "AuthCode"
}
```

When a profile is used, each CSV row is rebuilt from the mapping, so columns not listed are dropped. The profile's source system must match the delivery's. IT creates and enables or disables profiles. Finance and IT can view them.

**Shopify integration**

Syncs orders and refunds for reconciliation.

- **Status:** `Healthy`, `Stale` (no sync in over 26 hours) or `No syncs yet`, plus last sync time and quarantined records.
- **Sync now:** pulls the last 24 hours.
- **Webhook:** `POST /api/ingest/shopify/webhook` verifies an HMAC-SHA256 signature using `SHOPIFY_WEBHOOK_SECRET`, then ingests through the same pipeline. Duplicates are skipped by payload hash.
- **Setup** needs the environment variables in the table below. Full guide: `backend/SHOPIFY_INTEGRATION.md`.

**Ontology policy integration**

Nimbus reads approved Sales Audit rules from Ontology Studio. It never edits or promotes Ontology content.

- Shows whether the connection is configured, the policy source, and approved rules loaded against expected.
- **Refresh approved policy** re-reads the rules.
- Agent permissions and financial gates stay enforced by Nimbus code, whatever Ontology says.

**Reconciliation tolerance** (Finance can edit)

A local fallback, used until Nimbus receives approved tolerance bands from Ontology.

| Setting                  | Valid range  | Effect                                                                                                         |
| ------------------------ | ------------ | -------------------------------------------------------------------------------------------------------------- |
| Amount tolerance         | 0 or more    | Largest POS-versus-processor difference still treated as a match. Also applied to bank and ERP variance checks |
| Settlement-day tolerance | 0 to 31 days | How many days later than the business date a settlement may land before it is a`TIMING_DIFFERENCE`             |

With no policy saved, both are 0, meaning exact match and same-day settlement. The saved policy records who changed it and when. Changes take effect on the next reconcile and do not re-evaluate records already matched.

**Production readiness**

| Check                         | Passes when                                                                                    |
| ----------------------------- | ---------------------------------------------------------------------------------------------- |
| Ontology user authentication  | Authentication is enabled                                                                      |
| Approved Sales Audit ontology | Rules are approved and released and an integration key is configured                           |
| Required source feeds         | A delivery has been received from every source: POS, POSControl, Processor, Bank, ERP, Shopify |

It also lists the latest delivery per required feed, and the inputs still needed: source schemas and read-only endpoints, matching keys and control totals, cutoff calendar and tolerance policy, approval matrix and escalation owners.

### Foundation Data (Data & Control)

`/foundation`, for Finance and IT. Tabs: Stores, Products, Calendar, Tenders, Registers & cashiers, Accounts & tax. Each entry has a code, name, active flag and free-form attributes. **Stores** (the shop and its locations) and **Products** (one entry per variant, keyed by SKU) are read-only and synced from Shopify with **Sync from Shopify** (`POST /api/ingest/shopify/sync-reference`). Entries added by hand are never overwritten, and items removed from Shopify are marked inactive. The other tabs are maintained by hand.

### Totals, rules & GL mapping

Two tabs, both feeding the audit controls. Totals and rules are defined and approved in Ontology Studio and shown read-only.

| Tab                | What it holds                                                                                                               |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| **Totals & rules** | Total definitions (name, aggregation, source system, transaction types, tender, level), with a preview, and the audit rules |
| **GL mapping**     | General-ledger cross-references and a journal preview                                                                       |

### Data retention

How long each dataset is kept. Policies are previewed safely, and purging is a separate supervised step.

| Dataset                  | Table                                         |
| ------------------------ | --------------------------------------------- |
| `source_records`         | Archived source deliveries (by received date) |
| `canonical_transactions` | Normalized transactions (by created date)     |
| `audit_events`           | Audit trail (by created date)                 |

| Setting       | Rule                                                                                  |
| ------------- | ------------------------------------------------------------------------------------- |
| Retain (days) | Minimum 30. Saved when you leave the field                                            |
| Archive first | Whether eligible records are written to an archive file before any purge. Default yes |

**Actions**

| Action           | What happens                                                                                                                                             | Role |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| Preview          | Counts records older than the cutoff. Changes nothing                                                                                                    | IT   |
| Archive eligible | Writes up to 5,000 eligible records to a`.jsonl` file under `NIMBUS_ARCHIVE_DIR/<dataset>/`, with a SHA-256 hash, and records a run. **Does not delete** | IT   |
| Purge            | Only through the API with`confirm_purge: true`. The UI never purges                                                                                      | IT   |

The History table lists each run with eligible, archived and purged counts. Setting a policy needs IT. Viewing needs IT or Finance.

### Agents

Runtime settings, changeable by **admin only**. Environment variables are the defaults, and stored values override them. Each change is written to the audit trail as `AGENT_SETTING_CHANGED` with the before and after values.

| Setting                      | Default             | Environment variable                           | Allowed values                                                                          |
| ---------------------------- | ------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------- |
| Investigator enabled         | On                  | `NIMBUS_AGENT_INVESTIGATOR` (`0` turns it off) | true or false                                                                           |
| Auto-investigate after batch | On                  | `NIMBUS_AUTO_INVESTIGATE` (`1` is on)          | true or false                                                                           |
| Model                        | `claude-sonnet-5-5` | `NIMBUS_LLM_MODEL`                             | `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1`, `claude-haiku-4-5-20251001` |
| Tools                        | All                 | none                                           | Non-empty subset of the investigator's tools                                            |

**Fixed limits for each investigation** (code, not editable in the UI)

| Limit             | Value                                     |
| ----------------- | ----------------------------------------- |
| Max turns         | 6                                         |
| Max output tokens | 1,200                                     |
| Timeout           | 90 seconds                                |
| Spend cap         | `NIMBUS_AGENT_MAX_COST_USD`, default 0.50 |
| Cases per batch   | `NIMBUS_AGENT_BATCH_LIMIT`, default 25    |

**Autonomy limits:** `policy-v1.0` sets an autonomy limit of 1,000 and 250 for timing differences. These, policy thresholds and roles are **not** editable from the UI. This is deliberate, so a setting can't widen what an agent may do.

**What the page shows:** each agent's kind (Rules or LLM), role, what it can and cannot do, live event counts from the audit trail (total and last 24 hours), the ontology and policy versions, token usage, and failed runs from recent `AGENT_RUN` events. Without an LLM key or with the investigator off, the page says so, and rules check evidence completeness instead.

### Export destinations

Where approved results are sent downstream (for example to an ERP or GL).

| Destination kind | Formats     | Notes                                                         |
| ---------------- | ----------- | ------------------------------------------------------------- |
| `webhook`        | JSON or CSV | Needs an`http(s)` URL. An optional secret signs each delivery |
| `file`           | JSON or CSV | Written to`NIMBUS_EXPORT_DIR`                                 |

Two defaults are created on first use: a test webhook receiver and an "ERP/GL file drop".

| Dataset       | Content                                                                        |
| ------------- | ------------------------------------------------------------------------------ |
| `resolutions` | Closed, validated cases with decision, disposition and validation result       |
| `adjustments` | Approved financial adjustments from validated cases (journal lines for ERP/GL) |
| `audit`       | Audit events since the last delivery, with hashes                              |

Exports are incremental and idempotent, with signed delivery and acknowledgment tracking. **Scope** can be `all`, `current`, or `backposts`. A back-post is a record for a business day that has already ended or was already exported to that destination. A dry run previews a batch without sending. Creating, enabling or disabling destinations, and running exports, need IT. Names must be unique, and each create and toggle is audited.

### Roles & access

Screens and the API paths each one needs are defined in `backend/access.py`. A role can open a screen only if it is listed, and the API enforces the same map. This page shows the matrix.

| Role            | Label                               |
| --------------- | ----------------------------------- |
| `admin`         | Administrator (can open everything) |
| `finance`       | Finance Approver                    |
| `it`            | IT Support Admin                    |
| `analyst`       | Sales Auditor                       |
| `auditor`       | Internal Auditor                    |
| `store_manager` | Store Manager                       |
| `viewer`        | Viewer                              |

`/api/me`, `/api/batch/status` and `/api/access/matrix` are open to every signed-in user because every page needs them for the freshness banner and identity. Roles come from Ontology Studio and can be remapped with `NIMBUS_ONTOLOGY_ROLE_MAP`.

### Who can change what

| Change                                    | Role needed                |
| ----------------------------------------- | -------------------------- |
| Run batch, ingest, simulate               | IT                         |
| Run reconcile                             | IT or Finance              |
| Create or toggle source-feed profile      | IT                         |
| Reconciliation tolerance                  | Finance                    |
| Retention policy, preview, archive, purge | IT                         |
| Export destinations and export runs       | IT                         |
| Agent settings                            | Admin                      |
| Autonomy limits, policy thresholds, roles | Code or config change only |

Administrators pass any check that lists a role.

---

## Environment variables

| Variable                                         | Used for                                                                             | Default                         |
| ------------------------------------------------ | ------------------------------------------------------------------------------------ | ------------------------------- |
| `NIMBUS_ONTOLOGY_AUTH_URL` / `ONTOLOGY_AUTH_URL` | Authentication through Ontology Studio                                               | none                            |
| `NIMBUS_ONTOLOGY_ROLE_MAP`                       | Remap Ontology roles to Nimbus roles                                                 | none                            |
| `NIMBUS_ONTOLOGY_STORE_CLAIM`                    | Claim that scopes a user to stores                                                   | none                            |
| `NIMBUS_AGENT_INVESTIGATOR`                      | `0` turns the LLM investigator off                                                   | on                              |
| `NIMBUS_AUTO_INVESTIGATE`                        | `1` investigates new cases after each batch                                          | on                              |
| `NIMBUS_LLM_MODEL`                               | Investigator model                                                                   | `claude-sonnet-5-5`             |
| `NIMBUS_CHAT_MODEL`                              | Chat model                                                                           | falls back to`NIMBUS_LLM_MODEL` |
| `NIMBUS_AGENT_BATCH_LIMIT`                       | Cases the LLM handles per batch                                                      | 25                              |
| `NIMBUS_AGENT_MAX_COST_USD`                      | Spend cap per investigation                                                          | 0.50                            |
| `NIMBUS_ARCHIVE_DIR`                             | Retention archive location                                                           | `/app/archives`                 |
| `NIMBUS_EXPORT_DIR`                              | File-drop export location                                                            | set in`exports.py`              |
| `NIMBUS_ENABLE_TEST_SINK`                        | Enables the built-in test receiver in development/test only                          | true in local Compose           |
| `NIMBUS_SINK_SECRET`                             | Signing secret for the development/test webhook receiver                             | `demo-secret` in local Compose  |
| `SHOPIFY_STORE_URL`                              | Enables Shopify in the batch and sync                                                | none                            |
| `SHOPIFY_ACCESS_TOKEN`                           | Shopify Admin API token (preferred). Or`SHOPIFY_API_KEY` plus `SHOPIFY_API_PASSWORD` | none                            |
| `SHOPIFY_STORE_ID`                               | `store_id` stamped on Shopify records                                                | `SHOPIFY-MAIN`                  |
| `SHOPIFY_API_VERSION`                            | Shopify API version                                                                  | `2024-01`                       |
| `SHOPIFY_WEBHOOK_SECRET`                         | Verifies webhook signatures                                                          | none                            |
| `SHOPIFY_FETCH_LIMIT`                            | Page size for Shopify fetches                                                        | 250                             |
| `SHOPIFY_SYNC_LOOKBACK_DAYS`                     | Lookback for syncs                                                                   | 3                               |

Do not commit real credentials. Keep them in `.env`.

---

## API quick reference

| Method and path                                          | Purpose                        | Role                               |
| -------------------------------------------------------- | ------------------------------ | ---------------------------------- |
| `GET /api/batch/status`                                  | Freshness for the banner       | Any signed-in                      |
| `GET /api/batch/runs`                                    | Recent runs                    | Finance, IT                        |
| `POST /api/batch/run`                                    | Run a batch                    | IT                                 |
| `GET /api/ingest/stats`                                  | Counters and recent quarantine |                                    |
| `GET /api/ingest/deliveries`                             | Deliveries grouped by id       | IT, Finance                        |
| `GET /api/ingest/feed-health`                            | Per-feed freshness             | IT, Finance                        |
| `GET /api/ingest/contracts`                              | Published input contract       | IT, Finance                        |
| `GET/POST /api/ingest/profiles`, `PATCH …/{id}`          | Source-feed profiles           | View: IT, Finance. Change: IT      |
| `POST /api/ingest`, `/api/ingest/csv`                    | Load records                   | IT                                 |
| `POST /api/reconcile`                                    | Reconcile now                  | IT, Finance                        |
| `GET/PUT /api/reconciliation/policy`                     | Tolerances                     | View: Finance, IT. Change: Finance |
| `GET /api/production-readiness`                          | Cutover checklist              |                                    |
| `GET /api/ontology/integration/status`, `POST …/refresh` | Ontology policy                |                                    |
| `GET/PUT /api/operations/retention…`                     | Retention                      | IT                                 |
| `GET/POST /api/exports/destinations`                     | Export destinations            | View: Finance, IT. Change: IT      |
| `GET /api/agent/config`, `PUT /api/agent/settings`       | Agents                         | View: Finance, IT. Change: Admin   |
| `GET/POST /api/foundation`                               | Reference data                 | Finance, IT                        |

---

## Common tasks

| I want to…                                           | Do this                                                                                                            |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Check whether my data is current                     | Open Data Pipeline and read the health banner                                                                      |
| Load a day's data                                    | Run nightly batch (IT), or schedule `PYTHONPATH=backend python -m apps.operations.batch_job --date YYYY-MM-DD`     |
| Re-run a day that already succeeded                  | `POST /api/batch/run` with `rerun=true`, or `apps.operations.batch_job --rerun`                                    |
| Find out why a record didn't load                    | Pipeline → Quarantine tab                                                                                          |
| Add a feed with different column names               | Settings → Integrations → Add source-feed profile with a column mapping (IT), then ingest CSV with its`profile_id` |
| Fix a feed showing "overdue"                         | Check the source delivery. Confirm the schedule text contains`daily` or `weekly`                                   |
| Ignore small rounding or late-settlement differences | Settings → Integrations → Reconciliation tolerance (Finance)                                                       |
| Turn off the LLM investigator                        | Settings → Agents (admin), or set`NIMBUS_AGENT_INVESTIGATOR=0`                                                     |
| See how much data would be removed                   | Settings → Retention → Preview                                                                                     |
| Send results to the ERP                              | Settings → Export destinations, then run an export (IT)                                                            |

## Troubleshooting

| Symptom                                                   | Likely cause and fix                                                                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Batch returns 409                                         | That date already succeeded. Use`rerun=true`                                                                        |
| Banner says "Data is stale"                               | No successful batch in 26 hours. Check Batch runs for a failure or a missed schedule                                |
| Batch fails and Shopify is configured                     | A Shopify fetch error fails the whole batch by design. Check credentials and API version, then rerun                |
| Feed shows`unknown`                                       | The profile's schedule has no`daily` or `weekly`. Edit the schedule                                                 |
| Feed shows`never`                                         | No delivery received for that source system yet                                                                     |
| Many`UNMATCHED_SALE` cases                                | Check that`payment_reference` is populated in both POS and processor feeds, and that store ids and currencies agree |
| Many`TIMING_DIFFERENCE` cases                             | The processor settles later than the tolerance. Raise the settlement-day tolerance                                  |
| Rounding differences become`AMOUNT_MISMATCH`              | Raise the amount tolerance (default is zero)                                                                        |
| `INCOMPLETE_FEED` on a store                              | The POS file had fewer transactions than the control manifest declared. Re-send the missing records                 |
| Records stuck as`Quarantined`                             | Read the reason in the Quarantine tab. A conflicting resubmission needs a new record id                             |
| Can't see a settings section                              | Your role isn't listed for it. See Roles & access                                                                   |
| Retention Preview says "Configure retention policy first" | No policy for that dataset yet. Set retention days                                                                  |
| Agents page shows "LLM agent off"                         | Investigator disabled or no LLM credentials. Rules check evidence instead                                           |

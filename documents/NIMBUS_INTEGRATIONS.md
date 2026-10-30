# Nimbus Integration Guide

## Integration Overview

Nimbus acts as an orchestration layer coordinating evidence from multiple systems. It does not replace existing systems but reads from them, coordinates their outputs, and produces auditable resolution workflows.

### First Production Integration Scope

**Phase 1 — Minimum Viable Integration:**
1. One POS system with control feed
2. One payment processor
3. One bank feed
4. Existing ontology platform
5. One downstream destination (ERP/GL)

**Expand after end-to-end correctness is demonstrated:**
- Additional sales channels
- Additional financial scenarios
- Additional downstream destinations
- Multi-tenant governance

---

## Business Source Integrations

### 1. POS / Sales Channels

**Data to Provide:**
- Transaction headers (ID, timestamp, register, terminal, operator)
- Transaction lines (item, quantity, price, tax, discount amount)
- Tender records (payment method, amount, authorization)
- Void/cancellation records (original transaction, reason)
- Return records (original sale reference, returned items, refund amount)

**Control Data:**
- Session/register opening/closing markers
- Daily totals declared by register
- Sequence/batch markers for completeness checking
- End-of-day markers certifying data completeness
- Reconciliation declarations

**Required Identifiers:**
- Store ID (canonical across all systems)
- Terminal/register ID (within store)
- Transaction ID (unique within business day/store)
- Operator/cashier ID (for audit trail)
- Customer reference (masked if PII)

**Timestamps:**
- Transaction timestamp (when sale occurred)
- Business date (store business day, may differ from calendar date)
- Processing timestamp (when recorded in system)
- Event timestamp for voids/returns (when reversal occurred)

**Delivery Options:**
- File batch (TLog daily file)
- Real-time API events
- Database query interface (read-only)

### 2. POS Control Feeds

**What to Provide:**
- Register opening/closing records with declared totals
- Sequence information (transaction ID ranges)
- End-of-day manifests with expected counts and amounts
- Cash count and deposit information
- Exception reports (detected by POS)
- Completeness markers (store certifies day closed)

**Purpose:**
- Independent verification of completeness
- Duplicate detection (retried deliveries)
- Reconciliation of source-declared totals vs. Nimbus calculations
- Store-level certification

### 3. E-Commerce / OMS

**Data to Provide:**
- Order headers (ID, timestamp, customer, channel)
- Order lines (SKU, quantity, price)
- Fulfillment status (picked, shipped, delivered)
- Cancellation records (order ID, reason, timestamp)
- Payment capture records (authorization, capture, refund)
- Refund status updates

**Delivery:**
- Event stream (order created, captured, refunded)
- Daily order export
- API query interface

### 4. Returns / Refund Systems

**Data to Provide:**
- Return authorization (return ID, original sale reference)
- Returned items (SKU, quantity, condition)
- Refund amount
- Refund method (original tender, alternate method)
- Refund status (requested, authorized, processed, posted)
- Refund reference/confirmation number

**Critical:**
- Link return to original sale transaction
- Provide refund confirmation (not assumed)
- Timestamp when refund was authorized and when processed
- Final amount (may differ from sale if partial return)

### 5. Payment Processor

**Transaction Records:**
- Authorization records (card reference, amount, timestamp, auth code)
- Capture records (transaction ID, amount, final status)
- Void/reversal records (original auth ID, reversal timestamp)
- Refund records (original transaction ID, refund amount, status)
- Chargeback records (transaction ID, amount, chargeback reason)

**Settlement Records:**
- Batch header (batch ID, date, total count, total amount)
- Transaction details (merchant ref, amount, status, adjustment)
- Fee records (processing fees, chargeback fees, etc.)
- Adjustments (refunds, chargebacks, corrections)
- Batch footer (total batched transactions)

**Required Identifiers:**
- Merchant reference (maps to POS transaction ID)
- Processor reference (for tracing at processor)
- Payment method (card type, partial card number — masked)

**Delivery:**
- Daily settlement files (SFTP, API download)
- Real-time transaction events (optional)
- Status query API (for validation checks)

### 6. Bank Feeds

**Data to Provide:**
- Deposit records (date posted, amount, reference)
- Withdrawal records (date posted, amount, reference)
- Value date (when funds available)
- Posting date (when bank posted)
- Bank reference number
- Processor reference (deposit ID from processor)
- Currency

**Purpose:**
- Confirm cash movements actually occurred
- Reconcile processor settlement to bank deposits
- Detect timing differences (settlement vs. posting)
- Identify missing or delayed deposits

**Delivery:**
- Daily bank feed file (BAI2, MT940, or CSV)
- Real-time API (optional)
- Dashboard query interface

### 7. Reference Data Systems

**What to Provide:**
- Product master (SKU, description, category)
- Promotion/discount rules (ID, applicable items, discount amount, validity window)
- Price records (effective-dated pricing)
- Store master (store ID, name, location, open/close dates)
- Legal entity / company reference data
- Tender type definitions (cash, card types, gift card, check)
- Currency definitions (code, decimal places)
- Calendar (business days, holidays, fiscal periods)

**Delivery:**
- Initial load + daily/weekly updates
- API query interface
- Master data management system interface

### 8. ERP / GL

**Data Needed from System:**
- Accounting period status (open, closed, locked)
- GL account master (account code, description, class)
- Posting rules / mapping configurations
- Posting acknowledgments (was GL posting accepted)
- Posted transaction references
- Period cutoff times

**Data to Send to ERP:**
- Daily sales and refund totals
- Tender type totals
- Tax totals (if applicable)
- Exception adjustments (if approved)
- Versioned exports (to avoid duplicates)

**Delivery:**
- Daily batch export (file, API, database insert)
- Acknowledgment/receipt (required)
- GL posting reference (required for tracking)

### 9. Inventory System

**Data to Send:**
- Inventory movement records (if applicable)
- Returns reconciliation

**Delivery:**
- Daily inventory adjustment export
- Optional for initial Phase 1

### 10. Analytics / Reporting

**Data to Send:**
- Summarized sales data
- Exception statistics
- KPI calculations
- Case status reports

**Delivery:**
- Daily/hourly refresh
- Optional for Phase 1

---

## Integration Contract Specification

**For each connector, document:**

| Field | Value | Example |
|-------|-------|---------|
| **Ownership** | System owner | "Payments Team" |
| **Purpose** | Why this data | "Confirm payment captures" |
| **Direction** | Read, Write, or Both | Read |
| **Data Model** | What gets exchanged | "Settlement transaction detail" |
| **Cadence** | Frequency | "Daily at 06:00 UTC" |
| **Expected Latency** | How fresh is data | "Within 4 hours of close" |
| **Late Arrival Window** | How long to wait | "24 hours, then escalate" |
| **Authentication** | How to connect | "OAuth2 with cert" |
| **Credentials Management** | Where to store | "Vault, rotated quarterly" |
| **Schema Version** | Data contract version | "v2.1" |
| **Pagination** | For large results | "Cursor-based, 1000 rows" |
| **Rate Limits** | Throttling | "100 req/min" |
| **Timeout** | How long to wait | "30 seconds" |
| **Retry Policy** | On failure | "3 retries, exponential backoff" |
| **Idempotency** | Duplicate handling | "Idempotency key in request" |
| **Completeness Checks** | How to verify full delivery | "Control total match" |
| **Completion Indicators** | How to know it's done | "Manifest record received" |
| **Error Handling** | What to do on failure | "Log and alert" |
| **Fallback / Manual Process** | If connector down | "Query via web dashboard" |
| **Data Retention** | How long to keep | "1 year" |
| **PII Handling** | Masking rules | "Mask full card numbers" |
| **Audit Trail** | What to log | "Request/response + timestamp" |
| **Compliance** | Any regulatory needs | "SOC2 required" |

---

## Supported Tool Actions

### Evidence Retrieval (Read-Only)

**Actions the Investigation Agent can take:**

1. **Retrieve Source Transaction**
   - Query POS journal or archive
   - Get original transaction details
   - Returns: Complete record with timestamps
   - Latency: < 5 seconds
   - Constraints: Read-only, historical only

2. **Retrieve Payment Confirmation**
   - Query processor payment records
   - Get authorization/capture status
   - Returns: Processor reference, amount, status
   - Latency: < 10 seconds
   - Constraints: Masked card data

3. **Retrieve Refund Status**
   - Query processor for refund confirmation
   - Get refund ID, amount, posting status
   - Returns: Refund confirmation record
   - Latency: < 10 seconds
   - Constraints: May be "not yet posted"

4. **Query Settlement Batch**
   - Retrieve processor settlement batch
   - Get batch contents for date range
   - Returns: Batch detail records
   - Latency: < 5 seconds
   - Constraints: Read historical batches

5. **Retrieve Bank Deposit**
   - Query bank deposit records
   - Get deposit amount, date, reference
   - Returns: Bank posting confirmation
   - Latency: < 15 seconds (depends on bank)
   - Constraints: May lag by 1 business day

6. **Query Store Status**
   - Retrieve store operational status
   - Get store open/close times, exceptions
   - Returns: Store status record
   - Latency: < 5 seconds
   - Constraints: Real-time only

7. **Check Rule / Policy Status**
   - Retrieve current rule versions
   - Get policy configuration
   - Returns: Rule definition
   - Latency: < 1 second
   - Constraints: Cached, so may be "aged"

### Action Execution (Governed Writes)

**Actions the Resolution Agent can take (all require approval):**

1. **Apply Timing Disposition**
   - Classify variance as timing
   - Mark for next-cycle validation
   - Financial write: No
   - Authority: Autonomous if policy permits
   - Latency: Immediate

2. **Apply Tolerance Disposition**
   - Accept variance within tolerance
   - Mark as resolved (within policy)
   - Financial write: No
   - Authority: Autonomous if policy permits
   - Latency: Immediate

3. **Mark Duplicate Quarantine**
   - Quarantine duplicate record
   - Protect legitimate transaction
   - Financial write: No
   - Authority: Autonomous (data integrity)
   - Latency: Immediate

4. **Request Connector Retry**
   - Retry failed evidence request
   - Replay ingestion/reconciliation
   - Financial write: No
   - Authority: IT autonomous within scope
   - Latency: Depends on connector

5. **Apply Financial Correction**
   - Post an adjustment
   - Correct a posting error
   - Financial write: Yes
   - Authority: Finance approval required
   - Latency: Immediate (subject to GL posting rules)

6. **Initiate Refund Recovery**
   - Request processor to process refund
   - Retry failed refund request
   - Financial write: Yes (external)
   - Authority: Finance approval required
   - Latency: Depends on processor (usually next settlement cycle)

7. **Change Connector Mode**
   - Pause/resume connector
   - Emergency stop
   - Financial write: No
   - Authority: IT approval required
   - Latency: Immediate

---

## Recovery and Fallback Procedures

### Connector Unavailable: Read-Only Evidence

**Scenario:** Processor API timeout during investigation

**Response:**
1. Retry after backoff (3 attempts total)
2. If successful: proceed normally
3. If failed: record evidence gap
4. Options:
   - Hold case (wait for connector recovery)
   - Request additional evidence from other sources
   - Escalate if critical evidence missing
   - Do NOT invent evidence

### Connector Unavailable: Action Execution

**Scenario:** Settlement connector down during resolution

**Response:**
1. Log failed action attempt
2. Record idempotency key and correlation ID
3. Retry after connector recovery
4. Before retry: query status to avoid duplication
5. If still failing: escalate to IT

### Late Arrival Data

**Scenario:** Settlement batch arrives after expected cutoff

**Response:**
1. Ingest with late-arrival timestamp
2. Re-run reconciliation if exception affected
3. Revalidate any validation obligations
4. Update case state if needed

### Missing Control Data

**Scenario:** Store EOD marker never arrives

**Response:**
1. Hold transaction batch (do not discard)
2. Wait for explicit deadline (typically 24 hours)
3. After deadline: escalate for manual investigation
4. Manual options: query store, use partial data with documented caveats

---

## Security & Access Control

### Credentials Management

- Store all connector credentials in secure vault (not in code)
- Rotate credentials quarterly
- Audit all credential access
- Use service accounts (not personal credentials)
- Enforce TLS 1.2+ for all connections

### Data Access Enforcement

- Apply field-level masking before agent retrieval
- Full card numbers → Masked (****1234)
- SSN/personal IDs → Removed or masked
- Authenticate every connector call
- Log all data access with timestamp/actor

### Audit Trail

- Record every connector call (request timestamp, parameters, response)
- Never log full credentials or sensitive data
- Link each connector call to its case/workflow
- Preserve audit logs for compliance retention period

---

## Performance and Optimization

### Caching Strategy

- Cache reference data (products, policies, rules) with 1-hour TTL
- Cache store master data with daily refresh
- Don't cache transaction or decision data
- Explicit invalidation on policy changes

### Batch Processing Optimization

- Process ingestion files in parallel (multiple stores)
- Run reconciliation engine asynchronously
- Aggregate evidence requests to same system
- Rate-limit to avoid overwhelming connectors

### Monitoring and Alerting

| Alert | Threshold | Action |
|-------|-----------|--------|
| Connector Latency High | > 30 sec average | Notify ops |
| Connector Errors | > 5% failure rate | Page on-call engineer |
| Evidence Request Timeout | Any occurrence | Log and retry |
| Duplicate Evidence | Multiple sources conflict | Escalate to analyst |

---

## Example: Phase 1 Minimal Integration

**Configured Connectors:**

1. **POS Integration**
   - TLog file daily delivery (SFTP)
   - Control totals file (same delivery)
   - EOD marker file (signals completeness)
   - Expected: 09:00 UTC daily

2. **Payment Processor**
   - Settlement file API download
   - Daily at 07:00 UTC
   - Contains captures, refunds, batch reconciliation

3. **Bank Feed**
   - BAI2 file download
   - Daily at 12:00 UTC
   - Deposit confirmations only (not every transaction)

4. **ERP GL**
   - Daily export via SFTP
   - Scheduled: 22:00 UTC
   - Receives: Daily sales totals, refund totals, tax detail
   - Expects GL posting acknowledgment next day

5. **Ontology Platform**
   - REST API queries for definitions
   - MCP agent access for contextual retrieval
   - Cached with 1-hour TTL

**Supported First-Pass Scenarios:**
- Settlement reconciliation (sales vs. processor)
- Timing differences (cutoff variances)
- Duplicate detection (POS/processor discrepancies)
- Cash deposit matching (processor settlement to bank)
- Tolerance exceptions (within policy)

**Not Yet Supported:**
- Multi-store orchestration
- Omnichannel reconciliation
- Gift card settlement
- Advanced inventory integration

# Shopify Integration Guide

This document describes the Shopify integration for the ZeTSA (Nimbus Sales Audit) platform.

## Overview

The Shopify integration allows ZeTSA to:
- Ingest orders and refunds from Shopify as a transaction source
- Reconcile Shopify orders with their refunds
- Detect payment and fulfillment exceptions
- Integrate Shopify data into the unified reconciliation and audit workflow

## Architecture

### Components

1. **shopify_adapter.py** — REST API client for fetching orders and refunds
2. **shopify_ingest.py** — Validation and parsing of Shopify records
3. **routes_shopify.py** — REST endpoints for sync, webhooks, and status
4. **ingest.py** (updated) — Core ingestion pipeline with Shopify support
5. **ontology.py** (updated) — Shopify-specific exception types

### Data Flow

```
Shopify API → shopify_adapter → shopify_ingest → ingest.py → CanonicalTransaction
                                                   ↓
                                           reconcile_shopify() → Exceptions → Cases
```

## Setup

### 1. Shopify API Credentials

Create a Shopify private app or custom app to get:
- Admin API access token (custom app, preferred), or API key + password (legacy private app)
- Store URL (e.g., `https://my-store.myshopify.com`)
- Webhook signing secret (if using webhooks)

### 2. Environment Configuration

Add to `.env`:

```env
SHOPIFY_STORE_URL=https://your-store.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_xxx   # preferred; or SHOPIFY_API_KEY + SHOPIFY_API_PASSWORD
SHOPIFY_STORE_ID=SHOPIFY-MAIN   # store_id stamped on records from batch and webhooks
SHOPIFY_API_VERSION=2024-01
SHOPIFY_WEBHOOK_SECRET=your_webhook_signing_secret
SHOPIFY_FETCH_LIMIT=250
SHOPIFY_SYNC_LOOKBACK_DAYS=3
```

### 3. Install Dependencies

`aiohttp` is in `requirements.txt` (already added):

```bash
pip install -r backend/requirements.txt
```

## Usage

### Manual Sync

Fetch orders and refunds for a specific date range:

```bash
curl -X POST http://localhost:8001/api/ingest/shopify/sync \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "store_id": "SHOPIFY-MAIN",
    "created_at_min": "2024-01-15T00:00:00Z",
    "created_at_max": "2024-01-16T00:00:00Z"
  }'
```

Response:
```json
{
  "status": "success",
  "received": 45,
  "archived": 43,
  "quarantined": 2,
  "canonical_created": 50,
  "errors": []
}
```

### Automated Batch Sync

The daily batch job includes Shopify data when `SHOPIFY_STORE_URL` is set; a Shopify fetch failure fails the batch (it is not skipped). To run a batch:

```bash
curl -X POST http://localhost:8001/api/batch/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "business_date": "2024-01-15"
  }'
```

### Webhook Integration (Optional)

To receive real-time order updates from Shopify:

1. Configure webhook endpoint in Shopify admin:
   - Topic: `orders/create`, `orders/updated`, `refunds/create`
   - URL: `https://your-app/api/ingest/shopify/webhook`

2. Shopify will POST webhook events to your endpoint

3. The endpoint verifies the HMAC-SHA256 signature, then ingests the order/refund through the same pipeline as a manual sync (deduplicated by payload hash). Reconciliation runs with the daily batch.

### Status Check

Get connector health and last sync timestamp:

```bash
curl http://localhost:8001/api/ingest/shopify/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Response:
```json
{
  "connector": "shopify",
  "status": "healthy",
  "last_sync_timestamp": "2024-01-15T18:30:45Z",
  "last_received_timestamp": "2024-01-15T18:35:20Z",
  "quarantined_records": 0,
  "hours_since_last_sync": 2.5
}
```

## Data Mapping

### Orders

| Shopify Field | Canonical Field | Notes |
|---|---|---|
| order.id | payment_reference | Key for reconciliation |
| order.created_at | event_timestamp | Order creation time |
| order.total_price | signed_amount | Positive amount |
| order.currency | currency | 3-letter code |
| order.financial_status | (used for validation) | pending, paid, refunded, etc. |
| "Shopify" | source_system | Source identifier |
| "ecommerce" | channel_id | Sales channel |
| store_id | store_id | Mapped from config |

### Refunds

| Shopify Field | Canonical Field | Notes |
|---|---|---|
| refund.id | settlement_reference | Unique refund ID |
| order.id | payment_reference | Links refund to order |
| sum of successful refund transactions (fallback: line-item subtotals) | signed_amount | Negative amount |
| refund.created_at | event_timestamp | Refund creation time |
| order.currency | currency | From parent order |
| "Shopify" | source_system | Source identifier |
| "ecommerce" | channel_id | Sales channel |

## Reconciliation Rules

### Order ↔ Refund Matching

Orders are matched to refunds using:
- `store_id` (must be identical; the order may be from an earlier business date)
- `payment_reference` (order ID)
- `currency` (must match)

Orders with `financial_status = paid` and no refund on file are closed as Matched. Pending, authorized or voided orders stay open. A refund that arrives later still matches the order and is checked against its total.

### Exception Types

#### SHOPIFY_UNMATCHED_REFUND
- **Trigger**: Refund exists but no corresponding order found
- **Exposure**: Full refund amount
- **Severity**: High
- **Disposition**: Manual Review
- **Example**: Order deleted after refund was processed

#### SHOPIFY_PARTIAL_REFUND
- **Trigger**: Total refunded for an order (all refunds summed) < order amount
- **Exposure**: Zero (partial refunds are normal)
- **Severity**: Medium
- **Disposition**: Disposition Only
- **Example**: Customer returns 2 of 5 items

#### SHOPIFY_OVERAGE_REFUND
- **Trigger**: Total refunded for an order > order amount
- **Exposure**: Difference amount
- **Severity**: High
- **Disposition**: Manual Review
- **Example**: Refund includes discounts or restocking fees not in original order

## Validation Rules

### Orders

- `amount` must be ≥ 0 (non-negative)
- `event_time` must be ISO-8601 with timezone
- `financial_status` must be valid (pending, authorized, paid, refunded, voided, partially_refunded, partially_paid)
- `store_id` must exist in configuration
- `amount` must not exceed max order value (guard against API errors)

### Refunds

- `amount` must be ≥ 0 (non-negative)
- `event_time` must be ISO-8601
- `order_id` is required; a refund with no matching order is flagged by reconciliation (SHOPIFY_UNMATCHED_REFUND), not quarantined
- Duplicate detection hashes only immutable fields (id, type, store, event time, amount, currency, references). Re-syncing an order whose status or note changed is skipped as a duplicate; a changed amount or time is quarantined as a conflicting resubmission. Status changes after first ingest are therefore not picked up.

## Quarantine Reasons

Records are quarantined (not processed) if:

| Reason | Condition | Resolution |
|---|---|---|
| missing fields | Required field is null/empty | Review source data |
| amount is not a valid decimal | Amount cannot be parsed | Check Shopify API response |
| amount must be non-negative | Negative amount | Check Shopify order/refund details |
| event_time is not ISO-8601 | Timestamp format invalid | Verify timestamp parsing |
| invalid financial_status | Status not in approved list | Update validation rules |
| invalid record_type | Type is not "Order" or "Refund" | Check data mapping |
| missing required field: order_id | Refund without order reference | Contact Shopify support |

Quarantined records are visible in audit logs and can be reviewed/reprocessed.

## Rate Limiting

The Shopify API enforces rate limits:
- Standard REST: ~2 requests per second (leaky bucket)

The adapter includes automatic retry logic:
- 3 attempts per request on 429, 5xx, timeouts and connection errors
- Exponential backoff from 1 second; respects `Retry-After`

## Performance Considerations

### API Calls

- Orders use Link-header cursor pagination (`SHOPIFY_FETCH_LIMIT`, max 250)
- Refunds are fetched only for orders that are `refunded`/`partially_refunded`

### Optimization

To reduce API calls:
- Filter by `created_at` range (use `created_at_min`, `created_at_max`)
- Increase lookback window only if needed
- Consider webhook integration for real-time updates instead of polling

## Troubleshooting

### Authentication Error

```
Status 401: Invalid API credentials
```

**Solution**: Verify `SHOPIFY_API_KEY` and `SHOPIFY_API_PASSWORD` in `.env`

### Rate Limited (429)

```
Rate limited after 3 retries
```

**Solution**: Adjust sync schedule or increase `SHOPIFY_SYNC_LOOKBACK_DAYS` to fetch larger windows less frequently

### Webhook Verification Failed

```
Invalid webhook signature
```

**Solution**: Verify `SHOPIFY_WEBHOOK_SECRET` matches the value in Shopify admin settings

### Missing Orders

```
Shopify sync completed: 0 canonical_created
```

**Solution**: 
1. Check `created_at_min` / `created_at_max` dates
2. Verify orders exist in Shopify admin for the date range
3. Check API credentials have read access to orders

## Example: Complete Flow

1. **Configuration**
   ```env
   SHOPIFY_STORE_URL=https://demo.myshopify.com
   SHOPIFY_API_KEY=my-api-key
   SHOPIFY_API_PASSWORD=my-api-password
   ```

2. **Fetch Data**
   ```
   POST /api/ingest/shopify/sync with store_id and date range
   ```

3. **Ingest & Validate**
   - Records parsed and validated
   - Invalid records quarantined with reasons
   - Valid records stored as SourceRecords

4. **Transform**
   - SourceRecords converted to CanonicalTransactions
   - Channels, amounts, dates normalized
   - Source lineage tracked

5. **Reconcile**
   - Orders matched to refunds
   - Exceptions detected and flagged
   - Cases created for investigation

6. **Investigate**
   - Finance team reviews exceptions
   - Evidence gathered from Shopify orders
   - Disposition determined and applied

## Testing

_No automated tests exist yet._

### Manual Testing

```bash
# Create test store_id mapping
psql -d nimbus_db -c "INSERT INTO stores (id, name) VALUES ('SHOPIFY-MAIN', 'Shopify Main Store');"

# Run sync
curl -X POST http://localhost:8001/api/ingest/shopify/sync \
  -H "Authorization: Bearer $(get_test_token)" \
  -d '{"store_id": "SHOPIFY-MAIN"}'

# Check status
curl http://localhost:8001/api/ingest/shopify/status
```

## Related Documentation

- [Ingest Pipeline](./ingest.py) — Core reconciliation logic
- [Ontology](./ontology.py) — Exception type definitions
- [Batch Processing](./batch.py) — Daily batch scheduler
- [Shopify API Docs](https://shopify.dev/docs/api/admin-rest) — Official Shopify API reference

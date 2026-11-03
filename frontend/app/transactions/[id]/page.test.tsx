import { render, screen, within } from '@testing-library/react';
import { Suspense } from 'react';
import { describe, expect, it } from 'vitest';
import TransactionPage from '@/app/transactions/[id]/page';
import { apiFailure, mockApi } from '@/test/utils';

const TX = '/api/transactions/t1';

const detail = {
  id: 't1',
  business_date: '2026-10-01',
  store_id: 'S1',
  source_system: 'POS',
  transaction_type: 'Sale',
  amount: '100.00',
  reconciliation_status: 'Matched',
  payment_reference: 'REF-1',
  detail: {
    status: 'No detail',
    line_count: 0,
    lines_truncated: false,
    lines: [],
    discount_count: 0,
    discounts_truncated: false,
    discounts: [],
    tax_count: 0,
    taxes_truncated: false,
    taxes: [],
    tender_count: 0,
    tenders_truncated: false,
    tenders: [],
    totals: null,
    total_variance: null,
    tender_variance: null,
  },
  returns: null,
  event_timestamp: '2026-10-01T10:00:00Z',
  processing_timestamp: '2026-10-01T10:00:05Z',
  register_id: 'R1',
  tender_type: 'Card',
  currency: 'USD',
  settlement_reference: null,
  settlement_date: '2026-10-01',
  transaction_subtype: null,
  channel_id: 'store',
  quantity: null,
  source_lineage: 'pos-map-v1',
  disposition: 'Closed',
  source_record_id: null,
  source: null,
  cases: [],
  cases_truncated: false,
  linked_transactions: [],
  adjustments: [],
  adjustments_truncated: false,
};

const change = (over: Record<string, unknown>) => ({
  id: 'a1',
  field: 'Amount',
  before: '100.00',
  after: '95.00',
  change: '-5.00',
  status: 'Approved',
  applied: true,
  rationale: 'Keying error',
  proposed_by: 'clerk',
  proposed_at: '2026-10-02T09:00:00Z',
  reviewed_by: 'approver',
  reviewed_at: '2026-10-02T10:00:00Z',
  ...over,
});

function open(changes: object) {
  const value = { id: 't1' };
  const params = Object.assign(Promise.resolve(value), { status: 'fulfilled' as const, value });
  mockApi({ [`GET ${TX}`]: detail, [`GET ${TX}/changes`]: changes });
  render(
    <Suspense fallback="loading">
      <TransactionPage params={params} />
    </Suspense>
  );
}

describe('Transaction page: change history', () => {
  it('shows each correction with the value before and after it, and who approved it', async () => {
    open({
      currency: 'USD',
      truncated: false,
      changes: [
        change({}),
        change({
          id: 'a2',
          field: 'Tender',
          before: 'Card',
          after: 'Cash',
          change: null,
          rationale: 'Paid in cash',
        }),
      ],
    });

    expect(await screen.findByRole('heading', { name: 'Change history' })).toBeInTheDocument();
    const amount = screen.getByText('Keying error').closest('tr') as HTMLElement;
    expect(within(amount).getByText('$100.00')).toBeInTheDocument();
    expect(within(amount).getByText('$95.00')).toBeInTheDocument();
    expect(within(amount).getByText(/approved by approver/)).toBeInTheDocument();

    const tender = screen.getByText('Paid in cash').closest('tr') as HTMLElement;
    expect(within(tender).getByText('Card')).toBeInTheDocument();
    expect(within(tender).getByText('Cash')).toBeInTheDocument();
  });

  it('marks a pending or rejected correction as not applied', async () => {
    open({
      currency: 'USD',
      truncated: false,
      changes: [
        change({
          id: 'p',
          status: 'Proposed',
          applied: false,
          reviewed_by: null,
          reviewed_at: null,
        }),
        change({ id: 'r', status: 'Rejected', applied: false, rationale: 'Not valid' }),
      ],
    });
    expect(await screen.findByText('Proposed · not applied')).toBeInTheDocument();
    expect(screen.getByText('Rejected · not applied')).toBeInTheDocument();
  });

  it('flags a correction whose stored value cannot be used', async () => {
    open({
      currency: 'USD',
      truncated: false,
      changes: [change({ after: null, change: null, invalid: true })],
    });
    expect(await screen.findByText('Invalid value')).toBeInTheDocument();
  });

  it('shows no history card for a transaction that was never corrected', async () => {
    open({ currency: 'USD', truncated: false, changes: [] });
    await screen.findByText(/Source record/);
    expect(screen.queryByRole('heading', { name: 'Change history' })).not.toBeInTheDocument();
  });

  it('still shows the transaction when the history cannot be loaded', async () => {
    open(apiFailure('Forbidden', 403));
    expect(await screen.findByText(/Source record/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Change history' })).not.toBeInTheDocument();
  });

  it('says when only the first corrections are listed', async () => {
    open({ currency: 'USD', truncated: true, changes: [change({})] });
    expect(await screen.findByText('Showing the first 200 corrections.')).toBeInTheDocument();
  });
});

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import TransactionsPage from '@/app/transactions/page';
import { calls, mockApi } from '@/test/utils';
import type { Routes } from '@/test/utils';

const row = (id: string, store: string) => ({
  id,
  business_date: '2026-10-01',
  event_timestamp: '2026-10-01T10:00:00Z',
  store_id: store,
  register_id: 'R1',
  source_system: 'POS',
  source_record_id: `src-${id}`,
  transaction_type: 'Sale',
  tender_type: 'Card',
  payment_reference: null,
  amount: '25.00',
  currency: 'USD',
  reconciliation_status: 'Matched',
  item_count: 3,
  detail_status: 'Balanced',
  original_reference: null,
});
const BRIEF = (id: string) => ({
  id,
  currency: 'USD',
  detail_status: 'Balanced',
  original_reference: null,
  return_reason: null,
  transaction_number: '42',
  line_count: 1,
  lines: [
    {
      line_no: 1,
      item_code: `SKU-${id}`,
      description: 'Rice',
      quantity: '1',
      unit_price: '25.00',
      gross_amount: '25.00',
    },
  ],
  tenders: [{ tender_type: 'Card', amount: '25.00', reference: null }],
  taxes: [],
  discount_count: 0,
  corrections: 0,
});
const routes = (extra: Routes = {}): Routes => ({
  'GET /api/transactions/facets': {
    stores: [],
    sources: [],
    types: [],
    tenders: [],
    statuses: [],
    detail_statuses: [],
    date_min: null,
    date_max: null,
  },
  'GET /api/transactions': {
    items: [row('t1', 'S1'), row('t2', 'S2')],
    total: 1_250_000,
    page: 1,
    pages: 25_000,
    by_status: {},
    net_amount: '0',
  },
  'GET /api/transactions/t1/brief': BRIEF('t1'),
  'GET /api/transactions/t2/brief': BRIEF('t2'),
  ...extra,
});

describe('Transactions page: opening a row in place', () => {
  it('loads no row detail until a row is opened', async () => {
    mockApi(routes());
    render(<TransactionsPage />);
    await screen.findAllByText('Sale');
    expect(calls('get', '/api/transactions/t1/brief')).toHaveLength(0);
  });

  it('opens a row to show how it was paid and its lines, on the same page', async () => {
    mockApi(routes());
    render(<TransactionsPage />);
    const first = (await screen.findAllByText('S1'))[0].closest('tr') as HTMLElement;

    await userEvent.click(first);
    expect(await screen.findByText('SKU-t1 · Rice · 1 × $25.00 = $25.00')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the transaction' })).toHaveAttribute(
      'href',
      '/transactions/t1'
    );
    expect(calls('get', '/api/transactions/t1/brief')).toHaveLength(1);
    expect(screen.getAllByText('S2').length).toBeGreaterThan(0); // the rest of the list is still there
  });

  it('closes a row when clicked again and opens only one at a time', async () => {
    mockApi(routes());
    render(<TransactionsPage />);
    const first = (await screen.findAllByText('S1'))[0].closest('tr') as HTMLElement;
    await userEvent.click(first);
    await screen.findByText(/SKU-t1/);

    const second = screen.getAllByText('S2')[0].closest('tr') as HTMLElement;
    await userEvent.click(second);
    expect(await screen.findByText(/SKU-t2/)).toBeInTheDocument();
    expect(screen.queryByText(/SKU-t1/)).not.toBeInTheDocument();

    await userEvent.click(second);
    expect(screen.queryByText(/SKU-t2/)).not.toBeInTheDocument();
  });

  it('closes the open row when the next page of a huge table arrives', async () => {
    mockApi(routes());
    render(<TransactionsPage />);
    const first = (await screen.findAllByText('S1'))[0].closest('tr') as HTMLElement;
    await userEvent.click(first);
    await screen.findByText(/SKU-t1/);

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(screen.queryByText(/SKU-t1/)).not.toBeInTheDocument());
    expect(calls('get', '/api/transactions').at(-1)?.[1]?.params).toMatchObject({ page: 2 });
  });

  it('still shows one page of a million-row result, with the real total', async () => {
    mockApi(routes());
    render(<TransactionsPage />);
    await screen.findAllByText('Sale');
    expect(screen.getByText(/1–50 of 1,250,000/)).toBeInTheDocument();
    expect(within(document.body).getAllByRole('row').length).toBeLessThanOrEqual(3);
  });
});

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ ...nav, back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/transactions',
  useSearchParams: () => new URLSearchParams(),
}));

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
  ...extra,
});

describe('Transactions page: opening a row', () => {
  it('goes to the transaction page when a row is clicked, without expanding it in place', async () => {
    nav.push.mockClear();
    mockApi(routes());
    render(<TransactionsPage />);
    const first = (await screen.findAllByText('S1'))[0].closest('tr') as HTMLElement;

    await userEvent.click(first);
    expect(nav.push).toHaveBeenCalledWith('/transactions/t1');
    expect(first).not.toHaveAttribute('aria-expanded');
    expect(calls('get', '/api/transactions/t1/brief')).toHaveLength(0);
  });

  it('still shows one page of a million-row result, with the real total', async () => {
    mockApi(routes());
    render(<TransactionsPage />);
    await screen.findAllByText('Sale');
    expect(screen.getByText(/1–50 of 1,250,000/)).toBeInTheDocument();
    expect(within(document.body).getAllByRole('row').length).toBeLessThanOrEqual(3);
  });
});

describe('Transaction filters', () => {
  it('reveals specialist filters and keeps their active count visible when collapsed', async () => {
    window.history.replaceState(null, '', '/transactions');
    mockApi(routes());
    render(<TransactionsPage />);
    await screen.findAllByText('Sale');
    expect(screen.queryByRole('textbox', { name: 'Register ID' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'More filters' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Register ID' }), 'R1');
    await userEvent.click(screen.getByRole('button', { name: 'Fewer filters (1 active)' }));
    expect(screen.getByRole('button', { name: 'More filters (1 active)' })).toHaveAttribute(
      'aria-expanded',
      'false'
    );
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByRole('button', { name: 'More filters' })).toBeInTheDocument();
    window.history.replaceState(null, '', '/transactions');
  });

  it('reveals specialist filters supplied in a shared URL', async () => {
    window.history.replaceState(null, '', '/transactions?register_id=R1');
    mockApi(routes());
    render(<TransactionsPage />);
    expect(await screen.findByRole('textbox', { name: 'Register ID' })).toHaveValue('R1');
    expect(screen.getByRole('button', { name: 'Fewer filters (1 active)' })).toHaveAttribute(
      'aria-expanded',
      'true'
    );
    window.history.replaceState(null, '', '/transactions');
  });
});

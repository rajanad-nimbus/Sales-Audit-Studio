import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import RegisterEventsPage from '@/app/register-events/page';
import { calls, mockApi } from '@/test/utils';
import type { Routes } from '@/test/utils';

const event = (id: string, type: string, over: object = {}) => ({
  id,
  business_date: '2026-10-01',
  event_time: '2026-10-01T09:05:00Z',
  store_id: 'S1',
  register_id: 'R3',
  cashier_id: 'amy',
  event_type: type,
  sub_type: null,
  transaction_number: '7',
  original_transaction_number: null,
  reason_code: null,
  amount: null,
  currency: 'USD',
  audit_code: null,
  ...over,
});
const context = (id: string) => ({
  event_id: id,
  store_id: 'S1',
  register_id: 'R3',
  business_date: '2026-10-01',
  earlier_not_shown: false,
  later_not_shown: true,
  events: [
    {
      id: 'before',
      event_time: '2026-10-01T09:00:00Z',
      event_type: 'open',
      sub_type: null,
      transaction_number: '1',
      amount: null,
      currency: 'USD',
      audit_code: null,
      cashier_id: 'amy',
    },
    {
      id,
      event_time: '2026-10-01T09:05:00Z',
      event_type: 'void',
      sub_type: null,
      transaction_number: '7',
      amount: null,
      currency: 'USD',
      audit_code: 'VOID_NO_ORIG',
      cashier_id: 'amy',
    },
  ],
});
/** The table row with this event type, ignoring the summary tiles above the table. */
const rowOf = async (type: string) => {
  await screen.findAllByRole('table');
  const [table] = screen.getAllByRole('table');
  return within(table).getByText(type).closest('tr') as HTMLElement;
};

const routes = (extra: Routes = {}): Routes => ({
  'GET /api/register-events/facets': { stores: [], types: [], date_min: null, date_max: null },
  'GET /api/register-events': {
    items: [event('e1', 'void', { audit_code: 'VOID_NO_ORIG' }), event('e2', 'no_sale')],
    total: 480_000,
    page: 1,
    pages: 9_600,
    by_type: { void: 1, no_sale: 1 },
    flagged: 1,
  },
  'GET /api/register-events/e1/context': context('e1'),
  'GET /api/register-events/e2/context': context('e2'),
  ...extra,
});

describe('Register Events page: opening a row in place', () => {
  it('loads nothing about surrounding events until a row is opened', async () => {
    mockApi(routes());
    render(<RegisterEventsPage />);
    await screen.findAllByText('Void');
    expect(calls('get', '/api/register-events/e1/context')).toHaveLength(0);
  });

  it('opens a row to show what happened on that register just before and after', async () => {
    mockApi(routes());
    render(<RegisterEventsPage />);
    const row = await rowOf('Void');

    await userEvent.click(row);
    expect(await screen.findByText('S1 · R3 · 2026-10-01')).toBeInTheDocument();
    const timeline = screen.getAllByRole('listitem');
    expect(within(timeline[0]).getByText('Open')).toBeInTheDocument();
    expect(within(timeline[1]).getByText(/▶ Void/)).toBeInTheDocument();
    expect(screen.getByText('Later events on this register are not shown.')).toBeInTheDocument();
    expect(calls('get', '/api/register-events/e1/context')).toHaveLength(1);
  });

  it('keeps the audit error link working without opening the row', async () => {
    mockApi(routes());
    render(<RegisterEventsPage />);
    const row = await rowOf('Void');
    const link = within(row).getByRole('link');
    expect(link).toHaveAttribute('href', '/audit-errors?code=VOID_NO_ORIG');
    await userEvent.click(link);
    expect(calls('get', '/api/register-events/e1/context')).toHaveLength(0);
  });

  it('closes the open row when clicked again, and opens one at a time', async () => {
    mockApi(routes());
    render(<RegisterEventsPage />);
    const first = await rowOf('Void');
    await userEvent.click(first);
    await screen.findByText('S1 · R3 · 2026-10-01');

    const second = await rowOf('No-sale');
    await userEvent.click(second);
    await screen.findByText('Later events on this register are not shown.');
    expect(calls('get', '/api/register-events/e2/context')).toHaveLength(1);
    await userEvent.click(second);
    expect(
      screen.queryByText('Later events on this register are not shown.')
    ).not.toBeInTheDocument();
  });

  it('shows one page of a very large result with the real total', async () => {
    mockApi(routes());
    render(<RegisterEventsPage />);
    await screen.findAllByText('Void');
    expect(screen.getByText(/1–50 of 480,000/)).toBeInTheDocument();
  });
});

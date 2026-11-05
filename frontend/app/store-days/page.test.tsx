import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import StoreDaysPage from '@/app/store-days/page';
import { ME, WithHeaders, calls, renderAs, tileValue } from '@/test/utils';
import type { Routes } from '@/test/utils';

vi.mock('@/components/StoreSelect', async () => ({
  StoreSelect: (await import('@/test/storeSelect')).FakeStoreSelect,
}));

const readiness = (ingest: string, audit: string, exportState: string, ready = false) => ({
  ingest: { state: ingest, transactions: 5, detail: `data ${ingest}` },
  audit: { state: audit, open_cases: 0, unexplained_variances: 0, detail: `audit ${audit}` },
  export: { state: exportState, records: 0, pending: 0, acknowledged: 0 },
  ready_to_close: ready,
});
const day = (store: string, date: string, status: string, r: ReturnType<typeof readiness>) => ({
  store_id: store,
  business_date: date,
  status,
  audit_version: 1,
  totals_stale: false,
  readiness: r,
});

const DAYS = [
  day('S1', '2026-10-02', 'Auditing', readiness('Complete', 'Clean', 'Not exported', true)),
  day('S2', '2026-10-02', 'Auditing', readiness('Incomplete', 'Needs attention', 'Not exported')),
  day('S3', '2026-10-01', 'Closed', readiness('Complete', 'Clean', 'Final')),
];
const SUMMARY = { total: 1240, by_status: { Auditing: 900, Closed: 340 }, totals_stale: 17 };
const late = (store: string, open = true) => ({
  store_id: store,
  business_date: '2026-10-02',
  store_day_open: open,
  state: open ? 'Incomplete' : 'No data',
  transactions: open ? 3 : 0,
  detail: open ? 'Fewer transactions arrived than the control total' : 'No transactions yet',
});
const ATTENTION = {
  business_date: '2026-10-02',
  expected_stores: 3000,
  late_stores: [late('S2'), late('S9', false)],
  late_total: 2750,
  late_page: 1,
  late_page_size: 100,
  stuck_after_days: 3,
  stuck_store_days: [
    { ...DAYS[1], days_open: 6, holding_back: ['Data: Incomplete', 'Audit: 2 open case(s)'] },
  ],
  stuck_truncated: false,
};
const BRIEF = {
  store_day: DAYS[1],
  readiness: {
    ...readiness('Incomplete', 'Needs attention', 'Not exported'),
    audit: { state: 'Needs attention', open_cases: 7, unexplained_variances: 8, detail: 'x' },
  },
  blockers: ['7 open case(s)', 'unresolved reconciliation variance'],
  variances: {
    with_variance: 8,
    unexplained: 8,
    top_unexplained: [
      {
        name: 'POS to GL Variance',
        level: 'Store',
        dimension: null,
        variance: '30.00',
        currency: 'USD',
        outcome: 'Needs case',
      },
    ],
  },
  cases: {
    open: 7,
    top: [
      {
        id: 'c1',
        case_number: 'C-1',
        case_type: 'GL gap',
        status: 'Open',
        exposure: '30.00',
        currency: 'USD',
      },
    ],
  },
  registers_with_gaps: 2,
};

const routes = (extra: Routes = {}): Routes => ({
  'GET /api/store-days': (config) =>
    new WithHeaders(DAYS, {
      'x-has-more':
        (config?.params as { page?: number } | undefined)?.page === 2 ? 'false' : 'true',
    }),
  'GET /api/store-days/summary': SUMMARY,
  'GET /api/store-days/attention': ATTENTION,
  ...extra,
});
const listParams = () => calls('get', '/api/store-days').at(-1)?.[1]?.params;
const thirtyDaysAgo = () => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);

describe('Store Days page: showing a large list', () => {
  it('shows readiness for the page of store days it was given', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());

    const s2 = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;
    expect(within(s2).getByText('Incomplete')).toBeInTheDocument();
    expect(within(s2).getByText('Needs attention')).toBeInTheDocument();
    const s3 = screen.getByText('S3', { selector: 'a' }).closest('tr') as HTMLElement;
    expect(within(s3).getByText('Final')).toBeInTheDocument();
  });

  it('asks for one page of the last 30 days, and counts every store day with the same filters', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });

    expect(listParams()).toEqual({
      store_id: undefined,
      date_from: thirtyDaysAgo(),
      date_to: undefined,
      q: undefined,
      status: undefined,
      limit: 50,
      page: 1,
    });
    // The summary sees the same filters, but not the status, so every status keeps its count.
    expect(calls('get', '/api/store-days/summary').at(-1)?.[1]?.params).toEqual({
      store_id: undefined,
      date_from: thirtyDaysAgo(),
      date_to: undefined,
      q: undefined,
    });
  });

  it('shows exact counts from the server, not counts of the rows on screen', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });

    expect(tileValue('Store Days')).toHaveTextContent('1,240');
    expect(tileValue('Not closed')).toHaveTextContent('900');
    expect(tileValue('Closed')).toHaveTextContent('340');
    expect(tileValue('Totals out of date')).toHaveTextContent('17');
    expect(screen.getByRole('button', { name: 'Auditing 900' })).toBeInTheDocument();
    expect(
      screen.getByText(/On this page: 1 ready to close · 1 data incomplete · 1 need attention/)
    ).toBeInTheDocument();
  });

  it('filters in the database: a status, a store and a date range are sent, not applied to the page', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });

    await userEvent.click(screen.getByRole('button', { name: 'Closed 340' }));
    await waitFor(() => expect(listParams()).toMatchObject({ status: 'Closed', page: 1 }));
    await userEvent.selectOptions(screen.getByLabelText('All stores'), 'S2');
    await waitFor(() => expect(listParams()).toMatchObject({ status: 'Closed', store_id: 'S2' }));
    await userEvent.clear(screen.getByLabelText('From'));
    await userEvent.type(screen.getByLabelText('From'), '2026-01-01');
    await waitFor(() => expect(listParams()).toMatchObject({ date_from: '2026-01-01' }));
    // Every filter change reloads the counts too.
    expect(calls('get', '/api/store-days/summary').at(-1)?.[1]?.params).toMatchObject({
      store_id: 'S2',
      date_from: '2026-01-01',
    });
  });

  it('searches after a pause rather than on every keystroke, and starts again from page 1', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(listParams()).toMatchObject({ page: 2 }));
    const before = calls('get', '/api/store-days').length;

    await userEvent.type(screen.getByLabelText('Search Store Days'), 'S02');
    await waitFor(() => expect(listParams()).toMatchObject({ q: 'S02', page: 1 }));
    // Three keystrokes were one search, not three.
    expect(calls('get', '/api/store-days').length - before).toBeLessThanOrEqual(2);
  });

  it('pages through the list with no total to count', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(listParams()).toMatchObject({ page: 2 }));
    expect(await screen.findByText('Page 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled(); // the server said no more
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(listParams()).toMatchObject({ page: 1 }));
  });

  it('changes the page size and returns to the first page', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(listParams()).toMatchObject({ page: 2 }));

    await userEvent.selectOptions(screen.getByLabelText('Rows per page'), '200');
    await waitFor(() => expect(listParams()).toMatchObject({ limit: 200, page: 1 }));
  });

  it('says when nothing matches, and how to widen it', async () => {
    await renderAs(
      <StoreDaysPage />,
      ME('finance'),
      routes({
        'GET /api/store-days': new WithHeaders([], { 'x-has-more': 'false' }),
        'GET /api/store-days/summary': { total: 0, by_status: {}, totals_stale: 0 },
      })
    );
    expect(await screen.findByText('No Store Days in this range')).toBeInTheDocument();
    expect(screen.getByText(/Widen the date range/)).toBeInTheDocument();
  });

  it('hides Open Store Day from roles that cannot open one', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });
    expect(screen.queryByRole('button', { name: 'Open Store Day' })).not.toBeInTheDocument();
  });

  it('lets a Sales Auditor open a store day', async () => {
    await renderAs(<StoreDaysPage />, ME('analyst'), routes());
    await screen.findByText('S2', { selector: 'a' });
    expect(screen.getByRole('button', { name: 'Open Store Day' })).toBeInTheDocument();
  });
});

describe('Store Days page: opening a row in place', () => {
  const briefRoute = { 'GET /api/store-days/S2/2026-10-02/brief': BRIEF };

  it('loads nothing extra until a row is opened', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes(briefRoute));
    await screen.findByText('S2', { selector: 'a' });
    expect(calls('get', '/api/store-days/S2/2026-10-02/brief')).toHaveLength(0);
  });

  it('opens a row to show what holds the day back, without leaving the list', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes(briefRoute));
    const row = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;

    await userEvent.click(within(row).getByText('2026-10-02'));
    expect(
      await screen.findByText(/7 open case\(s\) · unresolved reconciliation variance/)
    ).toBeInTheDocument();
    expect(screen.getByText('8 unexplained variances')).toBeInTheDocument();
    expect(screen.getByText(/POS to GL Variance: \$30\.00/)).toBeInTheDocument();
    expect(screen.getByText('7 open cases')).toBeInTheDocument();
    expect(screen.getByText(/missing from 2 registers/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open S2 · 2026-10-02' })).toHaveAttribute(
      'href',
      '/store-days/S2/2026-10-02'
    );
    expect(calls('get', '/api/store-days/S2/2026-10-02/brief')).toHaveLength(1);
    // The list is still there, on the same page.
    expect(screen.getByText('S3', { selector: 'a' })).toBeInTheDocument();
  });

  it('says only the largest few are shown when there are more', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes(briefRoute));
    const row = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByText('2026-10-02'));
    expect(
      await screen.findByText(/Showing the largest 1; the rest are on the store day/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Showing the 1 with the most exposure/)).toBeInTheDocument();
  });

  it('closes the row when clicked again, and opens only one row at a time', async () => {
    await renderAs(
      <StoreDaysPage />,
      ME('finance'),
      routes({
        ...briefRoute,
        'GET /api/store-days/S1/2026-10-02/brief': {
          ...BRIEF,
          blockers: [],
          variances: { with_variance: 0, unexplained: 0, top_unexplained: [] },
          cases: { open: 0, top: [] },
          registers_with_gaps: 0,
        },
      })
    );
    const s2 = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;
    await userEvent.click(within(s2).getByText('2026-10-02'));
    await screen.findByText('8 unexplained variances');

    const s1 = screen.getByText('S1', { selector: 'a' }).closest('tr') as HTMLElement;
    await userEvent.click(within(s1).getByText('2026-10-02'));
    expect(await screen.findByText('Nothing is holding this day back.')).toBeInTheDocument();
    expect(screen.queryByText('8 unexplained variances')).not.toBeInTheDocument();

    await userEvent.click(within(s1).getByText('2026-10-02'));
    expect(screen.queryByText('Nothing is holding this day back.')).not.toBeInTheDocument();
  });

  it('keeps the store link for going to the full day, without also opening the row', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes(briefRoute));
    const link = await screen.findByText('S2', { selector: 'a' });
    expect(link).toHaveAttribute('href', '/store-days/S2/2026-10-02');
    await userEvent.click(link);
    expect(calls('get', '/api/store-days/S2/2026-10-02/brief')).toHaveLength(0);
  });

  it('closes any open row when the page or filter changes', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes(briefRoute));
    const row = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByText('2026-10-02'));
    await screen.findByText('8 unexplained variances');

    await userEvent.click(screen.getByRole('button', { name: 'Closed 340' }));
    await waitFor(() =>
      expect(screen.queryByText('8 unexplained variances')).not.toBeInTheDocument()
    );
  });

  it('shows why a row could not be opened, without breaking the list', async () => {
    await renderAs(
      <StoreDaysPage />,
      ME('finance'),
      routes({
        'GET /api/store-days/S2/2026-10-02/brief': Object.assign(new Error('x'), {
          response: { status: 403, data: { detail: 'You are not assigned to store S2.' } },
        }),
      })
    );
    const row = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByText('2026-10-02'));
    expect(await screen.findByText('You are not assigned to store S2.')).toBeInTheDocument();
    expect(screen.getByText('S3', { selector: 'a' })).toBeInTheDocument();
  });
});

describe('Store Days page: late and stuck stores at scale', () => {
  it('shows the true number of late stores, a page at a time', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await userEvent.click(await screen.findByRole('tab', { name: /Late stores/ }));

    expect(
      await screen.findByText('Fewer transactions arrived than the control total')
    ).toBeInTheDocument();
    expect(screen.getByText('2,750 of 3,000 active stores')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Late stores\s*2750/ })).toBeInTheDocument();
    expect(screen.getByText(/1–100 of 2,750/)).toBeInTheDocument();
    const s9 = screen.getByText('S9').closest('tr') as HTMLElement;
    expect(within(s9).getByText('Not opened')).toBeInTheDocument();
  });

  it('asks the server for the next page of late stores, and for a different date from page 1', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await userEvent.click(await screen.findByRole('tab', { name: /Late stores/ }));
    await screen.findByText(/1–100 of 2,750/);

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() =>
      expect(calls('get', '/api/store-days/attention').at(-1)?.[1]?.params).toMatchObject({
        late_page: 2,
        late_page_size: 100,
      })
    );
    await userEvent.clear(screen.getByLabelText('Business date'));
    await userEvent.type(screen.getByLabelText('Business date'), '2026-10-01');
    await waitFor(() =>
      expect(calls('get', '/api/store-days/attention').at(-1)?.[1]?.params).toMatchObject({
        business_date: '2026-10-01',
        late_page: 1,
      })
    );
  });

  it('opens a stuck store day in place and reloads for another age', async () => {
    await renderAs(
      <StoreDaysPage />,
      ME('finance'),
      routes({ 'GET /api/store-days/S2/2026-10-02/brief': BRIEF })
    );
    await userEvent.click(await screen.findByRole('tab', { name: /Stuck store days/ }));
    expect(await screen.findByText('Data: Incomplete · Audit: 2 open case(s)')).toBeInTheDocument();

    await userEvent.click(screen.getByText('Data: Incomplete · Audit: 2 open case(s)'));
    expect(await screen.findByText('8 unexplained variances')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Open over 7 days' }));
    await waitFor(() =>
      expect(calls('get', '/api/store-days/attention').at(-1)?.[1]?.params).toMatchObject({
        stuck_after_days: 7,
      })
    );
  });

  it('still lists the store days when the attention lists cannot load', async () => {
    await renderAs(
      <StoreDaysPage />,
      ME('finance'),
      routes({
        'GET /api/store-days/attention': Object.assign(new Error('x'), {
          response: { status: 500 },
        }),
      })
    );
    expect(await screen.findByText('S2', { selector: 'a' })).toBeInTheDocument();
  });
});

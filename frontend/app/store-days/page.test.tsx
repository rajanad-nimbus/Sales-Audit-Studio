import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import StoreDaysPage from '@/app/store-days/page';
import { ME, calls, renderAs, tileValue } from '@/test/utils';

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
const ATTENTION = {
  business_date: '2026-10-02',
  expected_stores: 3,
  late_stores: [
    {
      store_id: 'S2',
      business_date: '2026-10-02',
      store_day_open: true,
      state: 'Incomplete',
      transactions: 3,
      detail: 'Fewer transactions arrived than the control total',
    },
    {
      store_id: 'S9',
      business_date: '2026-10-02',
      store_day_open: false,
      state: 'No data',
      transactions: 0,
      detail: 'No transactions or control total yet',
    },
  ],
  stuck_after_days: 3,
  stuck_store_days: [
    {
      ...DAYS[1],
      days_open: 6,
      holding_back: ['Data: Incomplete', 'Audit: 2 open case(s)'],
    },
  ],
  stuck_truncated: false,
};
const routes = (extra = {}) => ({
  'GET /api/store-days': DAYS,
  'GET /api/store-days/attention': ATTENTION,
  ...extra,
});

describe('Store Days page', () => {
  it('shows data, audit and export readiness for each store day', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());

    const s2 = (await screen.findByText('S2', { selector: 'a' })).closest('tr') as HTMLElement;
    expect(within(s2).getByText('Incomplete')).toBeInTheDocument();
    expect(within(s2).getByText('Needs attention')).toBeInTheDocument();
    const s3 = screen.getByText('S3', { selector: 'a' }).closest('tr') as HTMLElement;
    expect(within(s3).getByText('Final')).toBeInTheDocument();
  });

  it('counts store days by what they are ready for', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });

    expect(tileValue('Store Days')).toHaveTextContent('3');
    expect(tileValue('Ready to close')).toHaveTextContent('1');
    expect(tileValue('Data incomplete')).toHaveTextContent('1');
    expect(tileValue('Audit needs attention')).toHaveTextContent('1');
    expect(tileValue('Closed')).toHaveTextContent('1');
  });

  it('lists late stores for the chosen business date, including stores with no store day', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await userEvent.click(await screen.findByRole('tab', { name: /Late stores/ }));

    expect(
      await screen.findByText('Fewer transactions arrived than the control total')
    ).toBeInTheDocument();
    const s9 = screen.getByText('S9').closest('tr') as HTMLElement;
    expect(within(s9).getByText('Not opened')).toBeInTheDocument();
    const s2 = screen.getByText('S2').closest('tr') as HTMLElement;
    expect(within(s2).getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      '/store-days/S2/2026-10-02'
    );

    await userEvent.clear(screen.getByLabelText('Business date'));
    await userEvent.type(screen.getByLabelText('Business date'), '2026-10-01');
    await waitFor(() =>
      expect(calls('get', '/api/store-days/attention').at(-1)?.[1]).toEqual({
        params: { business_date: '2026-10-01', stuck_after_days: 3 },
      })
    );
  });

  it('lists stuck store days with why each is held back, and reloads for another age', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await userEvent.click(await screen.findByRole('tab', { name: /Stuck store days/ }));

    expect(await screen.findByText('Data: Incomplete · Audit: 2 open case(s)')).toBeInTheDocument();
    expect(screen.getByText('6')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Open over 7 days' }));
    await waitFor(() =>
      expect(calls('get', '/api/store-days/attention').at(-1)?.[1]).toMatchObject({
        params: { stuck_after_days: 7 },
      })
    );
  });

  it('still lists the store days when the attention lists cannot load', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), {
      'GET /api/store-days': DAYS,
      'GET /api/store-days/attention': Object.assign(new Error('x'), { response: { status: 500 } }),
    });
    expect(await screen.findByText('S2', { selector: 'a' })).toBeInTheDocument();
  });

  it('lets only IT open a store day', async () => {
    await renderAs(<StoreDaysPage />, ME('finance'), routes());
    await screen.findByText('S2', { selector: 'a' });
    expect(screen.queryByRole('button', { name: 'Open Store Day' })).not.toBeInTheDocument();
  });
});

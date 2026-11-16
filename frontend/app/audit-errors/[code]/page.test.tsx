import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { Suspense } from 'react';
import AuditErrorPage from '@/app/audit-errors/[code]/page';
import { ME, apiFailure, calls, renderAs } from '@/test/utils';
import type { Routes } from '@/test/utils';

const errorType = (code: string, over: object = {}) => ({
  code,
  label: `${code} label`,
  family: 'Transaction Audit',
  description: `${code} means something`,
  form: null,
  tab: null,
  recommended_solution: null,
  known_causes: [],
  resolution: null,
  open_count: 0,
  total_count: 0,
  latest_at: null,
  open_by_currency: [],
  ...over,
});
const caseRow = (n: number) => ({
  id: `c${n}`,
  case_number: `C-${n}`,
  status: 'Open',
  store_id: `S${n}`,
  business_date: '2026-10-01',
  amount: '10.00',
  currency: 'USD',
  priority: 'Normal',
  errors: 1,
});
const OVERVIEW = {
  items: [
    errorType('UNMATCHED_SALE', {
      open_count: 4,
      total_count: 120,
      latest_at: '2026-10-09T10:00:00Z',
    }),
    errorType('TAX_MISMATCH', { open_count: 0, total_count: 3 }),
    errorType('NEVER_RAISED'),
  ],
  open_total: 4,
  open_types: 1,
  window_days: 90,
};
const casesPage = (items: object[], hasMore = false, page = 1) => ({
  items,
  page,
  page_size: 25,
  has_more: hasMore,
});
const CASES = '/api/audit-errors/UNMATCHED_SALE/cases';
const routes = (extra: Routes = {}): Routes => ({
  'GET /api/audit-errors': OVERVIEW,
  [`GET ${CASES}`]: casesPage([caseRow(1), caseRow(2)]),
  ...extra,
});
const caseParams = () => calls('get', CASES).at(-1)?.[1]?.params;
const open = (r: Routes = {}, code = 'UNMATCHED_SALE') => {
  const value = { code };
  const params = Object.assign(Promise.resolve(value), { status: 'fulfilled' as const, value });
  return renderAs(
    <Suspense fallback="loading">
      <AuditErrorPage params={params} />
    </Suspense>,
    ME('finance'),
    routes(r)
  );
};

describe('Audit Error page: the error itself', () => {
  it('shows what the error means and its counts, on its own page', async () => {
    await open();
    expect(
      await screen.findByRole('heading', { name: 'UNMATCHED_SALE label' })
    ).toBeInTheDocument();
    expect(screen.getByText('UNMATCHED_SALE · Transaction Audit')).toBeInTheDocument();
    expect(screen.getByText('UNMATCHED_SALE means something')).toBeInTheDocument();
    expect(screen.getByText('4 open')).toBeInTheDocument();
    expect(screen.getByText('Raised, 90 days')).toBeInTheDocument();
    expect(calls('get', '/api/audit-errors')[0][1]).toEqual({ params: { days: 90 } });
    expect(screen.getByRole('link', { name: '← All Audit Errors' })).toHaveAttribute(
      'href',
      '/audit-errors'
    );
  });

  it('says so when the code is not a known error', async () => {
    await open({}, 'NOPE');
    expect(
      await screen.findByText('There is no audit error with the code NOPE.')
    ).toBeInTheDocument();
    expect(calls('get', '/api/audit-errors/NOPE/cases')).toHaveLength(0);
  });

  it('shows why the error could not be loaded', async () => {
    await open({ 'GET /api/audit-errors': apiFailure('nope', 403) });
    expect(await screen.findByText('Your role cannot view audit errors.')).toBeInTheDocument();
  });
});

describe('Audit Error page: its cases', () => {
  it('opens on the open cases, a page of 25, and shows them in a nested table', async () => {
    await open();

    expect(await screen.findByText('C-1')).toBeInTheDocument();
    expect(screen.getByText('UNMATCHED_SALE means something')).toBeInTheDocument();
    expect(caseParams()).toEqual({ limit: 25, status: 'open', store: undefined, page: 1 });
    expect(calls('get', CASES)).toHaveLength(1);
  });

  it('opens an error that has no open cases on every case, not an empty open list', async () => {
    await open(
      {
        'GET /api/audit-errors/TAX_MISMATCH/cases': casesPage([
          { ...caseRow(7), status: 'Closed' },
        ]),
      },
      'TAX_MISMATCH'
    );

    expect(await screen.findByText('C-7')).toBeInTheDocument();
    expect(calls('get', '/api/audit-errors/TAX_MISMATCH/cases')[0][1]?.params).toMatchObject({
      status: 'all',
    });
  });

  it('switches between open, closed and all cases on the server', async () => {
    await open();
    await screen.findByText('C-1');

    await userEvent.click(screen.getByRole('button', { name: 'Closed' }));
    await waitFor(() => expect(caseParams()).toMatchObject({ status: 'closed', page: 1 }));
    await userEvent.click(screen.getByRole('button', { name: 'All' }));
    await waitFor(() => expect(caseParams()).toMatchObject({ status: 'all' }));
    await userEvent.click(screen.getByRole('button', { name: 'Open' }));
    await waitFor(() => expect(caseParams()).toMatchObject({ status: 'open' }));
  });

  it('filters by store after a pause, and starts again from the first page', async () => {
    await open({ [`GET ${CASES}`]: casesPage([caseRow(1)], true) });
    await screen.findByText('C-1');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(caseParams()).toMatchObject({ page: 2 }));
    const before = calls('get', CASES).length;

    await userEvent.type(screen.getByLabelText('Filter cases by store'), 'S12');
    await waitFor(() => expect(caseParams()).toMatchObject({ store: 'S12', page: 1 }));
    expect(calls('get', CASES).length - before).toBeLessThanOrEqual(2); // three keystrokes, one search
  });

  it('pages through the cases with no total to count', async () => {
    await open({
      [`GET ${CASES}`]: (config) => {
        const page = (config?.params as { page: number }).page;
        return casesPage([caseRow(page * 10)], page < 3, page);
      },
    });
    await screen.findByText('C-10');
    expect(screen.getByText('Page 1')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('C-20')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('C-30')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('C-20')).toBeInTheDocument();
  });

  it('shows no paging control when everything fits on one page', async () => {
    await open();
    await screen.findByText('C-1');
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
  });

  it('says how to widen the list when the filters leave nothing', async () => {
    await open({ [`GET ${CASES}`]: casesPage([]) });
    expect(await screen.findByText('No cases match these filters')).toBeInTheDocument();
    expect(screen.getByText(/Try another status/)).toBeInTheDocument();
  });

  it('shows why the cases could not be loaded, without losing the error summary', async () => {
    await open({ [`GET ${CASES}`]: apiFailure('boom', 500) });
    expect(await screen.findByText('Could not load the cases for this error.')).toBeInTheDocument();
    expect(screen.getByText('UNMATCHED_SALE means something')).toBeInTheDocument();
  });
});

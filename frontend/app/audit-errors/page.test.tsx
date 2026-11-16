import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ ...nav, back: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/audit-errors',
  useSearchParams: () => new URLSearchParams(),
}));
import AuditErrorsPage from '@/app/audit-errors/page';
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
const open = (r: Routes = {}) => renderAs(<AuditErrorsPage />, ME('finance'), routes(r));

beforeEach(() => window.history.replaceState(null, '', '/audit-errors'));

describe('Audit Errors: the top table', () => {
  it('counts raised errors over the last 90 days by default, and says so', async () => {
    await open();
    expect(await screen.findByText('UNMATCHED_SALE label')).toBeInTheDocument();
    expect(calls('get', '/api/audit-errors')[0][1]).toEqual({ params: { days: '90' } });
    expect(screen.getByRole('columnheader', { name: 'Raised, 90 days' })).toBeInTheDocument();
    // Only errors with open cases are listed until you ask for them all.
    expect(screen.queryByText('TAX_MISMATCH label')).not.toBeInTheDocument();
  });

  it('recounts for another window and relabels the column', async () => {
    await open();
    await screen.findByText('UNMATCHED_SALE label');

    await userEvent.selectOptions(screen.getByLabelText('Raised in'), '0');
    await waitFor(() =>
      expect(calls('get', '/api/audit-errors').at(-1)?.[1]).toEqual({ params: { days: '0' } })
    );
    expect(
      await screen.findByRole('columnheader', { name: 'Raised, All time' })
    ).toBeInTheDocument();
  });

  it('lists every known error when asked', async () => {
    await open();
    await screen.findByText('UNMATCHED_SALE label');
    await userEvent.click(screen.getByRole('button', { name: 'All error types' }));
    expect(screen.getByText('TAX_MISMATCH label')).toBeInTheDocument();
    expect(screen.getByText('NEVER_RAISED label')).toBeInTheDocument();
  });

  it('shows why the errors could not be loaded', async () => {
    await open({ 'GET /api/audit-errors': apiFailure('nope', 403) });
    expect(await screen.findByText('Your role cannot view audit errors.')).toBeInTheDocument();
  });
});

describe('Audit Errors: opening an error', () => {
  beforeEach(() => {
    nav.push.mockClear();
    nav.replace.mockClear();
  });

  it('links each error to its own page instead of opening it in the table', async () => {
    await open();
    const link = await screen.findByRole('link', { name: 'UNMATCHED_SALE label' });
    expect(link).toHaveAttribute('href', '/audit-errors/UNMATCHED_SALE');
    expect(calls('get', CASES)).toHaveLength(0);
  });

  it('goes to the error page when its row is clicked', async () => {
    await open();
    await userEvent.click(await screen.findByText('2026-10-09'));
    expect(nav.push).toHaveBeenCalledWith('/audit-errors/UNMATCHED_SALE');
    expect(screen.queryByText('UNMATCHED_SALE means something')).not.toBeInTheDocument();
  });

  it('sends an older ?code= link to that error page', async () => {
    window.history.replaceState(null, '', '/audit-errors?code=UNMATCHED_SALE');
    await open();
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/audit-errors/UNMATCHED_SALE'));
  });
});

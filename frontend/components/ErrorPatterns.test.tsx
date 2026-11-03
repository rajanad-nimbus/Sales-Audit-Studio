import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ErrorPatterns } from '@/components/ErrorPatterns';
import { calls, mockApi, apiFailure } from '@/test/utils';
import { render } from '@testing-library/react';

const row = (over: object) => ({
  store_id: 'S1',
  code: 'UNMATCHED_SALE',
  label: 'Unmatched Sale',
  days: 5,
  first_seen: '2026-10-01',
  last_seen: '2026-10-09',
  trend: 'Rising',
  repeating: true,
  sources: ['Transactions'],
  ...over,
});

const PATTERNS = {
  since: '2026-09-10',
  until: '2026-10-09',
  window_days: 30,
  min_days: 3,
  registers: [
    row({ register_id: 'R7' }),
    row({
      register_id: 'R2',
      code: 'NOSALE_EXCESS',
      label: 'Excessive No-Sales',
      days: 1,
      repeating: false,
      trend: 'Steady',
    }),
  ],
  registers_truncated: false,
  stores: [row({})],
  stores_truncated: false,
  store_summary: [
    { store_id: 'S1', error_days: 6, error_codes: 2, repeating_codes: 1, trend: 'Rising' },
  ],
};

describe('ErrorPatterns', () => {
  it('lists only repeating errors by register at first, with the register named', async () => {
    mockApi({ 'GET /api/audit-errors/patterns': PATTERNS });
    render(<ErrorPatterns />);

    expect(await screen.findByText('S1 · R7')).toBeInTheDocument();
    expect(screen.getByText('Unmatched Sale')).toBeInTheDocument();
    expect(screen.queryByText('S1 · R2')).not.toBeInTheDocument();
    expect(screen.getByText('5 days')).toBeInTheDocument();
    expect(calls('get', '/api/audit-errors/patterns')[0][1]).toEqual({
      params: { days: '30', min_days: '3' },
    });
  });

  it('shows every error, including one-off ones, when asked', async () => {
    mockApi({ 'GET /api/audit-errors/patterns': PATTERNS });
    render(<ErrorPatterns />);
    await screen.findByText('S1 · R7');

    await userEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(screen.getByText('S1 · R2')).toBeInTheDocument();
    expect(screen.getByText('1 day')).toBeInTheDocument();
  });

  it('switches to the by-store view and lists the store summary', async () => {
    mockApi({ 'GET /api/audit-errors/patterns': PATTERNS });
    render(<ErrorPatterns />);
    await screen.findByText('S1 · R7');

    await userEvent.click(screen.getByRole('button', { name: 'By store' }));
    expect(screen.queryByText('S1 · R7')).not.toBeInTheDocument();
    const summary = screen.getByRole('heading', { name: 'Stores' })
      .nextElementSibling as HTMLElement;
    expect(within(summary).getByText('Rising')).toBeInTheDocument();
  });

  it('reloads with the chosen window and repeat threshold', async () => {
    mockApi({ 'GET /api/audit-errors/patterns': PATTERNS });
    render(<ErrorPatterns />);
    await screen.findByText('S1 · R7');

    await userEvent.selectOptions(screen.getByLabelText('Window'), '60');
    await userEvent.selectOptions(screen.getByLabelText('Repeating after'), '5');
    await waitFor(() =>
      expect(calls('get', '/api/audit-errors/patterns').at(-1)?.[1]).toEqual({
        params: { days: '60', min_days: '5' },
      })
    );
  });

  it('says so when the patterns cannot be loaded', async () => {
    mockApi({ 'GET /api/audit-errors/patterns': apiFailure('boom', 500) });
    render(<ErrorPatterns />);
    expect(await screen.findByText('Could not load error patterns.')).toBeInTheDocument();
  });
});

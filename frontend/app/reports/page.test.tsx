import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import ReportsPage from '@/app/reports/page';
import { apiFailure, calls, mockApi } from '@/test/utils';
import type { Routes } from '@/test/utils';

const VARIANCES = {
  title: 'Total variances',
  columns: ['Store', 'Business date', 'Variance'],
  rows: [
    ['S2', '2026-10-02', '8.00'],
    ['S1', '2026-10-01', '3.00'],
  ],
  truncated: false,
};
const TAX = {
  title: 'Tax by store and tax code',
  columns: ['Store', 'Tax code', 'Tax amount'],
  rows: [['S1', 'VAT', '5.00']],
  truncated: false,
};

const routes = (extra: Routes = {}): Routes => ({
  'GET /api/reports/variances': VARIANCES,
  'GET /api/reports/tax': TAX,
  'GET /api/reports/errors': { ...VARIANCES, columns: ['Store', 'Code'], rows: [] },
  ...extra,
});

describe('Reports page', () => {
  it('runs the variance report first and shows its columns and rows', async () => {
    mockApi(routes());
    render(<ReportsPage />);

    expect(await screen.findByText('S2')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Business date' })).toBeInTheDocument();
    expect(screen.getByText('2 rows')).toBeInTheDocument();
  });

  it('switches report and applies the date range', async () => {
    mockApi(routes());
    render(<ReportsPage />);
    await screen.findByText('S2');

    await userEvent.click(screen.getByRole('button', { name: 'Tax / VAT' }));
    expect(await screen.findByText('VAT')).toBeInTheDocument();
    expect(screen.queryByText('S2')).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('To'), '2026-10-31');
    await waitFor(() =>
      expect(calls('get', '/api/reports/tax').at(-1)?.[1]).toEqual({
        params: { start: undefined, end: '2026-10-31' },
      })
    );
  });

  it('says so when a report has no rows, and cannot be downloaded', async () => {
    mockApi(routes());
    render(<ReportsPage />);
    await screen.findByText('S2');

    await userEvent.click(screen.getByRole('button', { name: 'Errors by store' }));
    expect(await screen.findByText('Nothing to report for this range')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled();
  });

  it('flags a report that was cut off at the row limit', async () => {
    mockApi(routes({ 'GET /api/reports/variances': { ...VARIANCES, truncated: true } }));
    render(<ReportsPage />);
    expect(await screen.findByText(/first 5,000/)).toBeInTheDocument();
  });

  it('downloads the current report as CSV for the chosen range', async () => {
    mockApi(
      routes({
        'GET /api/reports/variances': (config) =>
          config?.params?.format === 'csv'
            ? new Blob(['Store\nS2\n'], { type: 'text/csv' })
            : VARIANCES,
      })
    );
    render(<ReportsPage />);
    await screen.findByText('S2');
    await userEvent.type(screen.getByLabelText('From'), '2026-10-01');

    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    await waitFor(() =>
      expect(
        calls('get', '/api/reports/variances').some(([, c]) => c?.params?.format === 'csv')
      ).toBe(true)
    );
    const download = calls('get', '/api/reports/variances').find(
      ([, c]) => c?.params?.format === 'csv'
    );
    expect(download?.[1]).toMatchObject({
      params: { start: '2026-10-01', format: 'csv' },
      responseType: 'blob',
    });
  });

  it('shows the reason when a report cannot be run', async () => {
    mockApi(routes({ 'GET /api/reports/variances': apiFailure('Unknown report', 404) }));
    render(<ReportsPage />);
    expect(await screen.findByText('Unknown report')).toBeInTheDocument();
    const table = screen.queryByRole('table');
    expect(table).not.toBeInTheDocument();
    expect(within(document.body).queryByText('S2')).not.toBeInTheDocument();
  });
});

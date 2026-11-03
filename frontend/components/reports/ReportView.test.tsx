import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ReportView } from '@/components/reports/ReportView';
import { REPORT_PAGES } from '@/lib/reports';
import { WithHeaders, apiFailure, calls, mockApi } from '@/test/utils';

const PATH = '/api/reports/variances';
const lastParams = () => calls('get', PATH).at(-1)?.[1]?.params;
const sevenDaysAgo = () => new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);

const reportPage = (page: number, rows: string[][], hasMore: boolean, pageSize = 50) => ({
  title: 'Total variances',
  page,
  page_size: pageSize,
  columns: ['Store', 'Business date'],
  rows,
  has_more: hasMore,
});

/** A server holding `total` rows, answering whichever page is asked for. */
function server(total: number) {
  mockApi({
    [`GET ${PATH}`]: (config) => {
      const {
        page = 1,
        page_size = 50,
        format,
      } = (config?.params ?? {}) as Record<string, number | string>;
      if (format === 'csv')
        return new WithHeaders(new Blob(['Store\n']), { 'x-report-truncated': 'false' });
      const from = (Number(page) - 1) * Number(page_size);
      const rows = Array.from(
        { length: Math.max(0, Math.min(Number(page_size), total - from)) },
        (_, i) => [`S${from + i + 1}`, '2026-10-01']
      );
      return reportPage(Number(page), rows, from + rows.length < total, Number(page_size));
    },
  });
}

describe('ReportView', () => {
  it('opens on the last seven days, first page, so a large estate is not loaded all at once', async () => {
    server(120);
    render(<ReportView report={REPORT_PAGES.variances} />);

    expect(await screen.findByText('S1')).toBeInTheDocument();
    expect(lastParams()).toEqual({ start: sevenDaysAgo(), end: undefined, page: 1, page_size: 50 });
    expect(screen.getByLabelText('From')).toHaveValue(sevenDaysAgo());
    expect(screen.getByText('Rows 1–50')).toBeInTheDocument();
    expect(calls('get', PATH)).toHaveLength(1);
  });

  it('asks the server for the next page and back, with no total to count', async () => {
    server(120);
    render(<ReportView report={REPORT_PAGES.variances} />);
    await screen.findByText('S1');
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('S51')).toBeInTheDocument();
    expect(screen.queryByText('S1')).not.toBeInTheDocument();
    expect(lastParams()).toMatchObject({ page: 2 });
    expect(screen.getByText('Rows 51–100')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('S101')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('S51')).toBeInTheDocument();
    expect(lastParams()).toMatchObject({ page: 2 });
  });

  it('goes back to the first page when the range or page size changes', async () => {
    server(120);
    render(<ReportView report={REPORT_PAGES.variances} />);
    await screen.findByText('S1');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('S51');

    await userEvent.selectOptions(screen.getByLabelText('Rows per page'), '25');
    expect(await screen.findByText('S1')).toBeInTheDocument();
    expect(lastParams()).toMatchObject({ page: 1, page_size: 25 });

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('S26');
    await userEvent.clear(screen.getByLabelText('From'));
    await userEvent.type(screen.getByLabelText('From'), '2026-01-01');
    await waitFor(() =>
      expect(lastParams()).toEqual({ start: '2026-01-01', end: undefined, page: 1, page_size: 25 })
    );
  });

  it('does not reload the report just because the page re-renders', async () => {
    server(10);
    const { rerender } = render(<ReportView report={REPORT_PAGES.variances} />);
    await screen.findByText('S1');
    rerender(<ReportView report={REPORT_PAGES.variances} />);
    expect(calls('get', PATH)).toHaveLength(1);
  });

  it('explains an empty page and points back, or to a wider range on the first page', async () => {
    server(0);
    render(<ReportView report={REPORT_PAGES.variances} />);
    expect(await screen.findByText('Nothing to report for this range')).toBeInTheDocument();
    expect(screen.getByText('Try a wider date range.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled();
  });

  it('downloads the whole range as CSV and warns when it was cut short', async () => {
    mockApi({
      [`GET ${PATH}`]: (config) =>
        (config?.params as Record<string, unknown>)?.format === 'csv'
          ? new WithHeaders(new Blob(['Store\nS1\n']), { 'x-report-truncated': 'true' })
          : reportPage(1, [['S1', '2026-10-01']], false),
    });
    render(<ReportView report={REPORT_PAGES.variances} />);
    await screen.findByText('S1');

    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    expect(await screen.findByText(/stopped at 100,000 rows/)).toBeInTheDocument();
    const download = calls('get', PATH).find(([, c]) => c?.params?.format === 'csv');
    expect(download?.[1]).toMatchObject({
      params: { start: sevenDaysAgo(), format: 'csv' },
      responseType: 'blob',
    });
    // The download asks for the whole range, not just the page on screen.
    expect(download?.[1]?.params).not.toHaveProperty('page');
  });

  it('shows no warning when the whole range was downloaded', async () => {
    server(3);
    render(<ReportView report={REPORT_PAGES.variances} />);
    await screen.findByText('S1');
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    await waitFor(() =>
      expect(calls('get', PATH).some(([, c]) => c?.params?.format === 'csv')).toBe(true)
    );
    expect(screen.queryByText(/stopped at/)).not.toBeInTheDocument();
  });

  it('shows why a report could not be run', async () => {
    mockApi({ [`GET ${PATH}`]: apiFailure('Unknown report', 404) });
    render(<ReportView report={REPORT_PAGES.variances} />);
    expect(await screen.findByText('Unknown report')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

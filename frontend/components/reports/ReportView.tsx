'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Alert, Button, DataTable, PageHeader, PageNav, Toolbar } from '@/components/ui';
import { apiError } from '@/lib/storeDays';
import type { ReportPage } from '@/lib/reports';

type Cell = string | number | null;
interface Report {
  columns: string[];
  rows: Cell[][];
  has_more: boolean;
  page: number;
  page_size: number;
}

const PAGE_SIZES = [25, 50, 100, 200];
const daysAgo = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

/**
 * One report, read a page at a time from the server. The range starts at the last seven days so a
 * large store estate does not load everything on arrival; widen it when more is needed.
 */
export function ReportView({ report }: { report: ReportPage }) {
  const [start, setStart] = useState(daysAgo(7));
  const [end, setEnd] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const range = { start: start || undefined, end: end || undefined };

  const load = useCallback(async () => {
    setData(null);
    try {
      const { data } = await api.get<Report>(`/api/reports/${report.name}`, {
        params: {
          start: start || undefined,
          end: end || undefined,
          page,
          page_size: pageSize,
        },
      });
      setData(data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not run the report.'));
    }
  }, [report.name, start, end, page, pageSize]);
  useEffect(() => {
    load();
  }, [load]);

  const download = async () => {
    setNotice(null);
    try {
      const res = await api.get(`/api/reports/${report.name}`, {
        params: { ...range, format: 'csv' },
        responseType: 'blob',
      });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(res.data);
      link.download = `${report.name}_${start || 'start'}_${end || 'now'}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
      if (res.headers?.['x-report-truncated'] === 'true') {
        setNotice('The download stopped at 100,000 rows. Narrow the date range to get the rest.');
      }
    } catch (e) {
      setError(apiError(e, 'Could not download the report.'));
    }
  };

  const filter = (change: () => void) => {
    change();
    setPage(1);
  };
  const first = data ? (data.page - 1) * data.page_size + 1 : 0;

  return (
    <div className="page-container">
      <PageHeader
        title={report.title}
        subtitle={report.subtitle}
        actions={
          <Button onClick={() => void download()} disabled={!data || data.rows.length === 0}>
            Download CSV
          </Button>
        }
      />
      {error && <Alert tone="error">{error}</Alert>}
      {notice && (
        <Alert tone="warning" onDismiss={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      <Toolbar
        count={
          data && data.rows.length > 0
            ? `Rows ${first.toLocaleString()}–${(first + data.rows.length - 1).toLocaleString()}`
            : undefined
        }
      >
        <input
          type="date"
          aria-label="From"
          value={start}
          max={end || undefined}
          onChange={(e) => filter(() => setStart(e.target.value))}
        />
        <input
          type="date"
          aria-label="To"
          value={end}
          min={start || undefined}
          onChange={(e) => filter(() => setEnd(e.target.value))}
        />
        <select
          aria-label="Rows per page"
          value={pageSize}
          onChange={(e) => filter(() => setPageSize(Number(e.target.value)))}
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={n}>
              {n} / page
            </option>
          ))}
        </select>
      </Toolbar>
      <DataTable
        columns={(data?.columns ?? []).map((header, i) => ({
          key: header,
          header,
          primary: i === 0,
          render: (r: Cell[]) => r[i] ?? '',
        }))}
        rows={data ? data.rows : null}
        rowKey={(r) => r.join('|')}
        emptyTitle="Nothing to report for this range"
        emptyHint={page > 1 ? 'There are no more rows. Go back a page.' : 'Try a wider date range.'}
        footer={data && <PageNav page={data.page} hasMore={data.has_more} onPage={setPage} />}
      />
    </div>
  );
}

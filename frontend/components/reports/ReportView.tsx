'use client';
import { formatNumber } from '@/lib/preferences';
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

function initialReportState() {
  const params =
    typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search);
  const requestedPage = Number(params.get('page'));
  const requestedPageSize = Number(params.get('page_size'));
  return {
    start: params.has('start') ? params.get('start') || '' : daysAgo(7),
    end: params.get('end') || '',
    storeId: (params.get('store_id') || '').slice(0, 50),
    page: Number.isInteger(requestedPage) && requestedPage > 0 && requestedPage <= 100_000
      ? requestedPage
      : 1,
    pageSize: PAGE_SIZES.includes(requestedPageSize) ? requestedPageSize : 50,
  };
}

/**
 * One report, read a page at a time from the server. The range starts at the last seven days so a
 * large store estate does not load everything on arrival; widen it when more is needed.
 */
export function ReportView({ report }: { report: ReportPage }) {
  const [initial] = useState(initialReportState);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [storeId, setStoreId] = useState(initial.storeId);
  const [stores, setStores] = useState<string[]>([]);
  const [page, setPage] = useState(initial.page);
  const [pageSize, setPageSize] = useState(initial.pageSize);
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const range = {
    start: start || undefined,
    end: end || undefined,
    store_id: storeId || undefined,
  };

  useEffect(() => {
    api
      .get<string[]>('/api/stores')
      .then(({ data }) => setStores(data))
      .catch(() => setStores([]));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    for (const key of ['start', 'end', 'store_id', 'page', 'page_size']) params.delete(key);
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    if (storeId) params.set('store_id', storeId);
    if (page > 1) params.set('page', String(page));
    if (pageSize !== 50) params.set('page_size', String(pageSize));
    const query = params.toString();
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ''}`;
    if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      window.history.replaceState(null, '', nextUrl);
    }
  }, [start, end, storeId, page, pageSize]);

  const load = useCallback(async () => {
    setData(null);
    try {
      const { data } = await api.get<Report>(`/api/reports/${report.name}`, {
        params: {
          start: start || undefined,
          end: end || undefined,
          store_id: storeId || undefined,
          page,
          page_size: pageSize,
        },
      });
      setData(data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not run the report.'));
    }
  }, [report.name, start, end, storeId, page, pageSize]);
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
      link.download = `${report.name}_${storeId || 'all-stores'}_${start || 'start'}_${end || 'now'}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
      if (res.headers?.['x-report-truncated'] === 'true') {
        setNotice('The download stopped at 100,000 rows. Narrow the date range to get the rest.');
      }
    } catch (e) {
      setError(apiError(e, 'Could not download the report.'));
    }
  };

  const copyReportLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      setNotice('Could not copy the link. You can copy it from the address bar.');
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
          <div className="row-actions">
            <Button variant="secondary" onClick={() => void copyReportLink()}>
              {linkCopied ? 'Link copied' : 'Copy report link'}
            </Button>
            <Button onClick={() => void download()} disabled={!data || data.rows.length === 0}>
              Download CSV
            </Button>
          </div>
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
            ? `Rows ${formatNumber(first)}–${formatNumber(first + data.rows.length - 1)}`
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
          aria-label="Store"
          value={storeId}
          onChange={(event) => filter(() => setStoreId(event.target.value))}
        >
          <option value="">All assigned stores</option>
          {stores.map((store) => (
            <option key={store} value={store}>
              {store}
            </option>
          ))}
        </select>
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
          csvValue: (r: Cell[]) => r[i] ?? '',
        }))}
        rows={data ? data.rows : null}
        rowKey={(r) => r.join('|')}
        csvFileName={`${report.name}-visible.csv`}
        emptyTitle="Nothing to report for this range"
        emptyHint={page > 1 ? 'There are no more rows. Go back a page.' : 'Try a wider date range.'}
        footer={data && <PageNav page={data.page} hasMore={data.has_more} onPage={setPage} />}
      />
    </div>
  );
}

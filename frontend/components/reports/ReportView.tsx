'use client';
import Link from 'next/link';
import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { Alert, Button, Card, DataTable, Field, PageHeader, PageNav, Toolbar } from '@/components/ui';
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
interface TrendPoint {
  month: string;
  total: string;
  level: string;
  currency: string;
  amount: string;
}

const PAGE_SIZES = [25, 50, 100, 200];
const daysAgo = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

function monthsBetween(start: string, end: string): string[] {
  if (!start || !end) return [];
  const first = Date.parse(`${start.slice(0, 7)}-01T00:00:00Z`);
  const last = Date.parse(`${end.slice(0, 7)}-01T00:00:00Z`);
  if (!Number.isFinite(first) || !Number.isFinite(last) || first > last) return [];
  const months: string[] = [];
  const cursor = new Date(first);
  while (cursor.valueOf() <= last) {
    months.push(cursor.toISOString().slice(0, 7));
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  return months;
}

function initialReportState(yearToDate = false) {
  const params = new URLSearchParams(window.location.search);
  const requestedPage = Number(params.get('page'));
  const requestedPageSize = Number(params.get('page_size'));
  const today = new Date().toISOString().slice(0, 10);
  const reportEnd = params.get('end') || today;
  const yearStart = `${reportEnd.slice(0, 4)}-01-01`;
  return {
    start: params.has('start')
      ? params.get('start') || ''
      : yearToDate
        ? yearStart
        : daysAgo(7),
    end: params.has('end') ? params.get('end') || '' : yearToDate ? today : '',
    storeId: (params.get('store_id') || '').slice(0, 50),
    page:
      Number.isInteger(requestedPage) && requestedPage > 0 && requestedPage <= 100_000
        ? requestedPage
        : 1,
    pageSize: PAGE_SIZES.includes(requestedPageSize) ? requestedPageSize : 50,
  };
}

/**
 * One report, read a page at a time from the server. Operational reports start with a short date
 * range; Flash Sales starts at January 1 and uses its To date as the year-to-date cutoff.
 */
export function ReportView({ report }: { report: ReportPage }) {
  const [start, setStart] = useState(daysAgo(7));
  const [end, setEnd] = useState('');
  const [storeId, setStoreId] = useState('');
  const [stores, setStores] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<Report | null>(null);
  const [monthlyTrend, setMonthlyTrend] = useState<TrendPoint[]>([]);
  const [trendError, setTrendError] = useState<string | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const [trendTotal, setTrendTotal] = useState('');
  const [trendCurrency, setTrendCurrency] = useState('');
  const [filtersReady, setFiltersReady] = useState(false);
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
    const initial = initialReportState(Boolean(report.yearToDate));
    setStart(initial.start);
    setEnd(initial.end);
    setStoreId(initial.storeId);
    setPage(initial.page);
    setPageSize(initial.pageSize);
    setFiltersReady(true);
  }, [report.yearToDate]);

  useEffect(() => {
    if (!filtersReady) return;
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
  }, [filtersReady, start, end, storeId, page, pageSize]);

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
    if (filtersReady) load();
  }, [filtersReady, load]);

  useEffect(() => {
    if (!filtersReady || !report.yearToDate) {
      setMonthlyTrend([]);
      setTrendError(null);
      setTrendLoading(false);
      return;
    }
    let current = true;
    setTrendError(null);
    setTrendLoading(true);
    api
      .get<{ rows: TrendPoint[] }>('/api/reports/flash-sales-ytd/trend', {
        params: {
          start: start || undefined,
          end: end || undefined,
          store_id: storeId || undefined,
        },
      })
      .then(({ data }) => {
        if (current) {
          setMonthlyTrend(data.rows);
          setTrendLoading(false);
        }
      })
      .catch((cause) => {
        if (current) {
          setMonthlyTrend([]);
          setTrendError(apiError(cause, 'Could not load the monthly trend.'));
          setTrendLoading(false);
        }
      });
    return () => {
      current = false;
    };
  }, [filtersReady, report.yearToDate, start, end, storeId]);

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
  const changeEnd = (value: string) =>
    filter(() => {
      setEnd(value);
      if (report.yearToDate) {
        const asOf = value || new Date().toISOString().slice(0, 10);
        if (!start || start.slice(0, 4) !== asOf.slice(0, 4)) {
          setStart(`${asOf.slice(0, 4)}-01-01`);
        }
      }
    });
  const first = data ? (data.page - 1) * data.page_size + 1 : 0;
  const ytdStart = report.yearToDate
    ? `${(end || new Date().toISOString().slice(0, 10)).slice(0, 4)}-01-01`
    : undefined;
  const visibleMonthlyTrend = report.yearToDate ? monthlyTrend : [];
  const trendTotals = Array.from(
    new Map(
      visibleMonthlyTrend.map((row) => [
        `${row.total}\u0000${row.level}`,
        { key: `${row.total}\u0000${row.level}`, name: row.total, level: row.level },
      ])
    ).values()
  );
  const activeTrendTotal =
    trendTotals.find((item) => item.key === trendTotal)?.key ?? trendTotals[0]?.key ?? '';
  const trendRows = visibleMonthlyTrend.filter(
    (row) => `${row.total}\u0000${row.level}` === activeTrendTotal
  );
  const trendCurrencies = [...new Set(trendRows.map((row) => row.currency))].sort();
  const activeTrendCurrency = trendCurrencies.includes(trendCurrency)
    ? trendCurrency
    : (trendCurrencies[0] ?? '');
  const selectedTrendRows = trendRows.filter((row) => row.currency === activeTrendCurrency);
  const months = monthsBetween(start || ytdStart || '', end || new Date().toISOString().slice(0, 10));
  const amountByMonth = new Map(selectedTrendRows.map((row) => [row.month, Number(row.amount)]));
  const monthValues = months.map((month) => ({ month, amount: amountByMonth.get(month) ?? 0 }));
  const trendMaximum = Math.max(...monthValues.map((item) => Math.abs(item.amount)), 0);
  const detachTitle = [
    report.title,
    storeId ? `Store ${storeId}` : 'All assigned stores',
    `${start || ytdStart || 'any date'} → ${end || 'latest'}`,
  ].join(' · ');

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
          min={ytdStart}
          max={end || undefined}
          onChange={(e) => filter(() => setStart(e.target.value))}
        />
        <input
          type="date"
          aria-label="To"
          value={end}
          min={!report.yearToDate ? start || undefined : undefined}
          onChange={(e) => changeEnd(e.target.value)}
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
      {report.yearToDate && data && (
        <Card title="Monthly sales">
          {trendError ? (
            <Alert tone="error">{trendError}</Alert>
          ) : trendLoading ? (
            <p className="hint" role="status">Loading monthly trend…</p>
          ) : monthlyTrend.length === 0 ? (
            <p className="hint">
              No current, non-stale totals are assigned to Flash Sales for this range.
            </p>
          ) : (
            <>
              <p className="hint">
                Monthly sums use the current, non-stale totals in this report. Values include{' '}
                {storeId ? `store ${storeId}` : 'all stores in your authorized scope'}.
              </p>
              <div className="cluster flash-trend-controls">
                <Field label="Total">
                  <select value={activeTrendTotal} onChange={(event) => setTrendTotal(event.target.value)}>
                    {trendTotals.map((item) => (
                      <option key={item.key} value={item.key}>
                        {item.name} · {item.level}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Currency">
                  <select
                    value={activeTrendCurrency}
                    onChange={(event) => setTrendCurrency(event.target.value)}
                  >
                    {trendCurrencies.map((currency) => (
                      <option key={currency} value={currency}>
                        {currency}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {monthValues.length > 0 && trendMaximum > 0 ? (
                <div className="flash-trend-chart" role="list" aria-label="Monthly sales amounts">
                  {monthValues.map(({ month, amount }) => {
                    const height = trendMaximum > 0 ? (Math.abs(amount) / trendMaximum) * 48 : 0;
                    const label = new Date(`${month}-01T00:00:00Z`).toLocaleString(undefined, {
                      month: 'short',
                      timeZone: 'UTC',
                    });
                    return (
                      <div className="flash-trend-month" role="listitem" key={month}>
                        <div className="flash-trend-track" aria-hidden="true">
                          <div
                            className={`flash-trend-bar${amount < 0 ? ' is-negative' : ''}`}
                            style={
                              amount < 0
                                ? { height: `${height}%`, top: '50%' }
                                : { height: `${height}%`, bottom: '50%' }
                            }
                          />
                        </div>
                        <strong>{money(amount, activeTrendCurrency)}</strong>
                        <span>{label}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="hint">No sales values are available for the selected total and currency.</p>
              )}
            </>
          )}
        </Card>
      )}
      <DataTable
        columns={(data?.columns ?? []).map((header, i) => ({
          key: header,
          header,
          primary: i === 0,
          render: (r: Cell[]) => {
            const value = r[i] ?? '';
            if (i !== 0) return value;
            if (report.name === 'flash-totals' && r[0] && r[1]) {
              return (
                <Link
                  href={`/store-days/${encodeURIComponent(String(r[0]))}/${encodeURIComponent(String(r[1]))}?tab=analysis`}
                  aria-label={`Open ${String(r[0])} store-day analysis for ${String(r[1])}`}
                >
                  {value}
                </Link>
              );
            }
            if (report.name === 'flash-sales-ytd' && r[0]) {
              const params = new URLSearchParams({ store_id: String(r[0]) });
              if (start) params.set('start', start);
              if (end) params.set('end', end);
              return (
                <Link
                  href={`/reports/flash-totals?${params.toString()}`}
                  aria-label={`Open daily Flash Totals for ${String(r[0])}`}
                >
                  {value}
                </Link>
              );
            }
            return value;
          },
          csvValue: (r: Cell[]) => r[i] ?? '',
        }))}
        rows={data ? data.rows : null}
        rowKey={(r) => r.join('|')}
        csvFileName={`${report.name}-visible.csv`}
        detachTitle={detachTitle}
        emptyTitle="Nothing to report for this range"
        emptyHint={page > 1 ? 'There are no more rows. Go back a page.' : 'Try a wider date range.'}
        footer={data && <PageNav page={data.page} hasMore={data.has_more} onPage={setPage} />}
      />
    </div>
  );
}

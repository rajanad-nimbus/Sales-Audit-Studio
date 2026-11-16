'use client';

import { formatNumber } from '@/lib/preferences';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { StoreSelect } from '@/components/StoreSelect';
import {
  Alert,
  Button,
  Card,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
  Pager,
} from '@/components/ui';
import type { Column } from '@/components/ui';
import { api, money } from '@/lib/api';
import { formatRollupKey } from '@/lib/storeDays';
import { SummarySavedViews, type SummaryFilters } from '@/components/SummarySavedViews';

interface MiscTotalRow {
  store_id: string;
  business_date: string;
  store_day_status: string;
  totals_stale: boolean;
  version: number;
  total_name: string;
  level: string;
  dimension: string | null;
  rollup_key: string | null;
  currency: string;
  calculated_amount: string;
  declared_amount: string | null;
  variance_amount: string | null;
  definition_revision: number | null;
}
interface Result {
  items: MiscTotalRow[];
  total: number;
  store_days: number;
  stale_store_days: number;
  page: number;
  page_size: number;
  pages: number;
}

const columns: Column<MiscTotalRow>[] = [
  {
    key: 'total',
    header: 'Miscellaneous total',
    primary: true,
    render: (row) => (
      <>
        {row.total_name}
        <small>
          {row.level}
          {row.dimension ? ` · ${row.dimension}` : ''}
          {row.rollup_key ? ` · ${formatRollupKey(row.rollup_key)}` : ''}
        </small>
      </>
    ),
  },
  {
    key: 'store-day',
    header: 'Store day',
    render: (row) => (
      <Link href={`/store-days/${encodeURIComponent(row.store_id)}/${row.business_date}`}>
        {row.store_id} · {row.business_date}
      </Link>
    ),
  },
  {
    key: 'status',
    header: 'Day status',
    render: (row) =>
      `${row.store_day_status} · v${row.version}${row.totals_stale ? ' · stale' : ''}`,
  },
  { key: 'currency', header: 'Currency', render: (row) => row.currency },
  {
    key: 'calculated',
    header: 'Calculated',
    numeric: true,
    render: (row) => money(row.calculated_amount, row.currency),
  },
  {
    key: 'declared',
    header: 'Declared',
    numeric: true,
    render: (row) =>
      row.declared_amount === null ? '—' : money(row.declared_amount, row.currency),
  },
  {
    key: 'variance',
    header: 'Variance',
    numeric: true,
    render: (row) =>
      row.variance_amount === null ? '—' : money(row.variance_amount, row.currency),
  },
  {
    key: 'definition',
    header: 'Definition',
    render: (row) => `Revision ${row.definition_revision ?? '—'}`,
  },
];

export default function MiscellaneousTotalsPage() {
  const [initialQuery] = useState(() => {
    const query =
      typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
    return {
      store: query?.get('store_id') ?? '',
      dateFrom: query?.get('date_from') ?? '',
      dateTo: query?.get('date_to') ?? '',
      search: query?.get('q') ?? '',
    };
  });
  const [store, setStore] = useState(initialQuery.store);
  const [dateFrom, setDateFrom] = useState(initialQuery.dateFrom);
  const [dateTo, setDateTo] = useState(initialQuery.dateTo);
  const [search, setSearch] = useState('');
  const [applied, setApplied] = useState({ store: '', dateFrom: '', dateTo: '', search: '' });
  const [result, setResult] = useState<Result | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const autoLoaded = useRef(false);

  async function load(nextPage = 1, filters = applied) {
    setBusy(true);
    setError('');
    try {
      const { data } = await api.get<Result>('/api/misc-totals', {
        params: {
          store_id: filters.store || undefined,
          date_from: filters.dateFrom || undefined,
          date_to: filters.dateTo || undefined,
          q: filters.search || undefined,
          page: nextPage,
          page_size: 50,
        },
      });
      setResult(data);
      setPage(nextPage);
      setApplied(filters);
      setNotice('');
    } catch (e) {
      setError(
        (e as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
          'Could not load miscellaneous totals.'
      );
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!initialQuery.store || !initialQuery.dateFrom || !initialQuery.dateTo || autoLoaded.current)
      return;
    autoLoaded.current = true;
    void load(1, initialQuery);
  }, [initialQuery, load]);

  function apply(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    applyFilters({ store, dateFrom, dateTo, search });
  }

  function applyFilters(filters: SummaryFilters) {
    setStore(filters.store);
    setDateFrom(filters.dateFrom);
    setDateTo(filters.dateTo);
    setSearch(filters.search);
    setApplied(filters);
    const params = new URLSearchParams();
    if (filters.store) params.set('store_id', filters.store);
    if (filters.dateFrom) params.set('date_from', filters.dateFrom);
    if (filters.dateTo) params.set('date_to', filters.dateTo);
    if (filters.search) params.set('q', filters.search);
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${params.size ? `?${params.toString()}` : ''}`
    );
    void load(1, filters);
  }

  async function download() {
    if (!result?.total) return;
    try {
      const { data, headers } = await api.get('/api/misc-totals', {
        params: {
          store_id: applied.store || undefined,
          date_from: applied.dateFrom || undefined,
          date_to: applied.dateTo || undefined,
          q: applied.search || undefined,
          format: 'csv',
        },
        responseType: 'blob',
      });
      const url = URL.createObjectURL(data);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'miscellaneous-totals.csv';
      anchor.click();
      URL.revokeObjectURL(url);
      if (headers['x-report-truncated'] === 'true') {
        setNotice('The export reached 100,000 rows. Narrow the date range to download the rest.');
      }
    } catch {
      setError('Could not download miscellaneous totals.');
    }
  }

  return (
    <main className="page-container">
      <PageHeader
        title="Miscellaneous Totals"
        subtitle="Review current versioned store-day totals categorized for operational analysis."
        actions={
          <Button onClick={() => void download()} disabled={!result?.total}>
            Download CSV
          </Button>
        }
      />
      <Card>
        <form onSubmit={apply} className="toolbar" style={{ alignItems: 'end', flexWrap: 'wrap' }}>
          <StoreSelect value={store} onChange={setStore} emptyLabel="All stores" />
          <label className="field">
            <span>From</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <label className="field">
            <span>Total or dimension</span>
            <input
              maxLength={120}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search miscellaneous totals"
            />
          </label>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Loading…' : 'View totals'}
          </Button>
        </form>
      </Card>
      <SummarySavedViews
        scope="misc_totals"
        label="Miscellaneous Totals"
        filters={{ store, dateFrom, dateTo, search }}
        onApply={applyFilters}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="warning">{notice}</Alert>}
      {result && (
        <>
          <MetricGrid>
            <MetricTile label="Current total rows" value={formatNumber(result.total)} />
            <MetricTile label="Store days" value={formatNumber(result.store_days)} />
            <MetricTile
              label="Days needing re-total"
              value={formatNumber(result.stale_store_days)}
            />
          </MetricGrid>
          <Card>
            <DataTable
              columns={columns}
              rows={busy ? null : result.items}
              rowKey={(row) =>
                `${row.store_id}:${row.business_date}:${row.total_name}:${row.level}:${row.dimension ?? ''}:${row.rollup_key ?? ''}:${row.currency}`
              }
              emptyTitle="No miscellaneous totals found"
              emptyHint="Change the store, date range, or search and try again."
              footer={
                <Pager
                  page={page}
                  pages={result.pages}
                  total={result.total}
                  pageSize={50}
                  pageSizes={[50]}
                  onPage={(next) => void load(next)}
                  onPageSize={() => {}}
                />
              }
            />
          </Card>
        </>
      )}
      {!result && !error && (
        <Card>
          <p>Select a store or date range to review its current miscellaneous totals.</p>
        </Card>
      )}
    </main>
  );
}

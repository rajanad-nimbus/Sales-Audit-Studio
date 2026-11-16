'use client';

import { formatNumber } from '@/lib/preferences';
import { useEffect, useRef, useState } from 'react';
import { api, money } from '@/lib/api';
import { StoreSelect } from '@/components/StoreSelect';
import { Alert, Button, Card, DataTable, PageHeader, Pager } from '@/components/ui';
import type { Column } from '@/components/ui';
import { SummarySavedViews, type SummaryFilters } from '@/components/SummarySavedViews';

interface TenderRow {
  tender_type: string;
  currency: string;
  tender_count: number;
  transaction_count: number;
  received_amount: string;
  refunded_amount: string;
  net_amount: string;
}
interface Result {
  items: TenderRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

const columns: Column<TenderRow>[] = [
  { key: 'tender', header: 'Tender type', primary: true, render: (row) => row.tender_type },
  { key: 'currency', header: 'Currency', render: (row) => row.currency },
  {
    key: 'transactions',
    header: 'Transactions',
    numeric: true,
    render: (row) => formatNumber(row.transaction_count),
  },
  {
    key: 'tenders',
    header: 'Tender entries',
    numeric: true,
    render: (row) => formatNumber(row.tender_count),
  },
  {
    key: 'received',
    header: 'Received',
    numeric: true,
    render: (row) => money(row.received_amount, row.currency),
  },
  {
    key: 'refunds',
    header: 'Refunds',
    numeric: true,
    render: (row) => money(row.refunded_amount, row.currency),
  },
  {
    key: 'net',
    header: 'Net',
    numeric: true,
    render: (row) => money(row.net_amount, row.currency),
  },
];

export default function TenderSummaryPage() {
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
      const { data } = await api.get<Result>('/api/tender-summary', {
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
      setApplied(filters);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(detail || 'Tender summary could not be loaded.');
      setResult(null);
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

  function apply(event: React.FormEvent) {
    event.preventDefault();
    applyFilters({ store, dateFrom, dateTo, search: search.trim() });
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
    setError('');
    setNotice('');
    try {
      const response = await api.get('/api/tender-summary', {
        params: {
          store_id: applied.store || undefined,
          date_from: applied.dateFrom || undefined,
          date_to: applied.dateTo || undefined,
          q: applied.search || undefined,
          format: 'csv',
        },
        responseType: 'blob',
      });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(response.data);
      link.download = 'tender_summary.csv';
      link.click();
      URL.revokeObjectURL(link.href);
      if (response.headers?.['x-report-truncated'] === 'true') {
        setNotice('The export reached 100,000 rows. Narrow the date range to download the rest.');
      }
    } catch {
      setError('Tender summary CSV could not be downloaded.');
    }
  }

  return (
    <main className="page-container">
      <PageHeader
        title="Tender Summary"
        subtitle="Compare payment and refund activity by tender type and currency."
        actions={
          <Button onClick={() => void download()} disabled={!result || result.total === 0}>
            Download CSV
          </Button>
        }
      />
      <Card>
        <form onSubmit={apply} className="toolbar" style={{ alignItems: 'end', flexWrap: 'wrap' }}>
          <StoreSelect value={store} onChange={setStore} emptyLabel="All stores" />
          <label className="field">
            <span>From</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(event) => setDateFrom(event.target.value)}
            />
          </label>
          <label className="field">
            <span>To</span>
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
          <label className="field">
            <span>Tender, transaction type, or reference</span>
            <input
              value={search}
              maxLength={120}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search payment activity"
            />
          </label>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Loading…' : 'View summary'}
          </Button>
        </form>
      </Card>
      <SummarySavedViews
        scope="tender_summary"
        label="Tender Summary"
        filters={{ store, dateFrom, dateTo, search }}
        onApply={applyFilters}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="warning">{notice}</Alert>}
      {result && (
        <Card>
          <DataTable
            columns={columns}
            rows={busy ? null : result.items}
            rowKey={(row) => `${row.tender_type}:${row.currency}`}
            emptyTitle="No tender activity found"
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
      )}
      {!result && !error && (
        <Card>
          <p>Select filters to review payment and refund activity across transactions.</p>
        </Card>
      )}
    </main>
  );
}

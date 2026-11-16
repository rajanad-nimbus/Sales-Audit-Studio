'use client';

import { formatNumber } from '@/lib/preferences';
import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
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
import { SummarySavedViews } from '@/components/SummarySavedViews';

interface ItemRow {
  item_code: string;
  description: string | null;
  currency: string;
  transaction_count: number;
  sold_quantity: string;
  returned_quantity: string;
  net_quantity: string;
  sold_amount: string;
  returned_amount: string;
  net_sales: string;
  net_tax: string;
}
interface CurrencyTotal {
  currency: string;
  sold_amount: string;
  returned_amount: string;
  net_sales: string;
  net_tax: string;
}
interface Result {
  items: ItemRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  totals_by_currency: CurrencyTotal[];
}
interface SummaryFilters {
  store: string;
  dateFrom: string;
  dateTo: string;
  search: string;
}

const quantity = (value: string) => formatNumber(Number(value), { maximumFractionDigits: 3 });

const columns = (filters: SummaryFilters): Column<ItemRow>[] => [
  { key: 'item', header: 'Item', primary: true, render: (row) => <code>{row.item_code}</code> },
  { key: 'description', header: 'Description', render: (row) => row.description || '—' },
  {
    key: 'transactions',
    header: 'Transactions',
    numeric: true,
    render: (row) => {
      const params = new URLSearchParams({ item_code: row.item_code });
      if (filters.store) params.set('store_id', filters.store);
      if (filters.dateFrom) params.set('date_from', filters.dateFrom);
      if (filters.dateTo) params.set('date_to', filters.dateTo);
      return (
        <Link href={`/transactions?${params.toString()}`}>
          {formatNumber(row.transaction_count)}
        </Link>
      );
    },
  },
  {
    key: 'sold',
    header: 'Units sold',
    numeric: true,
    render: (row) => quantity(row.sold_quantity),
  },
  {
    key: 'returned',
    header: 'Units returned',
    numeric: true,
    render: (row) => quantity(row.returned_quantity),
  },
  {
    key: 'net-qty',
    header: 'Net units',
    numeric: true,
    render: (row) => quantity(row.net_quantity),
  },
  {
    key: 'sales',
    header: 'Sales',
    numeric: true,
    render: (row) => money(row.sold_amount, row.currency),
  },
  {
    key: 'returns',
    header: 'Returns',
    numeric: true,
    render: (row) => money(row.returned_amount, row.currency),
  },
  {
    key: 'net',
    header: 'Net sales',
    numeric: true,
    render: (row) => money(row.net_sales, row.currency),
  },
  {
    key: 'tax',
    header: 'Net tax',
    numeric: true,
    render: (row) => money(row.net_tax, row.currency),
  },
];

export default function ItemSummaryPage() {
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
  const [applied, setApplied] = useState<SummaryFilters>({
    store: '',
    dateFrom: '',
    dateTo: '',
    search: '',
  });
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const autoLoaded = useRef(false);

  async function load(nextPage = 1, filters = applied) {
    setBusy(true);
    setError('');
    try {
      const { data } = await api.get<Result>('/api/item-summary', {
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
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(detail || 'Item summary could not be loaded.');
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
      const response = await api.get('/api/item-summary', {
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
      link.download = 'item_summary.csv';
      link.click();
      URL.revokeObjectURL(link.href);
      if (response.headers?.['x-report-truncated'] === 'true') {
        setNotice('The export reached 100,000 rows. Narrow the date range to download the rest.');
      }
    } catch {
      setError('Item summary CSV could not be downloaded.');
    }
  }

  return (
    <main className="page-container">
      <PageHeader
        title="Item Summary"
        subtitle="Review sold and returned quantities, net sales, and tax by item across audited transactions."
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
            <span>Item or description</span>
            <input
              value={search}
              maxLength={120}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search items"
            />
          </label>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Loading…' : 'View summary'}
          </Button>
        </form>
      </Card>
      <SummarySavedViews
        scope="item_summary"
        label="Item Summary"
        filters={{ store, dateFrom, dateTo, search }}
        onApply={applyFilters}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="warning">{notice}</Alert>}
      {result && (
        <>
          <MetricGrid>
            <MetricTile label="Item and currency rows" value={formatNumber(result.total)} />
            {result.totals_by_currency.map((total) => (
              <MetricTile
                key={total.currency}
                label={`Net sales · ${total.currency}`}
                value={money(total.net_sales, total.currency)}
              />
            ))}
          </MetricGrid>
          {result.totals_by_currency.length > 0 && (
            <Card>
              <div className="table-container">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Currency</th>
                      <th className="num">Sales</th>
                      <th className="num">Returns</th>
                      <th className="num">Net sales</th>
                      <th className="num">Net tax</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.totals_by_currency.map((total) => (
                      <tr key={total.currency}>
                        <td>{total.currency}</td>
                        <td className="num">{money(total.sold_amount, total.currency)}</td>
                        <td className="num">{money(total.returned_amount, total.currency)}</td>
                        <td className="num">{money(total.net_sales, total.currency)}</td>
                        <td className="num">{money(total.net_tax, total.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          <Card>
            <DataTable
              columns={columns(applied)}
              rows={busy ? null : result.items}
              rowKey={(row) => `${row.item_code}:${row.description ?? ''}:${row.currency}`}
              emptyTitle="No item activity found"
              emptyHint="Change the store, date range, or item search and try again."
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
          <p>Select filters and view a summary of the matching item activity.</p>
        </Card>
      )}
    </main>
  );
}

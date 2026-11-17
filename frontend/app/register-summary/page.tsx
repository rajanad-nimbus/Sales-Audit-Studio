'use client';

import { formatNumber } from '@/lib/preferences';
import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { StoreSelect } from '@/components/StoreSelect';
import { Alert, Button, Card, DataTable, PageHeader, Pager, Segmented } from '@/components/ui';
import type { Column } from '@/components/ui';
import { SummarySavedViews, type SummaryFilters } from '@/components/SummarySavedViews';

type GroupBy = 'register' | 'cashier';
interface SummaryRow {
  dimension: string | null;
  currency: string;
  transaction_count: number;
  exception_count: number;
  unbalanced_count: number;
  sales_amount: string;
  return_amount: string;
  net_amount: string;
  register_event_count: number;
  flagged_event_count: number;
  cash_declaration_count: number;
  out_of_balance_count: number;
  short_amount: string;
  over_amount: string;
}
interface Result {
  store_id: string;
  business_date: string;
  group_by: GroupBy;
  items: SummaryRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

const transactionHref = (
  row: SummaryRow,
  group: GroupBy,
  store: string,
  businessDate: string,
  extra: { status?: string; detail_status?: string } = {}
) => {
  if (!row.dimension) return null;
  const params = new URLSearchParams({
    store_id: store,
    date_from: businessDate,
    date_to: businessDate,
    [group === 'register' ? 'register_id' : 'cashier_id']: row.dimension,
    ...extra,
  });
  return `/transactions?${params.toString()}`;
};

const registerEventHref = (
  row: SummaryRow,
  group: GroupBy,
  store: string,
  businessDate: string,
  flagged = false
) => {
  if (!row.dimension) return null;
  const params = new URLSearchParams({
    store_id: store,
    date_from: businessDate,
    date_to: businessDate,
    [group === 'register' ? 'register_id' : 'cashier_id']: row.dimension,
    ...(flagged ? { flagged: '1' } : {}),
  });
  return `/register-events?${params.toString()}`;
};

const columns = (group: GroupBy, store: string, businessDate: string): Column<SummaryRow>[] => [
  {
    key: 'dimension',
    header: group === 'register' ? 'Register' : 'Cashier',
    primary: true,
    csvValue: (row) => row.dimension || 'Unassigned',
    render: (row) => row.dimension || 'Unassigned',
  },
  {
    key: 'currency',
    header: 'Currency',
    csvValue: (row) => row.currency,
    render: (row) => row.currency,
  },
  {
    key: 'transactions',
    header: 'Transactions',
    numeric: true,
    csvValue: (row) => row.transaction_count,
    render: (row) => {
      const href = transactionHref(row, group, store, businessDate);
      return href ? (
        <Link href={href}>{formatNumber(row.transaction_count)}</Link>
      ) : (
        formatNumber(row.transaction_count)
      );
    },
  },
  {
    key: 'exceptions',
    header: 'Exceptions',
    numeric: true,
    csvValue: (row) => row.exception_count,
    render: (row) => {
      const href = transactionHref(row, group, store, businessDate, { status: 'Exception' });
      return href && row.exception_count > 0 ? (
        <Link href={href}>{formatNumber(row.exception_count)}</Link>
      ) : (
        formatNumber(row.exception_count)
      );
    },
  },
  {
    key: 'unbalanced',
    header: 'Unbalanced',
    numeric: true,
    csvValue: (row) => row.unbalanced_count,
    render: (row) => {
      const href = transactionHref(row, group, store, businessDate, {
        detail_status: 'Unbalanced',
      });
      return href && row.unbalanced_count > 0 ? (
        <Link href={href}>{formatNumber(row.unbalanced_count)}</Link>
      ) : (
        formatNumber(row.unbalanced_count)
      );
    },
  },
  {
    key: 'sales',
    header: 'Sales',
    numeric: true,
    csvValue: (row) => `${row.sales_amount} ${row.currency}`,
    render: (row) => money(row.sales_amount, row.currency),
  },
  {
    key: 'returns',
    header: 'Returns',
    numeric: true,
    csvValue: (row) => `${row.return_amount} ${row.currency}`,
    render: (row) => money(row.return_amount, row.currency),
  },
  {
    key: 'net',
    header: 'Net sales',
    numeric: true,
    csvValue: (row) => `${row.net_amount} ${row.currency}`,
    render: (row) => money(row.net_amount, row.currency),
  },
  {
    key: 'events',
    header: 'Register events',
    numeric: true,
    csvValue: (row) => row.register_event_count,
    render: (row) => {
      const href = registerEventHref(row, group, store, businessDate);
      return href && row.register_event_count > 0 ? (
        <Link href={href}>{formatNumber(row.register_event_count)}</Link>
      ) : (
        formatNumber(row.register_event_count)
      );
    },
  },
  {
    key: 'flagged',
    header: 'Flagged events',
    numeric: true,
    csvValue: (row) => row.flagged_event_count,
    render: (row) => {
      const href = registerEventHref(row, group, store, businessDate, true);
      return href && row.flagged_event_count > 0 ? (
        <Link href={href}>{formatNumber(row.flagged_event_count)}</Link>
      ) : (
        formatNumber(row.flagged_event_count)
      );
    },
  },
  {
    key: 'declarations',
    header: 'Cash declarations',
    numeric: true,
    csvValue: (row) => row.cash_declaration_count,
    render: (row) => formatNumber(row.cash_declaration_count),
  },
  {
    key: 'cash-errors',
    header: 'Out of balance',
    numeric: true,
    csvValue: (row) => row.out_of_balance_count,
    render: (row) => formatNumber(row.out_of_balance_count),
  },
  {
    key: 'short',
    header: 'Short',
    numeric: true,
    csvValue: (row) => `${row.short_amount} ${row.currency}`,
    render: (row) => money(row.short_amount, row.currency),
  },
  {
    key: 'over',
    header: 'Over',
    numeric: true,
    csvValue: (row) => `${row.over_amount} ${row.currency}`,
    render: (row) => money(row.over_amount, row.currency),
  },
];

export default function RegisterSummaryPage() {
  const [initialQuery] = useState(() => {
    const query =
      typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
    return {
      store: query?.get('store_id') ?? '',
      businessDate: query?.get('business_date') ?? new Date().toISOString().slice(0, 10),
      groupBy: (query?.get('group_by') === 'cashier' ? 'cashier' : 'register') as GroupBy,
      source: query?.get('source') === 'store-day' ? 'store-day' : '',
    };
  });
  const [store, setStore] = useState(initialQuery.store);
  const [businessDate, setBusinessDate] = useState(initialQuery.businessDate);
  const [groupBy, setGroupBy] = useState<GroupBy>(initialQuery.groupBy);
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [exportError, setExportError] = useState('');
  const [exportNotice, setExportNotice] = useState('');
  const [linkCopied, setLinkCopied] = useState(false);
  const [copyError, setCopyError] = useState('');
  const autoLoaded = useRef(false);

  async function load(
    nextPage = 1,
    filters: SummaryFilters = { store, dateFrom: '', dateTo: '', search: '', businessDate, groupBy }
  ) {
    const nextStore = filters.store;
    const nextBusinessDate = filters.businessDate || businessDate;
    const nextGroupBy = filters.groupBy || groupBy;
    if (!nextStore || !nextBusinessDate) return;
    setBusy(true);
    setError('');
    try {
      const { data } = await api.get<Result>('/api/register-summary', {
        params: {
          store_id: nextStore,
          business_date: nextBusinessDate,
          group_by: nextGroupBy,
          page: nextPage,
          page_size: 50,
        },
      });
      setResult(data);
      setPage(nextPage);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(detail || 'Cashier/register summary could not be loaded.');
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!initialQuery.store || autoLoaded.current) return;
    autoLoaded.current = true;
    void load(1);
  }, [initialQuery, load]);

  function apply(event: React.FormEvent) {
    event.preventDefault();
    applyFilters({ store, dateFrom: '', dateTo: '', search: '', businessDate, groupBy });
  }

  function applyFilters(filters: SummaryFilters) {
    const nextGroupBy = filters.groupBy ?? 'register';
    const nextBusinessDate = filters.businessDate ?? filters.dateFrom;
    setLinkCopied(false);
    setStore(filters.store);
    setBusinessDate(nextBusinessDate);
    setGroupBy(nextGroupBy);
    const params = new URLSearchParams({
      store_id: filters.store,
      business_date: nextBusinessDate,
      group_by: nextGroupBy,
    });
    if (
      initialQuery.source === 'store-day' &&
      filters.store === initialQuery.store &&
      nextBusinessDate === initialQuery.businessDate
    ) {
      params.set('source', 'store-day');
    }
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`);
    void load(1, { ...filters, businessDate: nextBusinessDate, groupBy: nextGroupBy });
  }

  async function copySummaryLink() {
    setCopyError('');
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      setCopyError('Could not copy this summary link.');
    }
  }

  return (
    <main className="page-container">
      {initialQuery.source === 'store-day' &&
        store === initialQuery.store &&
        businessDate === initialQuery.businessDate && (
          <Link
            href={`/store-days/${encodeURIComponent(store)}/${encodeURIComponent(businessDate)}?tab=analysis`}
            className="back-link"
          >
            ← Store Day · {store} · {businessDate}
          </Link>
        )}
      <PageHeader
        title="Cashier / Register Summary"
        subtitle="Review transactions, exceptions, register events, and cash over/short by cashier or register."
        actions={
          result && (
            <>
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => void copySummaryLink()}
                title="Copy a link to these store-day filters and grouping."
              >
                {linkCopied ? 'Link copied' : 'Copy summary link'}
              </Button>
            </>
          )
        }
      />
      <Card>
        <form onSubmit={apply} className="toolbar" style={{ alignItems: 'end', flexWrap: 'wrap' }}>
          <StoreSelect
            value={store}
            onChange={(value) => {
              setStore(value);
              setResult(null);
            }}
          />
          <label className="field">
            <span>Business day</span>
            <input
              required
              type="date"
              value={businessDate}
              onChange={(event) => {
                setBusinessDate(event.target.value);
                setResult(null);
              }}
            />
          </label>
          <Segmented
            value={groupBy}
            onChange={(value) => {
              setGroupBy(value as GroupBy);
              setResult(null);
              setPage(1);
            }}
            options={[
              { value: 'register', label: 'By register' },
              { value: 'cashier', label: 'By cashier' },
            ]}
          />
          <Button type="submit" variant="primary" disabled={busy || !store}>
            {busy ? 'Loading…' : 'Review store day'}
          </Button>
        </form>
      </Card>
      <SummarySavedViews
        scope="register_summary"
        label="Cashier / Register Summary"
        filters={{ store, dateFrom: '', dateTo: '', search: '', businessDate, groupBy }}
        onApply={applyFilters}
        saveDisabled={!store}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {exportError && (
        <Alert tone="error" onDismiss={() => setExportError('')}>
          {exportError}
        </Alert>
      )}
      {exportNotice && (
        <Alert tone="warning" onDismiss={() => setExportNotice('')}>
          {exportNotice}
        </Alert>
      )}
      {copyError && (
        <Alert tone="error" onDismiss={() => setCopyError('')}>
          {copyError}
        </Alert>
      )}
      {result && (
        <Card>
          <p className="hint">
            {result.store_id} · {result.business_date} · amounts remain separated by currency
          </p>
          <DataTable
            columns={columns(groupBy, store, businessDate)}
            rows={busy ? null : result.items}
            csvFileName="register-summary-visible.csv"
            rowKey={(row) => `${row.dimension ?? 'unassigned'}:${row.currency}`}
            emptyTitle="No cashier or register activity found"
            emptyHint="Choose another store day to review its transaction and register-event activity."
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
          <p>Choose a store and business day to review activity by register or cashier.</p>
        </Card>
      )}
    </main>
  );
}

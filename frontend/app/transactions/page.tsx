'use client';

import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';
import {
  Alert,
  Button,
  DataTable,
  EmptyState,
  Field,
  PageHeader,
  Pager,
  useNameDialog,
} from '@/components/ui';
import type { Column } from '@/components/ui';
import { TransactionBrief } from '@/components/transactions/TransactionBrief';

interface Row {
  id: string;
  business_date: string;
  event_timestamp: string;
  store_id: string;
  register_id: string | null;
  cashier_id: string | null;
  salesperson_id: string | null;
  source_system: string;
  source_record_id: string | null;
  transaction_type: string;
  tender_type: string | null;
  payment_reference: string | null;
  amount: string;
  currency: string;
  reconciliation_status: string;
  item_count: number | null;
  detail_status: string;
  original_reference: string | null;
}
interface Facets {
  stores: string[];
  sources: string[];
  types: string[];
  tenders: string[];
  statuses: string[];
  detail_statuses: string[];
  date_min: string | null;
  date_max: string | null;
}
interface Result {
  items: Row[];
  total: number;
  page: number;
  pages: number;
  by_status: Record<string, number>;
  net_amount: string;
}
interface F {
  q: string;
  store_id: string;
  source_system: string;
  transaction_type: string;
  tender_type: string;
  tender_entry_type: string;
  currency: string;
  status: string;
  detail_status: string;
  register_id: string;
  cashier_id: string;
  salesperson_id: string;
  item_code: string;
  item_type: string;
  date_from: string;
  date_to: string;
}
type TransactionSort = { sort: string; dir: 'asc' | 'desc' };
interface SavedTransactionView {
  name: string;
  f: Partial<F>;
  sort: TransactionSort;
  pageSize: 25 | 50 | 100 | 200;
}
const SORT_FIELDS = new Set([
  'business_date',
  'event_timestamp',
  'amount',
  'store',
  'type',
  'status',
  'tender',
  'source',
]);
const EMPTY: F = {
  q: '',
  store_id: '',
  source_system: '',
  transaction_type: '',
  tender_type: '',
  tender_entry_type: '',
  currency: '',
  status: '',
  detail_status: '',
  register_id: '',
  cashier_id: '',
  salesperson_id: '',
  item_code: '',
  item_type: '',
  date_from: '',
  date_to: '',
};
const ADVANCED_FILTERS: (keyof F)[] = [
  'source_system',
  'transaction_type',
  'tender_type',
  'tender_entry_type',
  'currency',
  'detail_status',
  'register_id',
  'cashier_id',
  'salesperson_id',
  'item_code',
  'item_type',
];

export const statusTone = (s: string) =>
  s === 'Matched' ? 'badge-success' : s === 'Exception' ? 'badge-error' : 'badge-warning';

const COLUMNS: Column<Row>[] = [
  {
    key: 'date',
    header: 'Business date',
    sortKey: 'business_date',
    nowrap: true,
    render: (r) => r.business_date,
    csvValue: (r) => r.business_date,
  },
  {
    key: 'store',
    header: 'Store',
    sortKey: 'store',
    render: (r) => r.store_id,
    csvValue: (r) => r.store_id,
  },
  {
    key: 'register-cashier',
    header: 'Register / cashier',
    render: (r) => `${r.register_id ?? '—'} / ${r.cashier_id ?? '—'}`,
    csvValue: (r) => `${r.register_id ?? ''} / ${r.cashier_id ?? ''}`,
  },
  {
    key: 'salesperson',
    header: 'Salesperson',
    render: (r) => r.salesperson_id ?? '—',
    csvValue: (r) => r.salesperson_id,
  },
  {
    key: 'source',
    header: 'Source',
    sortKey: 'source',
    render: (r) => r.source_system,
    csvValue: (r) => r.source_system,
  },
  {
    key: 'type',
    header: 'Type',
    sortKey: 'type',
    render: (r) => r.transaction_type,
    csvValue: (r) => r.transaction_type,
  },
  {
    key: 'tender',
    header: 'Tender',
    sortKey: 'tender',
    render: (r) => r.tender_type ?? '—',
    csvValue: (r) => r.tender_type,
  },
  {
    key: 'amount',
    header: 'Amount',
    sortKey: 'amount',
    numeric: true,
    render: (r) => money(r.amount),
    csvValue: (r) => `${r.currency} ${r.amount}`,
  },
  {
    key: 'status',
    header: 'Status',
    sortKey: 'status',
    csvValue: (r) => r.reconciliation_status,
    render: (r) => (
      <span className={`badge badge-sm ${statusTone(r.reconciliation_status)}`}>
        {r.reconciliation_status}
      </span>
    ),
  },
  {
    key: 'items',
    header: 'Items',
    csvValue: (r) =>
      r.detail_status === 'No detail'
        ? 'No detail'
        : `${r.item_count ?? 0}${r.detail_status === 'Unbalanced' ? ' (Unbalanced)' : ''}`,
    render: (r) =>
      r.detail_status === 'No detail' ? (
        <span className="act-sub">No detail</span>
      ) : (
        <>
          {r.item_count ?? 0}
          {r.detail_status === 'Unbalanced' && (
            <span
              className="badge badge-sm badge-warning"
              style={{ marginLeft: '.35rem' }}
              title="Lines, tax and discounts do not add up to the amount, or the tenders do not cover it"
            >
              Unbalanced
            </span>
          )}
        </>
      ),
  },
  {
    key: 'ref',
    header: 'Reference',
    stopRowClick: true,
    csvValue: (r) => r.payment_reference ?? r.source_record_id ?? '',
    render: (r) => (
      <Link href={`/transactions/${r.id}`}>
        {r.payment_reference ?? r.source_record_id ?? 'Open'}
      </Link>
    ),
  },
];

export default function TransactionsPage() {
  const [f, setF] = useState<F>(EMPTY);
  const [qd, setQd] = useState('');
  const [sort, setSort] = useState<TransactionSort>({ sort: 'business_date', dir: 'desc' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  // The row open under the table, if any. Cleared whenever a different page of rows arrives.
  const [expanded, setExpanded] = useState<string | null>(null);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [views, setViews] = useState<SavedTransactionView[]>([]);
  const [viewsError, setViewsError] = useState(false);
  const viewSaveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const seq = useRef(0);

  // Filters live in the URL so a view can be shared (the case page links here this way).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const next = { ...EMPTY };
    (Object.keys(EMPTY) as (keyof F)[]).forEach((k) => {
      next[k] = p.get(k) ?? '';
    });
    setF(next);
    setShowAdvanced(ADVANCED_FILTERS.some((key) => !!next[key]));
    setQd(next.q);
    const requestedSort = p.get('sort');
    if (requestedSort && SORT_FIELDS.has(requestedSort)) {
      setSort({
        sort: requestedSort,
        dir: p.get('dir') === 'asc' ? 'asc' : 'desc',
      });
    }
    const requestedPageSize = Number(p.get('page_size'));
    if ([25, 50, 100, 200].includes(requestedPageSize)) setPageSize(requestedPageSize);
    setReady(true);
    api
      .get('/api/transactions/facets')
      .then((r) => setFacets(r.data))
      .catch(() => {});
    let localViews: SavedTransactionView[] = [];
    try {
      localViews = JSON.parse(localStorage.getItem('nimbus_transaction_views') || '[]');
    } catch {
      /* ignore */
    }
    api
      .get<{ views: SavedTransactionView[] }>('/api/me/saved-views/transactions')
      .then(async ({ data }) => {
        if (data.views.length === 0 && localViews.length > 0) {
          setViews(localViews);
          await api.put('/api/me/saved-views/transactions', { views: localViews });
        } else {
          setViews(data.views);
        }
        setViewsError(false);
      })
      .catch(() => {
        setViews(localViews);
        setViewsError(true);
      });
  }, []);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    (Object.keys(f) as (keyof F)[]).forEach((k) => {
      const v = k === 'q' ? qd : f[k];
      if (v) p.set(k, v);
    });
    if (sort.sort !== 'business_date') p.set('sort', sort.sort);
    if (sort.dir !== 'desc') p.set('dir', sort.dir);
    if (pageSize !== 50) p.set('page_size', String(pageSize));
    const s = p.toString();
    history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
  }, [ready, f, qd, sort, pageSize]);
  useEffect(() => {
    const t = setTimeout(() => setQd(f.q), 300);
    return () => clearTimeout(t);
  }, [f.q]);
  useEffect(() => {
    setPage(1);
  }, [
    qd,
    f.store_id,
    f.source_system,
    f.transaction_type,
    f.tender_type,
    f.tender_entry_type,
    f.currency,
    f.status,
    f.detail_status,
    f.register_id,
    f.cashier_id,
    f.salesperson_id,
    f.item_code,
    f.item_type,
    f.date_from,
    f.date_to,
    sort,
    pageSize,
  ]);

  const load = useCallback(async () => {
    if (!ready) return;
    const mine = ++seq.current;
    const params: Record<string, string | number> = { ...sort, page, page_size: pageSize };
    (Object.keys(f) as (keyof F)[]).forEach((k) => {
      const v = k === 'q' ? qd : f[k];
      if (v) params[k] = v;
    });
    try {
      const { data } = await api.get('/api/transactions', { params });
      if (mine === seq.current) {
        setData(data);
        setExpanded(null);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current)
        setError(
          (e as { response?: { status?: number } })?.response?.status === 403
            ? 'Your role cannot view transactions.'
            : 'Could not load transactions.'
        );
    }
  }, [ready, f, qd, sort, page, pageSize]);
  useEffect(() => {
    load();
  }, [load]);
  useDataChanged(() => {
    load();
  });

  const set = (patch: Partial<F>) => setF((x) => ({ ...x, ...patch }));
  const persistViews = (next: SavedTransactionView[]) => {
    setViews(next);
    try {
      localStorage.setItem('nimbus_transaction_views', JSON.stringify(next));
    } catch {
      /* ignore */
    }
    viewSaveQueue.current = viewSaveQueue.current
      .catch(() => undefined)
      .then(() => api.put('/api/me/saved-views/transactions', { views: next }))
      .then(() => setViewsError(false))
      .catch(() => setViewsError(true));
  };
  const applySavedView = (view: SavedTransactionView) => {
    setF({ ...EMPTY, ...view.f });
    setQd(view.f.q ?? '');
    setSort(view.sort);
    setPageSize(view.pageSize);
    setPage(1);
    setExpanded(null);
  };
  const { ask: askViewName, dialog: viewNameDialog } = useNameDialog();
  const saveCurrentView = async () => {
    const name = await askViewName(
      'Name this transaction view',
      views.map((view) => view.name)
    );
    if (!name) return;
    const filters = { ...f, q: qd };
    const savedFilters = Object.fromEntries(
      Object.entries(filters).filter(([, value]) => value !== '')
    ) as Partial<F>;
    persistViews([
      ...views.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
      { name, f: savedFilters, sort, pageSize: pageSize as SavedTransactionView['pageSize'] },
    ]);
  };
  const filtered = (Object.keys(f) as (keyof F)[]).some((k) => (k === 'q' ? qd : f[k]));
  const advancedCount = ADVANCED_FILTERS.filter((key) => !!f[key]).length;
  const toggleSort = (k: string) =>
    setSort((s) =>
      s.sort === k
        ? { sort: k, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { sort: k, dir: k === 'business_date' || k === 'amount' ? 'desc' : 'asc' }
    );
  const statuses = data ? Object.entries(data.by_status).sort((a, b) => b[1] - a[1]) : [];
  const spreadTotal = statuses.reduce((n, [, c]) => n + c, 0);

  return (
    <div className="page-container transactions-page">
      {viewNameDialog}
      <PageHeader
        title="Transactions"
        subtitle="Find a transaction, review its details, and investigate related cases."
      />
      {exportNotice && (
        <Alert tone="warning" onDismiss={() => setExportNotice(null)}>
          {exportNotice}
        </Alert>
      )}
      {error && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={load}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}

      <div className="ac-tiles">
        <button className="ac-tile" aria-pressed={!f.status} onClick={() => set({ status: '' })}>
          <small>All transactions</small>
          <b>{formatNumber(spreadTotal)}</b>
          <em>{data ? `Net ${money(data.net_amount)}` : ' '}</em>
        </button>
        {statuses.map(([s, c]) => (
          <button
            key={s}
            className={`ac-tile ${s === 'Exception' && c > 0 ? 'bad' : ''}`}
            aria-pressed={f.status === s}
            onClick={() => set({ status: f.status === s ? '' : s })}
          >
            <small>{s}</small>
            <b>{formatNumber(c)}</b>
            <em>
              {s === 'Matched'
                ? 'Reconciled across sources'
                : s === 'Exception'
                  ? 'Raised as cases'
                  : 'Not matched yet'}
            </em>
          </button>
        ))}
      </div>

      <section className="transaction-filters" aria-label="Transaction filters">
        <div className="transaction-filter-primary">
          <Field label="Search transactions">
            <input
              className="grow"
              type="search"
              value={f.q}
              onChange={(e) => set({ q: e.target.value })}
              placeholder="Search transaction, cashier, vendor, invoice, or payment reference"
              aria-label="Search transactions"
            />
          </Field>
          <Field label="Store">
            <select
              aria-label="Store"
              value={f.store_id}
              onChange={(e) => set({ store_id: e.target.value })}
            >
              <option value="">All stores</option>
              {facets?.stores.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="From business date">
            <input
              type="date"
              aria-label="From business date"
              title="From business date"
              value={f.date_from}
              min={facets?.date_min ?? undefined}
              max={f.date_to || facets?.date_max || undefined}
              onChange={(e) => set({ date_from: e.target.value })}
            />
          </Field>
          <Field label="To business date">
            <input
              type="date"
              aria-label="To business date"
              title="To business date"
              value={f.date_to}
              min={f.date_from || facets?.date_min || undefined}
              max={facets?.date_max ?? undefined}
              onChange={(e) => set({ date_to: e.target.value })}
            />
          </Field>
        </div>
        <div className="transaction-filter-actions">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={showAdvanced}
            aria-controls="transaction-advanced-filters"
            onClick={() => setShowAdvanced((open) => !open)}
          >
            {showAdvanced ? 'Fewer filters' : 'More filters'}
            {advancedCount > 0 ? ` (${advancedCount} active)` : ''}
          </Button>
          {filtered && (
            <Button
              size="sm"

              onClick={() => {
                setF(EMPTY);
                setQd('');
              }}
            >
              Clear filters
            </Button>
          )}
          <span className="toolbar-count">
            {data ? `${formatNumber(data.total)} ${filtered ? 'matching' : 'total'}` : ''}
          </span>
        </div>
        <div
          id="transaction-advanced-filters"
          className="transaction-filter-advanced"
          hidden={!showAdvanced}
        >
          <Field label="Source">
            <select
              aria-label="Source"
              value={f.source_system}
              onChange={(e) => set({ source_system: e.target.value })}
            >
              <option value="">All sources</option>
              {facets?.sources.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Type">
            <select
              aria-label="Type"
              value={f.transaction_type}
              onChange={(e) => set({ transaction_type: e.target.value })}
            >
              <option value="">All types</option>
              {facets?.types.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Tender">
            <select
              aria-label="Tender"
              value={f.tender_type}
              onChange={(e) => set({ tender_type: e.target.value })}
            >
              <option value="">All tenders</option>
              {facets?.tenders.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Tender entry type">
            <input
              value={f.tender_entry_type}
              maxLength={50}
              onChange={(e) => set({ tender_entry_type: e.target.value })}
              placeholder="Exact split-tender type"
            />
          </Field>
          <Field label="Currency">
            <input
              value={f.currency}
              maxLength={3}
              onChange={(e) => set({ currency: e.target.value.toUpperCase() })}
              placeholder="USD"
            />
          </Field>
          <Field label="Item type">
            <input
              value={f.item_type}
              maxLength={50}
              onChange={(e) => set({ item_type: e.target.value })}
              placeholder="Exact item type"
            />
          </Field>
          <Field label="Line detail">
            <select
              aria-label="Line detail"
              value={f.detail_status}
              onChange={(e) => set({ detail_status: e.target.value })}
            >
              <option value="">Any detail</option>
              {facets?.detail_statuses.map((s) => (
                <option key={s} value={s}>
                  {s === 'No detail' ? 'No line detail' : s}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Register ID">
            <input
              aria-label="Register ID"
              value={f.register_id}
              onChange={(e) => set({ register_id: e.target.value })}
              placeholder="Register"
            />
          </Field>
          <Field label="Cashier ID">
            <input
              aria-label="Cashier ID"
              value={f.cashier_id}
              onChange={(e) => set({ cashier_id: e.target.value })}
              placeholder="Cashier"
            />
          </Field>
          <Field label="Salesperson ID">
            <input
              aria-label="Salesperson ID"
              value={f.salesperson_id}
              onChange={(e) => set({ salesperson_id: e.target.value })}
              placeholder="Salesperson"
            />
          </Field>
          <Field label="Item code">
            <input
              aria-label="Item code"
              value={f.item_code}
              onChange={(e) => set({ item_code: e.target.value })}
              placeholder="Item code"
            />
          </Field>
        </div>
      </section>

      {viewsError && (
        <Alert tone="warning">
          Saved views are available on this device, but could not sync to your account.
        </Alert>
      )}
      <div className="ac-presets" aria-label="Saved transaction views">
        {views.map((view, index) => (
          <span key={`${view.name}-${index}`} className="ac-chip saved">
            <button onClick={() => applySavedView(view)}>{view.name}</button>
            <button
              aria-label={`Delete view ${view.name}`}
              onClick={() => persistViews(views.filter((_, row) => row !== index))}
            >
              ✕
            </button>
          </span>
        ))}
        <button className="ac-chip add" onClick={saveCurrentView}>
          + Save current view
        </button>
      </div>

      {!data ? (
        <SkeletonRows rows={8} label="Loading transactions" />
      ) : data.items.length === 0 ? (
        <EmptyState title="No transactions match">
          <p>
            {filtered
              ? 'Try clearing a filter.'
              : 'Transactions appear after a feed is ingested and normalized. Run the batch on the Imports page.'}
          </p>
          {filtered && (
            <Button
              size="sm"

              onClick={() => {
                setF(EMPTY);
                setQd('');
              }}
            >
              Clear filters
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTable
          columns={COLUMNS}
          csvFileName="transactions-visible.csv"
          rows={data.items}
          rowKey={(r) => r.id}
          onRowClick={(r) => setExpanded((open) => (open === r.id ? null : r.id))}
          expandedKey={expanded}
          renderDetail={(r) => <TransactionBrief id={r.id} />}
          sort={{ key: sort.sort, dir: sort.dir as 'asc' | 'desc' }}
          onSort={toggleSort}
          footer={
            <Pager
              page={data.page}
              pages={data.pages}
              total={data.total}
              pageSize={pageSize}
              pageSizes={[25, 50, 100, 200]}
              onPage={setPage}
              onPageSize={setPageSize}
            />
          }
        />
      )}
    </div>
  );
}

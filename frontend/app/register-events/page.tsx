'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';
import { Alert, Badge, Button, DataTable, EmptyState, PageHeader, Pager } from '@/components/ui';
import type { Column } from '@/components/ui';

interface Row {
  id: string;
  business_date: string;
  event_time: string;
  store_id: string;
  register_id: string | null;
  cashier_id: string | null;
  event_type: string;
  sub_type: string | null;
  transaction_number: string;
  original_transaction_number: string | null;
  reason_code: string | null;
  amount: string | null;
  currency: string;
  audit_code: string | null;
}
interface Facets {
  stores: string[];
  types: string[];
  date_min: string | null;
  date_max: string | null;
}
interface Result {
  items: Row[];
  total: number;
  page: number;
  pages: number;
  by_type: Record<string, number>;
  flagged: number;
}
interface F {
  q: string;
  store_id: string;
  event_type: string;
  flagged: string;
  date_from: string;
  date_to: string;
}
const EMPTY: F = { q: '', store_id: '', event_type: '', flagged: '', date_from: '', date_to: '' };

const TYPE_LABELS: Record<string, string> = {
  void: 'Void',
  post_void: 'Post-void',
  no_sale: 'No-sale',
  paid_in: 'Paid in',
  paid_out: 'Paid out',
  pull: 'Pull',
  loan: 'Loan',
  open: 'Open',
  close: 'Close',
  total: 'Total',
  day_close: 'Day close',
  condition: 'Condition',
  meter: 'Meter',
  term: 'Term',
  refund: 'Refund',
};
const typeLabel = (t: string) => TYPE_LABELS[t] ?? t;

const COLUMNS: Column<Row>[] = [
  {
    key: 'date',
    header: 'Business date',
    sortKey: 'business_date',
    nowrap: true,
    render: (r) => r.business_date,
  },
  {
    key: 'time',
    header: 'Time',
    sortKey: 'time',
    nowrap: true,
    render: (r) => r.event_time.slice(11, 16),
  },
  { key: 'store', header: 'Store', sortKey: 'store', render: (r) => r.store_id },
  { key: 'register', header: 'Register', render: (r) => r.register_id ?? '—' },
  { key: 'cashier', header: 'Cashier', render: (r) => r.cashier_id ?? '—' },
  {
    key: 'type',
    header: 'Event',
    sortKey: 'type',
    render: (r) => (
      <>
        {typeLabel(r.event_type)}
        {r.sub_type && <div className="act-sub">{r.sub_type}</div>}
      </>
    ),
  },
  {
    key: 'tran',
    header: 'Transaction',
    render: (r) => (
      <>
        {r.transaction_number === '0' ? '—' : r.transaction_number}
        {r.original_transaction_number && (
          <div className="act-sub">cancels {r.original_transaction_number}</div>
        )}
      </>
    ),
  },
  { key: 'reason', header: 'Reason', render: (r) => r.reason_code ?? '—' },
  {
    key: 'amount',
    header: 'Amount',
    sortKey: 'amount',
    numeric: true,
    render: (r) => (r.amount ? money(r.amount, r.currency) : '—'),
  },
  {
    key: 'audit',
    header: 'Audit',
    stopRowClick: true,
    render: (r) =>
      r.audit_code ? (
        <Link href={`/audit-errors?code=${encodeURIComponent(r.audit_code)}`}>
          <Badge variant="error" size="sm">
            {r.audit_code}
          </Badge>
        </Link>
      ) : (
        <span className="act-sub">—</span>
      ),
  },
];

export default function RegisterEventsPage() {
  const [f, setF] = useState<F>(EMPTY);
  const [qd, setQd] = useState('');
  const [sort, setSort] = useState({ sort: 'business_date', dir: 'desc' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [data, setData] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const seq = useRef(0);

  // Filters live in the URL so a view can be shared.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const next = { ...EMPTY };
    (Object.keys(EMPTY) as (keyof F)[]).forEach((k) => {
      next[k] = p.get(k) ?? '';
    });
    setF(next);
    setQd(next.q);
    setReady(true);
    api
      .get('/api/register-events/facets')
      .then((r) => setFacets(r.data))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    (Object.keys(f) as (keyof F)[]).forEach((k) => {
      const v = k === 'q' ? qd : f[k];
      if (v) p.set(k, v);
    });
    const s = p.toString();
    history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
  }, [ready, f, qd]);
  useEffect(() => {
    const t = setTimeout(() => setQd(f.q), 300);
    return () => clearTimeout(t);
  }, [f.q]);
  useEffect(() => {
    setPage(1);
  }, [qd, f.store_id, f.event_type, f.flagged, f.date_from, f.date_to, sort, pageSize]);

  const load = useCallback(async () => {
    if (!ready) return;
    const mine = ++seq.current;
    const params: Record<string, string | number | boolean> = {
      ...sort,
      page,
      page_size: pageSize,
    };
    (Object.keys(f) as (keyof F)[]).forEach((k) => {
      const v = k === 'q' ? qd : f[k];
      if (v) params[k] = k === 'flagged' ? true : v;
    });
    try {
      const { data } = await api.get('/api/register-events', { params });
      if (mine === seq.current) {
        setData(data);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current)
        setError(
          (e as { response?: { status?: number } })?.response?.status === 403
            ? 'Your role cannot view register events.'
            : 'Could not load register events.'
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
  const filtered = (Object.keys(f) as (keyof F)[]).some((k) => (k === 'q' ? qd : f[k]));
  const clear = () => {
    setF(EMPTY);
    setQd('');
  };
  const toggleSort = (k: string) =>
    setSort((s) =>
      s.sort === k
        ? { sort: k, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { sort: k, dir: k === 'business_date' || k === 'time' || k === 'amount' ? 'desc' : 'asc' }
    );
  const types = data ? Object.entries(data.by_type).sort((a, b) => b[1] - a[1]) : [];
  const typeTotal = types.reduce((n, [, c]) => n + c, 0);

  return (
    <div className="page-container">
      <PageHeader
        title="Register Events"
        subtitle="Till and drawer activity with no sale behind it: voids, no-sales, paid-in and paid-out, pulls and loans, and till, register and store opens and closes."
      />
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
        <button
          className="ac-tile"
          aria-pressed={!f.event_type}
          onClick={() => set({ event_type: '' })}
        >
          <small>All events</small>
          <b>{typeTotal.toLocaleString()}</b>
          <em>{data ? `${data.flagged.toLocaleString()} flagged by the audit` : ' '}</em>
        </button>
        {types.map(([t, c]) => (
          <button
            key={t}
            className="ac-tile"
            aria-pressed={f.event_type === t}
            onClick={() => set({ event_type: f.event_type === t ? '' : t })}
          >
            <small>{typeLabel(t)}</small>
            <b>{c.toLocaleString()}</b>
            <em> </em>
          </button>
        ))}
      </div>

      <div className="toolbar">
        <input
          className="grow"
          type="search"
          value={f.q}
          onChange={(e) => set({ q: e.target.value })}
          placeholder="Search cashier, register, transaction number or reason"
          aria-label="Search register events"
        />
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
        <select
          aria-label="Audit result"
          value={f.flagged}
          onChange={(e) => set({ flagged: e.target.value })}
        >
          <option value="">Any audit result</option>
          <option value="1">Flagged only</option>
        </select>
        <input
          type="date"
          aria-label="From business date"
          title="From business date"
          value={f.date_from}
          min={facets?.date_min ?? undefined}
          max={f.date_to || facets?.date_max || undefined}
          onChange={(e) => set({ date_from: e.target.value })}
        />
        <input
          type="date"
          aria-label="To business date"
          title="To business date"
          value={f.date_to}
          min={f.date_from || facets?.date_min || undefined}
          max={facets?.date_max ?? undefined}
          onChange={(e) => set({ date_to: e.target.value })}
        />
        {filtered && (
          <Button size="sm" onClick={clear}>
            Clear filters
          </Button>
        )}
        <span className="toolbar-count">
          {data ? `${data.total.toLocaleString()} ${filtered ? 'matching' : 'total'}` : ''}
        </span>
      </div>

      {!data ? (
        <SkeletonRows rows={8} label="Loading register events" />
      ) : data.items.length === 0 ? (
        <EmptyState title="No register events match">
          <p>
            {filtered
              ? 'Try clearing a filter.'
              : 'Register events appear after a POS log is ingested through the Data Pipeline.'}
          </p>
          {filtered && (
            <Button size="sm" onClick={clear}>
              Clear filters
            </Button>
          )}
        </EmptyState>
      ) : (
        <DataTable
          columns={COLUMNS}
          rows={data.items}
          rowKey={(r) => r.id}
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

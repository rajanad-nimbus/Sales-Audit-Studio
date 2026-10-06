'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, money } from '@/lib/api';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';

interface Row { id: string; business_date: string; event_timestamp: string; store_id: string; register_id: string | null; source_system: string; source_record_id: string | null; transaction_type: string; tender_type: string | null; payment_reference: string | null; amount: string; currency: string; reconciliation_status: string; item_count: number | null; detail_status: string; original_reference: string | null }
interface Facets { stores: string[]; sources: string[]; types: string[]; tenders: string[]; statuses: string[]; detail_statuses: string[]; date_min: string | null; date_max: string | null }
interface Result { items: Row[]; total: number; page: number; pages: number; by_status: Record<string, number>; net_amount: string }
interface F { q: string; store_id: string; source_system: string; transaction_type: string; tender_type: string; status: string; detail_status: string; date_from: string; date_to: string }
const EMPTY: F = { q: '', store_id: '', source_system: '', transaction_type: '', tender_type: '', status: '', detail_status: '', date_from: '', date_to: '' };
const SORTABLE: [string, string, boolean?][] = [['business_date', 'Business date'], ['store', 'Store'], ['source', 'Source'], ['type', 'Type'], ['tender', 'Tender'], ['amount', 'Amount', true], ['status', 'Status']];
export const statusTone = (s: string) => (s === 'Matched' ? 'badge-success' : s === 'Exception' ? 'badge-error' : 'badge-warning');

export default function TransactionsPage() {
  const router = useRouter();
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

  // Filters live in the URL so a view can be shared (the case page links here this way).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const next = { ...EMPTY }; (Object.keys(EMPTY) as (keyof F)[]).forEach((k) => { next[k] = p.get(k) ?? ''; });
    setF(next); setQd(next.q); setReady(true);
    api.get('/api/transactions/facets').then((r) => setFacets(r.data)).catch(() => {});
  }, []);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    (Object.keys(f) as (keyof F)[]).forEach((k) => { const v = k === 'q' ? qd : f[k]; if (v) p.set(k, v); });
    const s = p.toString(); history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
  }, [ready, f, qd]);
  useEffect(() => { const t = setTimeout(() => setQd(f.q), 300); return () => clearTimeout(t); }, [f.q]);
  useEffect(() => { setPage(1); }, [qd, f.store_id, f.source_system, f.transaction_type, f.tender_type, f.status, f.detail_status, f.date_from, f.date_to, sort, pageSize]);

  const load = useCallback(async () => {
    if (!ready) return;
    const mine = ++seq.current;
    const params: Record<string, string | number> = { ...sort, page, page_size: pageSize };
    (Object.keys(f) as (keyof F)[]).forEach((k) => { const v = k === 'q' ? qd : f[k]; if (v) params[k] = v; });
    try {
      const { data } = await api.get('/api/transactions', { params });
      if (mine === seq.current) { setData(data); setError(null); }
    } catch (e) {
      if (mine === seq.current) setError((e as { response?: { status?: number } })?.response?.status === 403 ? 'Your role cannot view transactions.' : 'Could not load transactions.');
    }
  }, [ready, f, qd, sort, page, pageSize]);
  useEffect(() => { load(); }, [load]);
  useDataChanged(() => { load(); });

  const set = (patch: Partial<F>) => setF((x) => ({ ...x, ...patch }));
  const filtered = (Object.keys(f) as (keyof F)[]).some((k) => (k === 'q' ? qd : f[k]));
  const toggleSort = (k: string) => setSort((s) => s.sort === k ? { sort: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { sort: k, dir: k === 'business_date' || k === 'amount' ? 'desc' : 'asc' });
  const statuses = data ? Object.entries(data.by_status).sort((a, b) => b[1] - a[1]) : [];
  const spreadTotal = statuses.reduce((n, [, c]) => n + c, 0);

  return (
    <div className="page-container">
      <div className="page-header">
        <div><h1>Transactions</h1><p className="page-sub">Every normalized transaction from POS, processor, bank, ERP and Shopify feeds. Open one to see its raw source record and the cases it relates to.</p></div>
      </div>
      {error && <div className="alert alert-error" role="alert">{error}<button className="btn-secondary btn-sm alert-close" onClick={load}>Retry</button></div>}

      <div className="ac-tiles">
        <button className="ac-tile" aria-pressed={!f.status} onClick={() => set({ status: '' })}><small>All transactions</small><b>{spreadTotal.toLocaleString()}</b><em>{data ? `Net ${money(data.net_amount)}` : ' '}</em></button>
        {statuses.map(([s, c]) => (
          <button key={s} className={`ac-tile ${s === 'Exception' && c > 0 ? 'bad' : ''}`} aria-pressed={f.status === s} onClick={() => set({ status: f.status === s ? '' : s })}>
            <small>{s}</small><b>{c.toLocaleString()}</b><em>{s === 'Matched' ? 'Reconciled across sources' : s === 'Exception' ? 'Raised as cases' : 'Not matched yet'}</em></button>
        ))}
      </div>

      <div className="toolbar">
        <input className="grow" type="search" value={f.q} onChange={(e) => set({ q: e.target.value })} placeholder="Search reference, source record, register or id" aria-label="Search transactions" />
        <select aria-label="Store" value={f.store_id} onChange={(e) => set({ store_id: e.target.value })}><option value="">All stores</option>{facets?.stores.map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="Source" value={f.source_system} onChange={(e) => set({ source_system: e.target.value })}><option value="">All sources</option>{facets?.sources.map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="Type" value={f.transaction_type} onChange={(e) => set({ transaction_type: e.target.value })}><option value="">All types</option>{facets?.types.map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="Tender" value={f.tender_type} onChange={(e) => set({ tender_type: e.target.value })}><option value="">All tenders</option>{facets?.tenders.map((s) => <option key={s}>{s}</option>)}</select>
        <select aria-label="Line detail" value={f.detail_status} onChange={(e) => set({ detail_status: e.target.value })}><option value="">Any detail</option>{facets?.detail_statuses.map((s) => <option key={s} value={s}>{s === 'No detail' ? 'No line detail' : s}</option>)}</select>
        <input type="date" aria-label="From business date" title="From business date" value={f.date_from} min={facets?.date_min ?? undefined} max={f.date_to || facets?.date_max || undefined} onChange={(e) => set({ date_from: e.target.value })} />
        <input type="date" aria-label="To business date" title="To business date" value={f.date_to} min={f.date_from || facets?.date_min || undefined} max={facets?.date_max ?? undefined} onChange={(e) => set({ date_to: e.target.value })} />
        {filtered && <button className="btn-secondary btn-sm" onClick={() => { setF(EMPTY); setQd(''); }}>Clear filters</button>}
        <span className="toolbar-count">{data ? `${data.total.toLocaleString()} ${filtered ? 'matching' : 'total'}` : ''}</span>
      </div>

      {!data ? <SkeletonRows rows={8} label="Loading transactions" />
        : data.items.length === 0 ? <div className="empty-state"><b>No transactions match</b><p>{filtered ? 'Try clearing a filter.' : 'Transactions appear after a feed is ingested and normalized. Run the batch on the Data Pipeline page.'}</p>{filtered && <button className="btn-secondary btn-sm" onClick={() => { setF(EMPTY); setQd(''); }}>Clear filters</button>}</div>
        : (
          <div className="table-container">
            <table className="data-table">
              <thead><tr>{SORTABLE.map(([k, label, num]) => (
                <th key={k} className={num ? 'num' : ''} aria-sort={sort.sort === k ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button className="ac-th" onClick={() => toggleSort(k)}>{label}{sort.sort === k ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}</button></th>
              ))}<th>Items</th><th>Reference</th></tr></thead>
              <tbody>{data.items.map((r) => (
                <tr key={r.id} className="row-link" tabIndex={0} onClick={() => router.push(`/transactions/${r.id}`)} onKeyDown={(e) => { if (e.key === 'Enter') router.push(`/transactions/${r.id}`); }}>
                  <td className="nowrap">{r.business_date}</td><td>{r.store_id}</td><td>{r.source_system}</td><td>{r.transaction_type}</td><td>{r.tender_type ?? '—'}</td>
                  <td className="num">{money(r.amount)}</td>
                  <td><span className={`badge badge-sm ${statusTone(r.reconciliation_status)}`}>{r.reconciliation_status}</span></td>
                  <td>{r.detail_status === 'No detail' ? <span className="act-sub">No detail</span> : <>{r.item_count ?? 0}{r.detail_status === 'Unbalanced' && <span className="badge badge-sm badge-warning" style={{ marginLeft: '.35rem' }} title="Lines, tax and discounts do not add up to the amount, or the tenders do not cover it">Unbalanced</span>}</>}</td>
                  <td onClick={(e) => e.stopPropagation()}><Link href={`/transactions/${r.id}`}>{r.payment_reference ?? r.source_record_id ?? 'Open'}</Link></td>
                </tr>
              ))}</tbody>
            </table>
            <div className="ac-pager">
              <span>{((data.page - 1) * pageSize + 1).toLocaleString()}–{Math.min(data.page * pageSize, data.total).toLocaleString()} of {data.total.toLocaleString()}</span><span className="ac-spacer" />
              <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} aria-label="Rows per page">{[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n} / page</option>)}</select>
              <button className="btn-secondary btn-small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <span>Page {data.page} of {data.pages.toLocaleString()}</span>
              <button className="btn-secondary btn-small" disabled={page >= data.pages} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </div>
        )}
    </div>
  );
}

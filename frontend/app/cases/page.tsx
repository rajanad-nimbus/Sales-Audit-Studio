'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ACRow, Case } from '@/lib/api';
import { CaseTable, SortKey, SortState } from '@/components/CaseTable';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';

// Server-side sort keys (Case.* columns) for each table column.
const SORT_PARAM: Record<SortKey, string> = {
  case_number: 'case_number', case_type: 'type', store_id: 'store', total_exception_amount: 'amount',
  total_exposure: 'exposure', evidence_completeness: 'evidence', priority: 'priority', status: 'status',
};
const toCase = (r: ACRow) => ({
  id: r.id, case_number: r.case_number, case_type: r.case_type, store_id: r.store_id, status: r.status, priority: r.priority,
  total_exception_amount: r.amount, total_exposure: r.exposure, evidence_completeness: r.evidence, business_date: r.business_date,
} as unknown as Case);

export default function CasesPage() {
  const [rows, setRows] = useState<Case[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [statuses, setStatuses] = useState<{ value: string; count: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [status, setStatus] = useState('');
  const [priority, setPriority] = useState('');
  const [sort, setSort] = useState<SortState | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [ready, setReady] = useState(false);
  const seq = useRef(0);

  // Restore filters from the URL once, then keep the URL in sync so views can be shared.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setQ(p.get('q') ?? ''); setDebouncedQ(p.get('q') ?? ''); setStatus(p.get('status') ?? ''); setPriority(p.get('priority') ?? '');
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    if (debouncedQ) p.set('q', debouncedQ); if (status) p.set('status', status); if (priority) p.set('priority', priority);
    const s = p.toString();
    history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
  }, [ready, debouncedQ, status, priority]);
  useEffect(() => { const t = setTimeout(() => setDebouncedQ(q), 300); return () => clearTimeout(t); }, [q]);
  useEffect(() => { setPage(1); }, [debouncedQ, status, priority, sort, pageSize]);

  const load = useCallback(async () => {
    if (!ready) return;
    const mine = ++seq.current;
    // All cases, including snoozed and closed. Counts and rows come from the same server query as the Action Center.
    const base = new URLSearchParams({ snoozed: 'all', stage: '' });
    if (debouncedQ) base.set('q', debouncedQ);
    if (status) base.set('status', status);
    if (priority) base.set('priority', priority);
    const list = new URLSearchParams(base);
    list.set('page', String(page)); list.set('page_size', String(pageSize));
    list.set('sort', sort ? SORT_PARAM[sort.key] : 'exposure'); list.set('dir', sort ? sort.dir : 'desc');
    try {
      const [c, f] = await Promise.all([api.get(`/api/action-center/cases?${list}`), api.get(`/api/action-center/facets?snoozed=all&stage=`)]);
      if (mine !== seq.current) return;
      setRows((c.data.items as ACRow[]).map(toCase)); setTotal(c.data.total); setPages(c.data.pages);
      setStatuses(f.data.status); setError(null);
    } catch (e) {
      if (mine === seq.current) setError((e as { response?: { status?: number } })?.response?.status === 403 ? 'Your role cannot view cases.' : 'Could not load cases.');
    } finally { if (mine === seq.current) setLoading(false); }
  }, [ready, debouncedQ, status, priority, sort, page, pageSize]);
  useEffect(() => { load(); }, [load]);
  useDataChanged(() => { load(); });

  const seed = async () => {
    try { await api.post('/api/seed'); await load(); }
    catch { setError('Seed failed (data may already exist)'); }
  };
  const onSort = (k: SortKey) => setSort((s) => s?.key === k ? (s.dir === 'asc' ? { key: k, dir: 'desc' } : null) : { key: k, dir: 'asc' });
  const filtered = !!(debouncedQ || status || priority);
  const allCount = statuses.reduce((a, s) => a + s.count, 0);
  const noData = !loading && !error && !filtered && total === 0;
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Cases</h1>
          <p className="page-sub">{sort ? 'Sorted by your chosen column. Click it again to reverse, then to reset.' : 'Sorted by exposure, highest first. Click a column to sort.'}</p>
        </div>
        {noData && <button className="btn-primary" onClick={seed}>Load demo data</button>}
      </div>
      {error && (
        <div className="alert alert-error" role="alert">{error}
          <button className="btn-secondary btn-sm alert-close" onClick={load}>Retry</button>
        </div>
      )}
      {(!noData && (ready && (!loading || filtered || total > 0))) && (
        <div className="toolbar">
          <input className="grow" type="search" placeholder="Search case, type or store" aria-label="Search cases" value={q} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses ({allCount.toLocaleString()})</option>
            {statuses.map((s) => <option key={s.value} value={s.value}>{s.value} ({s.count.toLocaleString()})</option>)}
          </select>
          <select aria-label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">All priorities</option><option>Critical</option><option>High</option><option>Normal</option>
          </select>
          {filtered && <button className="btn-secondary btn-sm" onClick={() => { setQ(''); setDebouncedQ(''); setStatus(''); setPriority(''); }}>Clear</button>}
          <span className="toolbar-count">{total.toLocaleString()} {filtered ? 'matching' : 'total'}</span>
        </div>
      )}
      {loading ? <SkeletonRows rows={8} label="Loading cases" />
        : noData ? <div className="empty-state"><b>No cases yet</b><p>Cases appear after the daily batch runs reconciliation.</p></div>
        : (
          <div>
            <CaseTable cases={rows} sort={sort ?? undefined} onSort={onSort} />
            {total > 0 && (
              <div className="ac-pager">
                <span>{from.toLocaleString()}–{Math.min(page * pageSize, total).toLocaleString()} of {total.toLocaleString()}</span><span className="ac-spacer" />
                <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} aria-label="Rows per page">{[25, 50, 100].map((n) => <option key={n} value={n}>{n} / page</option>)}</select>
                <button className="btn-secondary btn-small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
                <span>Page {page} of {pages.toLocaleString()}</span>
                <button className="btn-secondary btn-small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            )}
          </div>
        )}
    </div>
  );
}

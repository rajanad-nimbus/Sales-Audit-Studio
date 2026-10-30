'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ACRow, Case } from '@/lib/api';
import { CaseTable, SortKey, SortState } from '@/components/CaseTable';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';
import { Alert, Button, EmptyState, PageHeader, Pager, Toolbar } from '@/components/ui';

// Server-side sort keys (Case.* columns) for each table column.
const SORT_PARAM: Record<SortKey, string> = {
  case_number: 'case_number',
  case_type: 'type',
  store_id: 'store',
  total_exception_amount: 'amount',
  total_exposure: 'exposure',
  evidence_completeness: 'evidence',
  priority: 'priority',
  status: 'status',
};
const toCase = (r: ACRow) =>
  ({
    id: r.id,
    case_number: r.case_number,
    case_type: r.case_type,
    store_id: r.store_id,
    status: r.status,
    priority: r.priority,
    total_exception_amount: r.amount,
    total_exposure: r.exposure,
    evidence_completeness: r.evidence,
    business_date: r.business_date,
  }) as unknown as Case;

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
    setQ(p.get('q') ?? '');
    setDebouncedQ(p.get('q') ?? '');
    setStatus(p.get('status') ?? '');
    setPriority(p.get('priority') ?? '');
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    if (debouncedQ) p.set('q', debouncedQ);
    if (status) p.set('status', status);
    if (priority) p.set('priority', priority);
    const s = p.toString();
    history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
  }, [ready, debouncedQ, status, priority]);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    setPage(1);
  }, [debouncedQ, status, priority, sort, pageSize]);

  const load = useCallback(async () => {
    if (!ready) return;
    const mine = ++seq.current;
    // All cases, including snoozed and closed. Counts and rows come from the same server query as the Action Center.
    const base = new URLSearchParams({ snoozed: 'all', stage: '' });
    if (debouncedQ) base.set('q', debouncedQ);
    if (status) base.set('status', status);
    if (priority) base.set('priority', priority);
    const list = new URLSearchParams(base);
    list.set('page', String(page));
    list.set('page_size', String(pageSize));
    list.set('sort', sort ? SORT_PARAM[sort.key] : 'exposure');
    list.set('dir', sort ? sort.dir : 'desc');
    try {
      const [c, f] = await Promise.all([
        api.get(`/api/action-center/cases?${list}`),
        api.get(`/api/action-center/facets?snoozed=all&stage=`),
      ]);
      if (mine !== seq.current) return;
      setRows((c.data.items as ACRow[]).map(toCase));
      setTotal(c.data.total);
      setPages(c.data.pages);
      setStatuses(f.data.status);
      setError(null);
    } catch (e) {
      if (mine === seq.current)
        setError(
          (e as { response?: { status?: number } })?.response?.status === 403
            ? 'Your role cannot view cases.'
            : 'Could not load cases.'
        );
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [ready, debouncedQ, status, priority, sort, page, pageSize]);
  useEffect(() => {
    load();
  }, [load]);
  useDataChanged(() => {
    load();
  });

  const seed = async () => {
    try {
      await api.post('/api/seed');
      await load();
    } catch {
      setError('Seed failed (data may already exist)');
    }
  };
  const onSort = (k: SortKey) =>
    setSort((s) =>
      s?.key === k ? (s.dir === 'asc' ? { key: k, dir: 'desc' } : null) : { key: k, dir: 'asc' }
    );
  const filtered = !!(debouncedQ || status || priority);
  const allCount = statuses.reduce((a, s) => a + s.count, 0);
  const noData = !loading && !error && !filtered && total === 0;

  return (
    <div className="page-container">
      <PageHeader
        title="Cases"
        subtitle={
          sort
            ? 'Sorted by your chosen column. Click it again to reverse, then to reset.'
            : 'Sorted by exposure, highest first. Click a column to sort.'
        }
        actions={
          noData && (
            <Button variant="primary" onClick={seed}>
              Load demo data
            </Button>
          )
        }
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
      {!noData && ready && (!loading || filtered || total > 0) && (
        <Toolbar count={`${total.toLocaleString()} ${filtered ? 'matching' : 'total'}`}>
          <input
            className="grow"
            type="search"
            placeholder="Search case, type or store"
            aria-label="Search cases"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses ({allCount.toLocaleString()})</option>
            {statuses.map((s) => (
              <option key={s.value} value={s.value}>
                {s.value} ({s.count.toLocaleString()})
              </option>
            ))}
          </select>
          <select
            aria-label="Priority"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          >
            <option value="">All priorities</option>
            <option>Critical</option>
            <option>High</option>
            <option>Normal</option>
          </select>
          {filtered && (
            <Button
              size="sm"
              onClick={() => {
                setQ('');
                setDebouncedQ('');
                setStatus('');
                setPriority('');
              }}
            >
              Clear
            </Button>
          )}
        </Toolbar>
      )}
      {loading ? (
        <SkeletonRows rows={8} label="Loading cases" />
      ) : noData ? (
        <EmptyState title="No cases yet">
          <p>Cases appear after the daily batch runs reconciliation.</p>
        </EmptyState>
      ) : (
        <CaseTable
          cases={rows}
          sort={sort ?? undefined}
          onSort={onSort}
          footer={
            total > 0 && (
              <Pager
                page={page}
                pages={pages}
                total={total}
                pageSize={pageSize}
                onPage={setPage}
                onPageSize={setPageSize}
              />
            )
          }
        />
      )}
    </div>
  );
}

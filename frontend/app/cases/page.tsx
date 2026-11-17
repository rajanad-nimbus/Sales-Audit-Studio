'use client';

import { formatNumber } from '@/lib/preferences';
import { downloadBlob } from '@/lib/csv';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ACRow, Case } from '@/lib/api';
import { CaseTable, SortKey, SortState } from '@/components/CaseTable';
import { SkeletonRows } from '@/components/Feedback';
import { useDataChanged } from '@/lib/sync';
import {
  Alert,
  Button,
  EmptyState,
  PageHeader,
  Pager,
  Toolbar,
  useNameDialog,
} from '@/components/ui';

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
const SORT_KEY = Object.fromEntries(
  Object.entries(SORT_PARAM).map(([key, value]) => [value, key])
) as Record<string, SortKey>;
interface SavedCaseView {
  name: string;
  params: {
    q?: string;
    status?: string;
    priority?: string;
    sort?: string;
    dir?: 'asc' | 'desc';
    page?: string;
    page_size?: string;
  };
}
const toCase = (r: ACRow) =>
  ({
    id: r.id,
    case_number: r.case_number,
    case_type: r.case_type,
    store_id: r.store_id,
    currency: r.currency,
    amount_needs_review: r.amount_needs_review,
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
  const [loadFailed, setLoadFailed] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [status, setStatus] = useState('');
  const [priority, setPriority] = useState('');
  const [sort, setSort] = useState<SortState | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [ready, setReady] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedCaseView[]>([]);
  const [savedViewsError, setSavedViewsError] = useState(false);
  const savedViewSaveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const skipPageReset = useRef(false);
  const seq = useRef(0);

  // Restore filters from the URL once, then keep the URL in sync so views can be shared.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setQ(p.get('q') ?? '');
    setDebouncedQ(p.get('q') ?? '');
    setStatus(p.get('status') ?? '');
    setPriority(p.get('priority') ?? '');
    const sortKey = SORT_KEY[p.get('sort') ?? ''];
    const dir = p.get('dir');
    if (sortKey && (dir === 'asc' || dir === 'desc')) setSort({ key: sortKey, dir });
    const requestedPage = Number(p.get('page'));
    if (Number.isInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 100_000)
      setPage(requestedPage);
    const requestedPageSize = Number(p.get('page_size'));
    if ([25, 50, 100].includes(requestedPageSize)) setPageSize(requestedPageSize);
    skipPageReset.current = true;
    setReady(true);
  }, []);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    if (debouncedQ) p.set('q', debouncedQ);
    if (status) p.set('status', status);
    if (priority) p.set('priority', priority);
    if (sort) {
      p.set('sort', SORT_PARAM[sort.key]);
      p.set('dir', sort.dir);
    }
    if (page > 1) p.set('page', String(page));
    if (pageSize !== 50) p.set('page_size', String(pageSize));
    const s = p.toString();
    history.replaceState(null, '', s ? `?${s}` : window.location.pathname);
  }, [ready, debouncedQ, status, priority, sort, page, pageSize]);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (!ready) return;
    if (skipPageReset.current) {
      skipPageReset.current = false;
      return;
    }
    setPage(1);
  }, [ready, debouncedQ, status, priority, sort, pageSize]);

  useEffect(() => {
    let localViews: SavedCaseView[] = [];
    try {
      localViews = JSON.parse(localStorage.getItem('nimbus_cases_views') || '[]');
    } catch {
      /* ignore */
    }
    api
      .get<{ views: SavedCaseView[] }>('/api/me/saved-views/cases')
      .then(async ({ data }) => {
        if (data.views.length === 0 && localViews.length > 0) {
          setSavedViews(localViews);
          await api.put('/api/me/saved-views/cases', { views: localViews });
        } else {
          setSavedViews(data.views);
        }
        setSavedViewsError(false);
      })
      .catch(() => {
        setSavedViews(localViews);
        setSavedViewsError(true);
      });
  }, []);

  const persistSavedViews = (next: SavedCaseView[]) => {
    setSavedViews(next);
    try {
      localStorage.setItem('nimbus_cases_views', JSON.stringify(next));
    } catch {
      /* ignore */
    }
    savedViewSaveQueue.current = savedViewSaveQueue.current
      .catch(() => undefined)
      .then(() => api.put('/api/me/saved-views/cases', { views: next }))
      .then(() => setSavedViewsError(false))
      .catch(() => setSavedViewsError(true));
  };

  const applySavedView = (view: SavedCaseView) => {
    const nextQ = view.params.q ?? '';
    const nextStatus = view.params.status ?? '';
    const nextPriority = view.params.priority ?? '';
    const sortKey = SORT_KEY[view.params.sort ?? 'exposure'];
    const nextSort = sortKey ? { key: sortKey, dir: view.params.dir ?? 'desc' } : null;
    const nextPageSize = [25, 50, 100].includes(Number(view.params.page_size))
      ? Number(view.params.page_size)
      : 50;
    skipPageReset.current =
      debouncedQ !== nextQ ||
      status !== nextStatus ||
      priority !== nextPriority ||
      sort?.key !== nextSort?.key ||
      sort?.dir !== nextSort?.dir ||
      pageSize !== nextPageSize;
    setQ(nextQ);
    setDebouncedQ(nextQ);
    setStatus(nextStatus);
    setPriority(nextPriority);
    setSort(nextSort as SortState | null);
    setPageSize(nextPageSize);
    setPage(Number(view.params.page) || 1);
  };

  const { ask: askViewName, dialog: viewNameDialog } = useNameDialog();
  const saveCurrentView = async () => {
    const name = await askViewName(
      'Name this Cases view',
      savedViews.map((view) => view.name)
    );
    if (!name) return;
    const params: SavedCaseView['params'] = {
      sort: sort ? SORT_PARAM[sort.key] : 'exposure',
      dir: sort?.dir ?? 'desc',
      page: String(page),
      page_size: String(pageSize),
    };
    if (debouncedQ) params.q = debouncedQ;
    if (status) params.status = status;
    if (priority) params.priority = priority;
    persistSavedViews([
      ...savedViews.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
      { name, params },
    ]);
  };

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
      setLoadFailed(false);
    } catch (e) {
      if (mine === seq.current) setLoadFailed(true);
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

  const downloadFilteredCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const params = new URLSearchParams({ snoozed: 'all', stage: '' });
      if (debouncedQ) params.set('q', debouncedQ);
      if (status) params.set('status', status);
      if (priority) params.set('priority', priority);
      params.set('sort', sort ? SORT_PARAM[sort.key] : 'exposure');
      params.set('dir', sort?.dir ?? 'desc');
      const { data } = await api.get(`/api/action-center/export.csv?${params}`, {
        responseType: 'blob',
      });
      downloadBlob('cases.csv', data);
    } catch (cause) {
      const statusCode = (cause as { response?: { status?: number } })?.response?.status;
      setExportError(
        statusCode === 413
          ? 'This export is limited to 50,000 matching cases. Narrow the filters and try again.'
          : 'Could not download filtered cases.'
      );
    } finally {
      setExporting(false);
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
      {viewNameDialog}
      <PageHeader
        title="Cases"
        subtitle={
          sort
            ? 'Sorted by your chosen column. Click it again to reverse, then to reset.'
            : 'Sorted by exposure, highest first. Click a column to sort.'
        }
        actions={
          <>
            {total > 0 && (
              <Button
                variant="secondary"
                disabled={loading || exporting}
                onClick={() => void downloadFilteredCsv()}
              >
                {exporting ? 'Preparing CSV…' : 'Download filtered CSV'}
              </Button>
            )}
          </>
        }
      />
      {exportError && (
        <Alert tone="error" onDismiss={() => setExportError(null)}>
          {exportError}
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
      {savedViewsError && (
        <Alert tone="warning">
          Saved views are available on this device, but could not sync to your account.
        </Alert>
      )}
      <div className="ac-presets" aria-label="Saved Cases views">
        {savedViews.map((view, index) => (
          <span key={`${view.name}-${index}`} className="ac-chip saved">
            <button onClick={() => applySavedView(view)}>{view.name}</button>
            <button
              aria-label={`Delete view ${view.name}`}
              onClick={() => persistSavedViews(savedViews.filter((_, row) => row !== index))}
            >
              ✕
            </button>
          </span>
        ))}
        <button className="ac-chip add" onClick={saveCurrentView}>
          + Save current view
        </button>
      </div>
      {!noData && ready && (!loading || filtered || total > 0) && (
        <Toolbar count={`${formatNumber(total)} ${filtered ? 'matching' : 'total'}`}>
          <input
            className="grow"
            type="search"
            placeholder="Search case, type or store"
            aria-label="Search cases"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses ({formatNumber(allCount)})</option>
            {statuses.map((s) => (
              <option key={s.value} value={s.value}>
                {s.value} ({formatNumber(s.count)})
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
          failed={loadFailed}
          detachTitle={`Cases · ${status || 'all statuses'} · ${priority || 'all priorities'}`}
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

'use client';

import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api, currencyBreakdown } from '@/lib/api';
import { useDataChanged } from '@/lib/sync';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  MetricGrid,
  MetricTile,
  PageHeader,
  Segmented,
  Tabs,
  Toolbar,
  useNameDialog,
} from '@/components/ui';
import { ErrorPatterns } from '@/components/ErrorPatterns';
import { errorPath } from '@/components/AuditErrorDetail';
import type { ErrorType } from '@/components/AuditErrorDetail';
import type { Column } from '@/components/ui';

interface Overview {
  items: ErrorType[];
  open_total: number;
  open_types: number;
  window_days: number;
}
interface SavedAuditErrorsView {
  name: string;
  params: { q?: string; show?: 'open' | 'all'; days?: string; family?: string };
}

/** How far back "Raised" counts. Open errors are always counted whatever their age. */
const WINDOWS = [
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '1 year' },
  { value: '0', label: 'All time' },
];
const windowLabel = (days: number) =>
  WINDOWS.find((w) => w.value === String(days))?.label ?? `${days} days`;

const columnsFor = (days: number): Column<ErrorType>[] => [
  {
    key: 'error',
    header: 'Error',
    primary: true,
    stopRowClick: true,
    csvValue: (r) => `${r.label} (${r.code})`,
    render: (r) => (
      <>
        <Link href={errorPath(r.code)}>{r.label}</Link>
        <div className="act-sub">{r.code}</div>
      </>
    ),
  },
  { key: 'family', header: 'Family', render: (r) => r.family, csvValue: (r) => r.family },
  {
    key: 'open',
    header: 'Open',
    numeric: true,
    csvValue: (r) => r.open_count,
    render: (r) => (
      <Badge variant={r.open_count ? 'error' : 'secondary'} size="sm">
        {formatNumber(r.open_count)}
      </Badge>
    ),
  },
  {
    key: 'total',
    header: `Raised, ${windowLabel(days)}`,
    numeric: true,
    csvValue: (r) => r.total_count,
    render: (r) => formatNumber(r.total_count),
  },
  {
    key: 'amount',
    header: 'Open amount',
    numeric: true,
    csvValue: (r) =>
      r.open_by_currency.map((amount) => `${amount.currency} ${amount.amount}`).join('; '),
    render: (r) => (r.open_count ? currencyBreakdown(r.open_by_currency, 'amount') : '—'),
  },
  {
    key: 'latest',
    header: 'Last raised',
    nowrap: true,
    csvValue: (r) => (r.latest_at ? r.latest_at.slice(0, 10) : ''),
    render: (r) => (r.latest_at ? r.latest_at.slice(0, 10) : '—'),
  },
];

export default function AuditErrorsPage() {
  const router = useRouter();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [show, setShow] = useState<'open' | 'all'>('open');
  const [days, setDays] = useState('90');
  const [family, setFamily] = useState('');
  const [q, setQ] = useState('');
  const [tab, setTab] = useState<'types' | 'patterns'>('types');
  const [filtersReady, setFiltersReady] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedAuditErrorsView[]>([]);
  const [savedViewsError, setSavedViewsError] = useState(false);
  const savedViewSaveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const overviewRequestId = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++overviewRequestId.current;
    try {
      const { data } = await api.get('/api/audit-errors', { params: { days } });
      if (requestId !== overviewRequestId.current) return;
      setData(data);
      setError(null);
    } catch (e) {
      if (requestId !== overviewRequestId.current) return;
      setError(
        (e as { response?: { status?: number } })?.response?.status === 403
          ? 'Your role cannot view audit errors.'
          : 'Could not load audit errors.'
      );
    }
  }, [days]);
  useEffect(() => {
    load();
  }, [load]);
  useDataChanged(() => {
    load();
  });
  // An older link such as /audit-errors?code=TRAN_OUT_BAL goes to that error's own page.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    if (code) router.replace(errorPath(code));
    const requestedShow = params.get('show');
    if (requestedShow === 'open' || requestedShow === 'all') setShow(requestedShow);
    const requestedDays = params.get('days');
    if (WINDOWS.some((window) => window.value === requestedDays)) setDays(requestedDays!);
    setFamily(params.get('family') ?? '');
    setQ((params.get('q') ?? '').slice(0, 255));
    setFiltersReady(true);
  }, [router]);

  useEffect(() => {
    if (!filtersReady) return;
    const params = new URLSearchParams(window.location.search);
    if (params.has('code')) return;
    for (const key of ['show', 'days', 'family', 'q']) params.delete(key);
    if (show !== 'open') params.set('show', show);
    if (days !== '90') params.set('days', days);
    if (family) params.set('family', family);
    if (q.trim()) params.set('q', q.trim());
    const query = params.toString();
    history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
  }, [filtersReady, show, days, family, q]);

  useEffect(() => {
    let localViews: SavedAuditErrorsView[] = [];
    try {
      localViews = JSON.parse(localStorage.getItem('nimbus_audit_errors_views') || '[]');
    } catch {
      /* ignore */
    }
    api
      .get<{ views: SavedAuditErrorsView[] }>('/api/me/saved-views/audit_errors')
      .then(async ({ data }) => {
        if (data.views.length === 0 && localViews.length > 0) {
          setSavedViews(localViews);
          await api.put('/api/me/saved-views/audit_errors', { views: localViews });
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

  // The raised-in totals are tied to this window. Do not show a prior window's
  // counts under the newly selected filter while its overview is still loading.
  const currentData = data?.window_days === Number(days) ? data : null;
  const families = currentData ? [...new Set(currentData.items.map((i) => i.family))].sort() : [];
  const term = q.trim().toLowerCase();
  const rows = currentData
    ? currentData.items.filter(
        (i) =>
          (show === 'all' || i.open_count > 0) &&
          (!family || i.family === family) &&
          (!term || `${i.label} ${i.code}`.toLowerCase().includes(term))
      )
    : null;

  const persistSavedViews = (next: SavedAuditErrorsView[]) => {
    setSavedViews(next);
    try {
      localStorage.setItem('nimbus_audit_errors_views', JSON.stringify(next));
    } catch {
      /* ignore */
    }
    savedViewSaveQueue.current = savedViewSaveQueue.current
      .catch(() => undefined)
      .then(() => api.put('/api/me/saved-views/audit_errors', { views: next }))
      .then(() => setSavedViewsError(false))
      .catch(() => setSavedViewsError(true));
  };

  const applySavedView = (view: SavedAuditErrorsView) => {
    setTab('types');
    setShow(view.params.show ?? 'open');
    setDays(view.params.days ?? '90');
    setFamily(view.params.family ?? '');
    setQ(view.params.q ?? '');
  };

  const { ask: askViewName, dialog: viewNameDialog } = useNameDialog();
  const saveCurrentView = async () => {
    const name = await askViewName(
      'Name this Audit Errors view',
      savedViews.map((view) => view.name)
    );
    if (!name) return;
    const params: SavedAuditErrorsView['params'] = { show, days };
    if (family) params.family = family;
    if (q.trim()) params.q = q.trim();
    persistSavedViews([
      ...savedViews.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
      { name, params },
    ]);
  };

  return (
    <div className="page-container">
      {viewNameDialog}
      <PageHeader
        title="Audit Errors"
        subtitle="Every Sales Audit error type, how many are open, and how each one is fixed. Open one to see what it means, where it is corrected, and the cases that hold it."
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

      <Tabs
        value={tab}
        onChange={(id) => setTab(id as 'types' | 'patterns')}
        items={[
          { id: 'types', label: 'Error types' },
          { id: 'patterns', label: 'Repeating errors' },
        ]}
      />
      {savedViewsError && (
        <Alert tone="warning">
          Saved views are available on this device, but could not sync to your account.
        </Alert>
      )}
      <div className="ac-presets" aria-label="Saved Audit Errors views">
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
        {tab === 'types' && (
          <button className="ac-chip add" onClick={saveCurrentView}>
            + Save current view
          </button>
        )}
      </div>
      {tab === 'patterns' && <ErrorPatterns />}
      {tab === 'types' && (
        <>
          <MetricGrid>
            <MetricTile
              label="Open errors"
              value={currentData ? formatNumber(currentData.open_total) : '—'}
            />
            <MetricTile
              label="Error types affected"
              value={currentData ? formatNumber(currentData.open_types) : '—'}
            />
            <MetricTile
              label="Error types known"
              value={currentData ? currentData.items.length : '—'}
            />
          </MetricGrid>

          <Toolbar count={rows ? `${rows.length} shown` : ''}>
            <Segmented
              options={[
                { value: 'open', label: 'With open errors' },
                { value: 'all', label: 'All error types' },
              ]}
              value={show}
              onChange={setShow}
            />
            <input
              className="grow"
              type="search"
              value={q}
              maxLength={255}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search error name or code"
              aria-label="Search audit errors"
            />
            <select aria-label="Raised in" value={days} onChange={(e) => setDays(e.target.value)}>
              {WINDOWS.map((w) => (
                <option key={w.value} value={w.value}>
                  Raised in {w.label.toLowerCase()}
                </option>
              ))}
            </select>
            <select aria-label="Family" value={family} onChange={(e) => setFamily(e.target.value)}>
              <option value="">All families</option>
              {families.map((f) => (
                <option key={f}>{f}</option>
              ))}
            </select>
          </Toolbar>

          <DataTable
            columns={columnsFor(Number(days))}
            csvFileName="audit-errors-visible.csv"
            rows={rows}
            rowKey={(r) => r.code}
            onRowClick={(r) => router.push(errorPath(r.code))}
            emptyTitle={show === 'open' ? 'No open audit errors' : 'No error types match'}
            emptyHint={
              show === 'open'
                ? 'Nothing needs fixing. Switch to All error types to see what the audit checks for.'
                : 'Try clearing the search or family.'
            }
          />
        </>
      )}
    </div>
  );
}

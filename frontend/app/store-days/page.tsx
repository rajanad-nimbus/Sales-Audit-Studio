'use client';

import { formatNumber } from '@/lib/preferences';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { StoreScopeNote } from '@/components/StoreScope';
import { useMe } from '@/components/MeProvider';
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Field,
  MetricGrid,
  MetricTile,
  PageHeader,
  PageNav,
  Pager,
  Segmented,
  Tabs,
  Toolbar,
  useNameDialog,
} from '@/components/ui';
import { StoreSelect } from '@/components/StoreSelect';
import { StoreDayBrief } from '@/components/storeDays/StoreDayBrief';
import { apiError, dayKey, dayPath, dayStatusVariant, readinessVariant } from '@/lib/storeDays';
import type { Readiness, StoreDay } from '@/lib/storeDays';

interface LateStore {
  store_id: string;
  business_date: string;
  store_day_open: boolean;
  state: string;
  transactions: number;
  detail: string;
}
interface StuckDay extends StoreDay {
  days_open: number;
  holding_back: string[];
}
interface Attention {
  business_date: string;
  expected_stores: number;
  late_stores: LateStore[];
  late_total: number;
  late_page: number;
  late_page_size: number;
  stuck_after_days: number;
  stuck_store_days: StuckDay[];
  stuck_truncated: boolean;
  close_summary?: {
    closed_automatically: number;
    closed_by_person: number;
    open: number;
    not_opened: number;
  };
}
type ListTab = 'days' | 'late' | 'stuck';
const DATA_STATUSES = ['Complete', 'Incomplete', 'Not reconciled', 'No control total', 'No data'];
const OVERALL_STATUSES = ['Sales Audit in Progress', 'Sales Audit Complete'];
const AUDIT_STATUSES = [
  'Re-Totalling/Auditing Required',
  'Store Errors Pending',
  'Totaled',
  'Audited',
];
interface SavedStoreDaysView {
  name: string;
  params: Record<string, string>;
}
interface Summary {
  total: number;
  by_status: Record<string, number>;
  totals_stale: number;
  unsigned_audit_versions?: number;
}
const daysAgo = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
const validIsoDate = (value: string | null) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value
    ? value
    : null;
};

function ReadinessBadge({ r, part }: { r?: Readiness; part: 'ingest' | 'audit' | 'export' }) {
  if (!r) return <>—</>;
  const view = r[part];
  const label = part === 'audit' ? (r.audit.resa_status ?? r.audit.state) : view.state;
  const badgeState = part === 'audit' ? label : view.state;
  return (
    <span title={'detail' in view ? view.detail : `${view.records} record(s)`}>
      <Badge variant={readinessVariant(badgeState)} size="sm">
        {label}
      </Badge>
    </span>
  );
}

export default function StoreDaysPage() {
  const router = useRouter();
  const toast = useToast();
  const { me } = useMe();
  // Mirrors POST /api/store-days: Sales Auditor, IT and Admin.
  const canOpen = !!me && ['analyst', 'it', 'admin'].includes(me.role);
  const [days, setDays] = useState<StoreDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const [showOpen, setShowOpen] = useState(false);
  const [openStore, setOpenStore] = useState('');
  const [openDate, setOpenDate] = useState(today);
  const [busy, setBusy] = useState(false);
  // Every filter is sent to the server; the browser never holds more than one page.
  const [statusFilter, setStatusFilter] = useState('');
  const [dataStatus, setDataStatus] = useState('');
  const [auditStatus, setAuditStatus] = useState('');
  const [overallStatus, setOverallStatus] = useState('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [filtersReady, setFiltersReady] = useState(false);
  const [storeFilter, setStoreFilter] = useState('');
  const [dateFrom, setDateFrom] = useState(daysAgo(30));
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [hasMore, setHasMore] = useState(false);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [tab, setTab] = useState<ListTab>('days');
  const [attention, setAttention] = useState<Attention | null>(null);
  const [attentionError, setAttentionError] = useState<string | null>(null);
  const [attentionExporting, setAttentionExporting] = useState(false);
  const [attentionExportError, setAttentionExportError] = useState<string | null>(null);
  const yesterday = daysAgo(1);
  const [lateDate, setLateDate] = useState(yesterday);
  const [latePage, setLatePage] = useState(1);
  const [lateSize, setLateSize] = useState(100);
  const [stuckAfter, setStuckAfter] = useState(1);
  const [savedViews, setSavedViews] = useState<SavedStoreDaysView[]>([]);
  const [savedViewsError, setSavedViewsError] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);
  const savedViewSaveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const attentionRequest = useRef(0);
  const listRequest = useRef(0);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialQuery = (params.get('q') ?? '').slice(0, 50);
    const initialDateFrom = params.has('date_from')
      ? (validIsoDate(params.get('date_from')) ?? '')
      : daysAgo(30);
    const candidateDateTo = validIsoDate(params.get('date_to'));
    setQ(initialQuery);
    setDebouncedQ(initialQuery);
    setStoreFilter((params.get('store_id') ?? '').slice(0, 50));
    setDateFrom(initialDateFrom);
    setDateTo(
      candidateDateTo && (!initialDateFrom || candidateDateTo >= initialDateFrom)
        ? candidateDateTo
        : ''
    );
    setStatusFilter((params.get('status') ?? '').slice(0, 32));
    const requestedDataStatus = params.get('data_status') ?? '';
    const requestedAuditStatus = params.get('audit_status') ?? '';
    const requestedOverallStatus = params.get('overall_status') ?? '';
    setDataStatus(DATA_STATUSES.includes(requestedDataStatus) ? requestedDataStatus : '');
    setAuditStatus(AUDIT_STATUSES.includes(requestedAuditStatus) ? requestedAuditStatus : '');
    setOverallStatus(
      OVERALL_STATUSES.includes(requestedOverallStatus) ? requestedOverallStatus : ''
    );
    const requestedTab = params.get('tab');
    if (requestedTab === 'days' || requestedTab === 'late' || requestedTab === 'stuck')
      setTab(requestedTab);
    const requestedPageSize = Number(params.get('page_size'));
    if ([25, 50, 100, 200].includes(requestedPageSize)) setPageSize(requestedPageSize);
    const requestedPage = Number(params.get('page'));
    if (Number.isInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 100_000)
      setPage(requestedPage);
    const requestedLateDate = validIsoDate(params.get('late_date'));
    if (requestedLateDate) setLateDate(requestedLateDate);
    const requestedLatePage = Number(params.get('late_page'));
    if (
      Number.isInteger(requestedLatePage) &&
      requestedLatePage >= 1 &&
      requestedLatePage <= 100_000
    )
      setLatePage(requestedLatePage);
    const requestedLateSize = Number(params.get('late_size'));
    if ([50, 100, 200, 500].includes(requestedLateSize)) setLateSize(requestedLateSize);
    const requestedStuckAfter = Number(params.get('stuck_after'));
    if ([1, 3, 7, 14].includes(requestedStuckAfter)) setStuckAfter(requestedStuckAfter);
    setFiltersReady(true);

    let localViews: SavedStoreDaysView[] = [];
    try {
      localViews = JSON.parse(localStorage.getItem('nimbus_store_days_views') || '[]');
    } catch {
      /* ignore */
    }
    api
      .get<{ views: SavedStoreDaysView[] }>('/api/me/saved-views/store_days')
      .then(async ({ data }) => {
        if (data.views.length === 0 && localViews.length > 0) {
          setSavedViews(localViews);
          await api.put('/api/me/saved-views/store_days', { views: localViews });
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

  useEffect(() => {
    if (!filtersReady) return;
    const params = new URLSearchParams(window.location.search);
    const query = debouncedQ.trim();
    for (const key of [
      'q',
      'store_id',
      'date_from',
      'date_to',
      'status',
      'data_status',
      'audit_status',
      'overall_status',
      'tab',
      'page_size',
      'page',
      'late_date',
      'late_page',
      'late_size',
      'stuck_after',
    ])
      params.delete(key);
    if (query) params.set('q', query);
    if (storeFilter) params.set('store_id', storeFilter);
    params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (statusFilter) params.set('status', statusFilter);
    if (dataStatus) params.set('data_status', dataStatus);
    if (auditStatus) params.set('audit_status', auditStatus);
    if (overallStatus) params.set('overall_status', overallStatus);
    if (tab !== 'days') params.set('tab', tab);
    if (pageSize !== 50) params.set('page_size', String(pageSize));
    if (page > 1) params.set('page', String(page));
    if (tab === 'late' || lateDate !== yesterday) params.set('late_date', lateDate);
    if (latePage > 1) params.set('late_page', String(latePage));
    if (lateSize !== 100) params.set('late_size', String(lateSize));
    if (stuckAfter !== 1) params.set('stuck_after', String(stuckAfter));
    const queryString = params.toString();
    history.replaceState(
      null,
      '',
      queryString ? `${window.location.pathname}?${queryString}` : window.location.pathname
    );
  }, [
    filtersReady,
    debouncedQ,
    storeFilter,
    dateFrom,
    dateTo,
    statusFilter,
    dataStatus,
    auditStatus,
    overallStatus,
    tab,
    pageSize,
    page,
    lateDate,
    latePage,
    lateSize,
    stuckAfter,
    yesterday,
  ]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const loadAttention = useCallback(async () => {
    if (!filtersReady) return;
    const request = ++attentionRequest.current;
    setAttentionError(null);
    try {
      const { data } = await api.get('/api/store-days/attention', {
        params: {
          business_date: lateDate,
          stuck_after_days: stuckAfter,
          late_page: latePage,
          late_page_size: lateSize,
        },
      });
      if (request === attentionRequest.current) {
        setAttention(data);
        setAttentionError(null);
      }
    } catch {
      if (request === attentionRequest.current) {
        setAttention(null);
        setAttentionError('Could not load store review queues. Check your connection and retry.');
      }
    }
  }, [filtersReady, lateDate, stuckAfter, latePage, lateSize]);
  useEffect(() => {
    loadAttention();
  }, [loadAttention]);

  const load = useCallback(async () => {
    if (!filtersReady) return;
    const request = ++listRequest.current;
    setLoading(true);
    const scope = {
      store_id: storeFilter || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      q: debouncedQ.trim() || undefined,
    };
    try {
      const [list, counts] = await Promise.all([
        api.get('/api/store-days', {
          params: {
            ...scope,
            status: statusFilter || undefined,
            data_status: dataStatus || undefined,
            audit_status: auditStatus || undefined,
            overall_status: overallStatus || undefined,
            limit: pageSize,
            page,
          },
        }),
        api.get('/api/store-days/summary', {
          params: {
            ...scope,
            data_status: dataStatus || undefined,
            audit_status: auditStatus || undefined,
            overall_status: overallStatus || undefined,
          },
        }),
      ]);
      if (request === listRequest.current) {
        setDays(list.data);
        setHasMore(list.headers?.['x-has-more'] === 'true');
        setSummary(counts.data);
        setError(null);
        setLoadFailed(false);
      }
    } catch (cause) {
      if (request === listRequest.current) {
        setLoadFailed(true);
        setError(
          apiError(cause, 'Could not load Store Days. Check your access or connection and retry.')
        );
      }
    } finally {
      if (request === listRequest.current) setLoading(false);
    }
  }, [
    filtersReady,
    storeFilter,
    dateFrom,
    dateTo,
    debouncedQ,
    statusFilter,
    dataStatus,
    auditStatus,
    overallStatus,
    pageSize,
    page,
  ]);
  useEffect(() => {
    load();
  }, [load]);

  const persistSavedViews = (next: SavedStoreDaysView[]) => {
    setSavedViews(next);
    try {
      localStorage.setItem('nimbus_store_days_views', JSON.stringify(next));
    } catch {
      /* ignore */
    }
    savedViewSaveQueue.current = savedViewSaveQueue.current
      .catch(() => undefined)
      .then(() => api.put('/api/me/saved-views/store_days', { views: next }))
      .then(() => setSavedViewsError(false))
      .catch(() => setSavedViewsError(true));
  };

  const currentViewParams = (): Record<string, string> => {
    const params: Record<string, string> = {
      date_from: dateFrom,
      tab,
      stuck_after: String(stuckAfter),
    };
    if (debouncedQ.trim()) params.q = debouncedQ.trim();
    if (storeFilter) params.store_id = storeFilter;
    if (dateTo) params.date_to = dateTo;
    if (statusFilter) params.status = statusFilter;
    if (dataStatus) params.data_status = dataStatus;
    if (auditStatus) params.audit_status = auditStatus;
    if (overallStatus) params.overall_status = overallStatus;
    if (pageSize !== 50) params.page_size = String(pageSize);
    if (page > 1) params.page = String(page);
    if (tab === 'late' || lateDate !== yesterday) params.late_date = lateDate;
    if (latePage > 1) params.late_page = String(latePage);
    if (lateSize !== 100) params.late_size = String(lateSize);
    return params;
  };

  const applySavedView = (view: SavedStoreDaysView) => {
    const params = view.params;
    setQ(params.q ?? '');
    setDebouncedQ(params.q ?? '');
    setStoreFilter(params.store_id ?? '');
    setDateFrom(Object.hasOwn(params, 'date_from') ? params.date_from : daysAgo(30));
    setDateTo(params.date_to ?? '');
    setStatusFilter(params.status ?? '');
    const nextDataStatus = params.data_status ?? '';
    const nextAuditStatus = params.audit_status ?? '';
    const nextOverallStatus = params.overall_status ?? '';
    setDataStatus(DATA_STATUSES.includes(nextDataStatus) ? nextDataStatus : '');
    setAuditStatus(AUDIT_STATUSES.includes(nextAuditStatus) ? nextAuditStatus : '');
    setOverallStatus(OVERALL_STATUSES.includes(nextOverallStatus) ? nextOverallStatus : '');
    setTab(['days', 'late', 'stuck'].includes(params.tab) ? (params.tab as ListTab) : 'days');
    setPageSize(
      [25, 50, 100, 200].includes(Number(params.page_size)) ? Number(params.page_size) : 50
    );
    setPage(Number(params.page) > 0 && Number(params.page) <= 100_000 ? Number(params.page) : 1);
    setLateDate(validIsoDate(params.late_date ?? null) ?? yesterday);
    setLatePage(
      Number(params.late_page) > 0 && Number(params.late_page) <= 100_000
        ? Number(params.late_page)
        : 1
    );
    setLateSize(
      [50, 100, 200, 500].includes(Number(params.late_size)) ? Number(params.late_size) : 100
    );
    setStuckAfter(
      [1, 3, 7, 14].includes(Number(params.stuck_after)) ? Number(params.stuck_after) : 1
    );
    setExpanded(null);
  };

  const { ask: askViewName, dialog: viewNameDialog } = useNameDialog();
  const saveCurrentView = async () => {
    const name = await askViewName(
      'Name this Store Days view',
      savedViews.map((view) => view.name)
    );
    if (!name) return;
    persistSavedViews([
      ...savedViews.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
      { name, params: currentViewParams() },
    ]);
  };

  const exportAttention = async (queue: 'late' | 'stuck') => {
    setAttentionExporting(true);
    setAttentionExportError(null);
    try {
      const { data } = await api.get('/api/store-days/attention', {
        params: {
          format: 'csv',
          queue,
          business_date: lateDate,
          stuck_after_days: stuckAfter,
        },
        responseType: 'blob',
      });
      downloadBlob(`store-days-${queue}.csv`, data);
    } catch (e) {
      const statusCode = (e as { response?: { status?: number } }).response?.status;
      setAttentionExportError(
        statusCode === 413
          ? queue === 'late'
            ? 'The late-store export exceeds 50,000 rows. Use an account with a narrower store scope.'
            : 'The stuck-day export exceeds 50,000 rows. Increase the age threshold or narrow the store scope.'
          : 'Could not download this Store Days queue.'
      );
    } finally {
      setAttentionExporting(false);
    }
  };

  /** A change to what is being listed starts again from the first page, with nothing open. */
  const refilter = (change: () => void) => {
    change();
    setPage(1);
    setExpanded(null);
  };
  const onPage = (next: number) => {
    setPage(next);
    setExpanded(null);
  };

  const statuses = summary ? Object.keys(summary.by_status).sort() : [];
  const onThisPage = useMemo(
    () => ({
      ready: days.filter((d) => d.readiness?.ready_to_close).length,
      incomplete: days.filter((d) => d.readiness && d.readiness.ingest.state !== 'Complete').length,
      attention: days.filter(
        (d) => d.readiness && (d.readiness.audit.state !== 'Clean' || !d.readiness.audit.signed_off)
      ).length,
    }),
    [days]
  );
  const openDay = async () => {
    setBusy(true);
    try {
      const { data } = await api.post('/api/store-days', {
        store_id: openStore,
        business_date: openDate,
      });
      toast(
        `Opened ${data.store_id} · ${data.business_date}. Re-total it once its data has loaded.`,
        'success'
      );
      router.push(dayPath(data));
    } catch (e) {
      toast(apiError(e, 'Could not open the Store Day.'), 'error');
      setBusy(false);
    }
  };

  const copyWorkspaceLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      toast('Could not copy the link. You can copy it from the address bar.', 'error');
    }
  };

  return (
    <div className="page-container">
      {viewNameDialog}
      <PageHeader
        title="Store Days & Totals"
        subtitle="Versioned balancing totals for the controlled store-day audit lifecycle. Select a Store Day to see its totals, closure, cases and history."
        actions={
          <div className="row-actions">
            <Button variant="secondary" onClick={() => void copyWorkspaceLink()}>
              {linkCopied ? 'Link copied' : 'Copy workspace link'}
            </Button>
            {canOpen && (
              <Button
                variant={showOpen ? 'secondary' : 'primary'}
                onClick={() => setShowOpen(!showOpen)}
              >
                {showOpen ? 'Cancel' : 'Open Store Day'}
              </Button>
            )}
          </div>
        }
      />
      <StoreScopeNote />
      {error && (
        <Alert
          tone="error"
          action={
            <Button size="sm" className="alert-close" onClick={() => load()}>
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}

      {showOpen && canOpen && (
        <Card title="Open a Store Day">
          <p className="hint">
            A Store Day is the audit unit for one store and one business date. Open it here, then
            re-total it once its data has loaded.
          </p>
          <div className="form-row">
            <Field label="Store">
              <StoreSelect value={openStore} onChange={setOpenStore} />
            </Field>
            <Field label="Business date">
              <input
                type="date"
                value={openDate}
                max={today}
                onChange={(e) => setOpenDate(e.target.value)}
              />
            </Field>
            <Button variant="primary" disabled={busy || !openStore || !openDate} onClick={openDay}>
              {busy ? 'Opening…' : 'Open Store Day'}
            </Button>
          </div>
        </Card>
      )}

      {summary && (
        <MetricGrid>
          <MetricTile label="Store Days" value={formatNumber(summary.total)} />
          <MetricTile
            label="Not closed"
            value={formatNumber(summary.total - (summary.by_status.Closed ?? 0))}
          />
          <MetricTile label="Closed" value={formatNumber(summary.by_status.Closed ?? 0)} />
          <MetricTile label="Totals out of date" value={formatNumber(summary.totals_stale)} />
          <MetricTile
            label="Current totals without sign-off"
            value={formatNumber(summary.unsigned_audit_versions ?? 0)}
          />
        </MetricGrid>
      )}

      {attention?.close_summary && (
        <p className="hint">
          {attention.business_date}: {formatNumber(attention.close_summary.closed_automatically)} of{' '}
          {formatNumber(attention.expected_stores)} active stores closed automatically ·{' '}
          {formatNumber(attention.close_summary.closed_by_person)} closed by a person ·{' '}
          {formatNumber(attention.close_summary.open)} open ·{' '}
          {formatNumber(attention.close_summary.not_opened)} with no Store Day
        </p>
      )}

      <Card className="card-open">
        <Tabs
          value={tab}
          onChange={(id) => {
            setTab(id as ListTab);
            setExpanded(null);
          }}
          items={[
            { id: 'days', label: 'Store Days', count: summary?.total },
            { id: 'late', label: 'Late stores', count: attention?.late_total },
            { id: 'stuck', label: 'Needs review', count: attention?.stuck_store_days.length },
          ]}
        />
        {savedViewsError && (
          <Alert tone="warning">
            Saved views are available on this device, but could not sync to your account.
          </Alert>
        )}
        <div className="ac-presets" aria-label="Saved Store Days views">
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
        {tab !== 'days' && attentionError && (
          <Alert
            tone="error"
            action={
              <Button size="sm" onClick={() => void loadAttention()}>
                Retry
              </Button>
            }
          >
            {attentionError}
          </Alert>
        )}
        {tab === 'late' && (
          <>
            <Toolbar
              count={
                attention
                  ? `${formatNumber(attention.late_total)} of ${formatNumber(attention.expected_stores)} active stores`
                  : undefined
              }
            >
              <Field label="Business date">
                <input
                  type="date"
                  value={lateDate}
                  max={today}
                  onChange={(e) => {
                    setLateDate(e.target.value);
                    setLatePage(1);
                  }}
                />
              </Field>
              <Button
                size="sm"
                disabled={attentionExporting || !attention || attention.late_total === 0}
                onClick={() => exportAttention('late')}
              >
                {attentionExporting ? 'Preparing…' : 'Download all late stores'}
              </Button>
            </Toolbar>
            {attentionExportError && <Alert tone="error">{attentionExportError}</Alert>}
            <p className="hint">
              Active stores whose data for this date is not complete: no transactions, no control
              total to check them against, or fewer transactions than the control total.
            </p>
            <DataTable
              columns={[
                {
                  key: 'store',
                  header: 'Store',
                  primary: true,
                  render: (r: LateStore) => r.store_id,
                  csvValue: (r: LateStore) => r.store_id,
                },
                {
                  key: 'state',
                  header: 'Data',
                  render: (r: LateStore) => (
                    <Badge variant={readinessVariant(r.state)} size="sm">
                      {r.state}
                    </Badge>
                  ),
                  csvValue: (r: LateStore) => r.state,
                },
                {
                  key: 'detail',
                  header: 'Detail',
                  render: (r: LateStore) => r.detail,
                  csvValue: (r: LateStore) => r.detail,
                },
                {
                  key: 'tx',
                  header: 'Transactions',
                  numeric: true,
                  render: (r: LateStore) => r.transactions,
                  csvValue: (r: LateStore) => r.transactions,
                },
                {
                  key: 'open',
                  header: 'Store Day',
                  render: (r: LateStore) =>
                    r.store_day_open ? <Link href={dayPath(r)}>Open</Link> : 'Not opened',
                  csvValue: (r: LateStore) => (r.store_day_open ? 'Open' : 'Not opened'),
                },
              ]}
              rows={attention ? attention.late_stores : attentionError ? [] : null}
              rowKey={(r: LateStore) => r.store_id}
              failed={!!attentionError}
              csvFileName="store-days-late-visible.csv"
              emptyTitle="Every active store's data is complete for this date"
              footer={
                attention &&
                attention.late_total > 0 && (
                  <Pager
                    page={attention.late_page}
                    pages={Math.max(1, Math.ceil(attention.late_total / attention.late_page_size))}
                    total={attention.late_total}
                    pageSize={attention.late_page_size}
                    pageSizes={[50, 100, 200, 500]}
                    onPage={setLatePage}
                    onPageSize={(size) => {
                      setLateSize(size);
                      setLatePage(1);
                    }}
                  />
                )
              }
            />
          </>
        )}
        {tab === 'stuck' && (
          <>
            <Toolbar
              count={
                attention?.stuck_truncated
                  ? `Showing the oldest ${attention.stuck_store_days.length}`
                  : undefined
              }
            >
              <Segmented
                value={String(stuckAfter)}
                onChange={(v) => setStuckAfter(Number(v))}
                options={[1, 3, 7, 14].map((n) => ({
                  value: String(n),
                  label: n === 1 ? 'All open' : `Open over ${n} days`,
                }))}
              />
              <Button
                size="sm"
                disabled={attentionExporting || !attention?.stuck_store_days.length}
                onClick={() => exportAttention('stuck')}
              >
                {attentionExporting ? 'Preparing…' : 'Download all days needing review'}
              </Button>
            </Toolbar>
            {attentionExportError && <Alert tone="error">{attentionExportError}</Alert>}
            <p className="hint">
              Store days from before today that were not closed automatically, oldest first, with
              what is holding each one back. A day closes on the next run once that is resolved.
            </p>
            <DataTable
              columns={[
                {
                  key: 'store',
                  header: 'Store',
                  primary: true,
                  stopRowClick: true,
                  render: (d: StuckDay) => <Link href={dayPath(d)}>{d.store_id}</Link>,
                  csvValue: (d: StuckDay) => d.store_id,
                },
                {
                  key: 'date',
                  header: 'Business date',
                  nowrap: true,
                  render: (d: StuckDay) => d.business_date,
                  csvValue: (d: StuckDay) => d.business_date,
                },
                {
                  key: 'age',
                  header: 'Days open',
                  numeric: true,
                  render: (d: StuckDay) => d.days_open,
                  csvValue: (d: StuckDay) => d.days_open,
                },
                {
                  key: 'why',
                  header: 'Holding it back',
                  render: (d: StuckDay) => d.holding_back.join(' · '),
                  csvValue: (d: StuckDay) => d.holding_back.join('; '),
                },
              ]}
              rows={attention ? attention.stuck_store_days : attentionError ? [] : null}
              rowKey={dayKey}
              failed={!!attentionError}
              csvFileName="store-days-stuck-visible.csv"
              onRowClick={(d: StuckDay) =>
                setExpanded((open) => (open === dayKey(d) ? null : dayKey(d)))
              }
              expandedKey={expanded}
              renderDetail={(d: StuckDay) => (
                <StoreDayBrief storeId={d.store_id} businessDate={d.business_date} />
              )}
              emptyTitle="No store day has been open that long"
            />
          </>
        )}
        {tab === 'days' && (
          <>
            <Toolbar
              count={
                days.length > 0
                  ? `On this page: ${onThisPage.ready} ready to close · ${onThisPage.incomplete} data incomplete · ${onThisPage.attention} need attention`
                  : undefined
              }
            >
              <input
                className="grow"
                type="search"
                value={q}
                onChange={(e) => refilter(() => setQ(e.target.value))}
                placeholder="Search store"
                aria-label="Search Store Days"
                maxLength={50}
              />
              <StoreSelect
                value={storeFilter}
                onChange={(v) => refilter(() => setStoreFilter(v))}
                emptyLabel="All stores"
              />
              <input
                type="date"
                aria-label="From"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(e) => refilter(() => setDateFrom(e.target.value))}
              />
              <input
                type="date"
                aria-label="To"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(e) => refilter(() => setDateTo(e.target.value))}
              />
              <select
                aria-label="Data status"
                value={dataStatus}
                onChange={(e) => refilter(() => setDataStatus(e.target.value))}
              >
                <option value="">All data statuses</option>
                {DATA_STATUSES.map((value) => (
                  <option key={value} value={value}>
                    Data: {value}
                  </option>
                ))}
              </select>
              <select
                aria-label="Overall audit status"
                value={overallStatus}
                onChange={(e) => refilter(() => setOverallStatus(e.target.value))}
              >
                <option value="">All overall statuses</option>
                {OVERALL_STATUSES.map((value) => (
                  <option key={value} value={value}>
                    Overall: {value}
                  </option>
                ))}
              </select>
              <select
                aria-label="Audit status"
                value={auditStatus}
                onChange={(e) => refilter(() => setAuditStatus(e.target.value))}
              >
                <option value="">All audit statuses</option>
                {AUDIT_STATUSES.map((value) => (
                  <option key={value} value={value}>
                    Audit: {value}
                  </option>
                ))}
              </select>
            </Toolbar>
            {summary && (
              <Segmented
                value={statusFilter}
                onChange={(v) => refilter(() => setStatusFilter(v))}
                options={[
                  { value: '', label: `All ${formatNumber(summary.total)}` },
                  ...statuses.map((status) => ({
                    value: status,
                    label: `${status} ${formatNumber(summary.by_status[status])}`,
                  })),
                ]}
              />
            )}
          </>
        )}
        {tab === 'days' && (
          <DataTable
            columns={[
              {
                key: 'store',
                header: 'Store',
                primary: true,
                stopRowClick: true,
                render: (d) => <Link href={dayPath(d)}>{d.store_id}</Link>,
                csvValue: (d) => d.store_id,
              },
              {
                key: 'date',
                header: 'Business date',
                nowrap: true,
                render: (d) => d.business_date,
                csvValue: (d) => d.business_date,
              },
              {
                key: 'status',
                header: 'Status',
                render: (d) => (
                  <Badge variant={dayStatusVariant(d.status)} size="sm">
                    {d.status}
                  </Badge>
                ),
                csvValue: (d) => d.status,
              },
              {
                key: 'data',
                header: 'Data',
                render: (d) => <ReadinessBadge r={d.readiness} part="ingest" />,
                csvValue: (d) => d.readiness?.ingest.state ?? '',
              },
              {
                key: 'audit',
                header: 'Audit',
                render: (d) => <ReadinessBadge r={d.readiness} part="audit" />,
                csvValue: (d) => d.readiness?.audit.resa_status ?? d.readiness?.audit.state ?? '',
              },
              {
                key: 'export',
                header: 'Export',
                render: (d) => <ReadinessBadge r={d.readiness} part="export" />,
                csvValue: (d) => d.readiness?.export.state ?? '',
              },
              {
                key: 'ver',
                header: 'Version',
                numeric: true,
                render: (d) => `v${d.audit_version}`,
                csvValue: (d) => d.audit_version,
              },
            ]}
            rows={loading ? null : days}
            failed={loadFailed}
            rowKey={dayKey}
            csvFileName="store-days-visible.csv"
            onRowClick={(d) => setExpanded((open) => (open === dayKey(d) ? null : dayKey(d)))}
            expandedKey={expanded}
            renderDetail={(d) => (
              <StoreDayBrief storeId={d.store_id} businessDate={d.business_date} />
            )}
            emptyTitle={
              summary && summary.total === 0 && !statusFilter
                ? 'No Store Days in this range'
                : 'No Store Days match this filter'
            }
            emptyHint={
              canOpen
                ? 'Widen the date range, or use "Open Store Day" above to start one.'
                : 'Widen the date range. An IT administrator opens new store days.'
            }
            footer={
              <PageNav
                page={page}
                hasMore={hasMore}
                onPage={onPage}
                pageSize={pageSize}
                onPageSize={(size) => refilter(() => setPageSize(size))}
              />
            }
          />
        )}
      </Card>
    </div>
  );
}

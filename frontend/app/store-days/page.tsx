'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
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
}
type ListTab = 'days' | 'late' | 'stuck';
interface Summary {
  total: number;
  by_status: Record<string, number>;
  totals_stale: number;
}
const daysAgo = (n: number) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

function ReadinessBadge({ r, part }: { r?: Readiness; part: 'ingest' | 'audit' | 'export' }) {
  if (!r) return <>—</>;
  const view = r[part];
  return (
    <span title={'detail' in view ? view.detail : `${view.records} record(s)`}>
      <Badge variant={readinessVariant(view.state)} size="sm">
        {view.state}
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
  const today = new Date().toISOString().slice(0, 10);
  const [showOpen, setShowOpen] = useState(false);
  const [openStore, setOpenStore] = useState('');
  const [openDate, setOpenDate] = useState(today);
  const [busy, setBusy] = useState(false);
  // Every filter is sent to the server; the browser never holds more than one page.
  const [statusFilter, setStatusFilter] = useState('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
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
  const yesterday = daysAgo(1);
  const [lateDate, setLateDate] = useState(yesterday);
  const [latePage, setLatePage] = useState(1);
  const [lateSize, setLateSize] = useState(100);
  const [stuckAfter, setStuckAfter] = useState(3);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const loadAttention = useCallback(async () => {
    try {
      const { data } = await api.get('/api/store-days/attention', {
        params: {
          business_date: lateDate,
          stuck_after_days: stuckAfter,
          late_page: latePage,
          late_page_size: lateSize,
        },
      });
      setAttention(data);
    } catch {
      setAttention(null);
    }
  }, [lateDate, stuckAfter, latePage, lateSize]);
  useEffect(() => {
    loadAttention();
  }, [loadAttention]);

  const load = useCallback(async () => {
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
          params: { ...scope, status: statusFilter || undefined, limit: pageSize, page },
        }),
        api.get('/api/store-days/summary', { params: scope }),
      ]);
      setDays(list.data);
      setHasMore(list.headers?.['x-has-more'] === 'true');
      setSummary(counts.data);
      setError(null);
    } catch {
      setError('Unable to load Store Days. Sign in with a Finance or IT role.');
    } finally {
      setLoading(false);
    }
  }, [storeFilter, dateFrom, dateTo, debouncedQ, statusFilter, pageSize, page]);
  useEffect(() => {
    load();
  }, [load]);

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
      attention: days.filter((d) => d.readiness && d.readiness.audit.state !== 'Clean').length,
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

  return (
    <div className="page-container">
      <PageHeader
        title="Store Days & Totals"
        subtitle="Versioned balancing totals for the controlled store-day audit lifecycle. Select a Store Day to see its totals, closure, cases and history."
        actions={
          canOpen && (
            <Button
              variant={showOpen ? 'secondary' : 'primary'}
              onClick={() => setShowOpen(!showOpen)}
            >
              {showOpen ? 'Cancel' : 'Open Store Day'}
            </Button>
          )
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
          <MetricTile label="Store Days" value={summary.total.toLocaleString()} />
          <MetricTile
            label="Not closed"
            value={(summary.total - (summary.by_status.Closed ?? 0)).toLocaleString()}
          />
          <MetricTile label="Closed" value={(summary.by_status.Closed ?? 0).toLocaleString()} />
          <MetricTile label="Totals out of date" value={summary.totals_stale.toLocaleString()} />
        </MetricGrid>
      )}

      <Card>
        <Tabs
          value={tab}
          onChange={(id) => {
            setTab(id as ListTab);
            setExpanded(null);
          }}
          items={[
            { id: 'days', label: 'Store Days', count: summary?.total },
            { id: 'late', label: 'Late stores', count: attention?.late_total },
            { id: 'stuck', label: 'Stuck store days', count: attention?.stuck_store_days.length },
          ]}
        />
        {tab === 'late' && (
          <>
            <Toolbar
              count={
                attention
                  ? `${attention.late_total.toLocaleString()} of ${attention.expected_stores.toLocaleString()} active stores`
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
            </Toolbar>
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
                },
                {
                  key: 'state',
                  header: 'Data',
                  render: (r: LateStore) => (
                    <Badge variant={readinessVariant(r.state)} size="sm">
                      {r.state}
                    </Badge>
                  ),
                },
                { key: 'detail', header: 'Detail', render: (r: LateStore) => r.detail },
                {
                  key: 'tx',
                  header: 'Transactions',
                  numeric: true,
                  render: (r: LateStore) => r.transactions,
                },
                {
                  key: 'open',
                  header: 'Store Day',
                  render: (r: LateStore) =>
                    r.store_day_open ? <Link href={dayPath(r)}>Open</Link> : 'Not opened',
                },
              ]}
              rows={attention ? attention.late_stores : null}
              rowKey={(r: LateStore) => r.store_id}
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
                  label: `Open over ${n} day${n === 1 ? '' : 's'}`,
                }))}
              />
            </Toolbar>
            <DataTable
              columns={[
                {
                  key: 'store',
                  header: 'Store',
                  primary: true,
                  stopRowClick: true,
                  render: (d: StuckDay) => <Link href={dayPath(d)}>{d.store_id}</Link>,
                },
                {
                  key: 'date',
                  header: 'Business date',
                  nowrap: true,
                  render: (d: StuckDay) => d.business_date,
                },
                {
                  key: 'age',
                  header: 'Days open',
                  numeric: true,
                  render: (d: StuckDay) => d.days_open,
                },
                {
                  key: 'why',
                  header: 'Holding it back',
                  render: (d: StuckDay) => d.holding_back.join(' · '),
                },
              ]}
              rows={attention ? attention.stuck_store_days : null}
              rowKey={dayKey}
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
            </Toolbar>
            {summary && (
              <Segmented
                value={statusFilter}
                onChange={(v) => refilter(() => setStatusFilter(v))}
                options={[
                  { value: '', label: `All ${summary.total.toLocaleString()}` },
                  ...statuses.map((status) => ({
                    value: status,
                    label: `${status} ${summary.by_status[status].toLocaleString()}`,
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
              },
              {
                key: 'date',
                header: 'Business date',
                nowrap: true,
                render: (d) => d.business_date,
              },
              {
                key: 'status',
                header: 'Status',
                render: (d) => (
                  <Badge variant={dayStatusVariant(d.status)} size="sm">
                    {d.status}
                  </Badge>
                ),
              },
              {
                key: 'data',
                header: 'Data',
                render: (d) => <ReadinessBadge r={d.readiness} part="ingest" />,
              },
              {
                key: 'audit',
                header: 'Audit',
                render: (d) => <ReadinessBadge r={d.readiness} part="audit" />,
              },
              {
                key: 'export',
                header: 'Export',
                render: (d) => <ReadinessBadge r={d.readiness} part="export" />,
              },
              {
                key: 'ver',
                header: 'Version',
                numeric: true,
                render: (d) => `v${d.audit_version}`,
              },
            ]}
            rows={loading ? null : days}
            rowKey={dayKey}
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

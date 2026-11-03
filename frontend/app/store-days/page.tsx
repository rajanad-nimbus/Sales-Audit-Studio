'use client';

import { useCallback, useEffect, useState } from 'react';
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
  Segmented,
  Tabs,
  Toolbar,
} from '@/components/ui';
import { StoreSelect } from '@/components/StoreSelect';
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
  stuck_after_days: number;
  stuck_store_days: StuckDay[];
  stuck_truncated: boolean;
}
type ListTab = 'days' | 'late' | 'stuck';

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
  const canOpen = !!me && (me.role === 'it' || me.role === 'admin');
  const [days, setDays] = useState<StoreDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const today = new Date().toISOString().slice(0, 10);
  const [showOpen, setShowOpen] = useState(false);
  const [openStore, setOpenStore] = useState('');
  const [openDate, setOpenDate] = useState(today);
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [q, setQ] = useState('');
  const [storeFilter, setStoreFilter] = useState('');
  const [tab, setTab] = useState<ListTab>('days');
  const [attention, setAttention] = useState<Attention | null>(null);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const [lateDate, setLateDate] = useState(yesterday);
  const [stuckAfter, setStuckAfter] = useState(3);

  const loadAttention = useCallback(async () => {
    try {
      const { data } = await api.get('/api/store-days/attention', {
        params: { business_date: lateDate, stuck_after_days: stuckAfter },
      });
      setAttention(data);
    } catch {
      setAttention(null);
    }
  }, [lateDate, stuckAfter]);
  useEffect(() => {
    loadAttention();
  }, [loadAttention]);

  const load = useCallback(async () => {
    try {
      const d = await api.get('/api/store-days');
      setDays(d.data);
      setError(null);
    } catch {
      setError('Unable to load Store Days. Sign in with a Finance or IT role.');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const statuses = Array.from(new Set(days.map((d) => d.status))).sort();
  const shownDays = days
    .filter(
      (d) =>
        (!statusFilter || d.status === statusFilter) &&
        (!storeFilter || d.store_id === storeFilter) &&
        (!q.trim() ||
          `${d.store_id} ${d.business_date}`.toLowerCase().includes(q.trim().toLowerCase()))
    )
    .sort(
      (a, b) =>
        b.business_date.localeCompare(a.business_date) || a.store_id.localeCompare(b.store_id)
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

      {days.length > 0 && (
        <MetricGrid>
          <MetricTile label="Store Days" value={days.length} />
          <MetricTile
            label="Ready to close"
            value={days.filter((d) => d.readiness?.ready_to_close).length}
          />
          <MetricTile
            label="Data incomplete"
            value={
              days.filter((d) => d.readiness && d.readiness.ingest.state !== 'Complete').length
            }
          />
          <MetricTile
            label="Audit needs attention"
            value={
              days.filter((d) => d.readiness && !['Clean'].includes(d.readiness.audit.state)).length
            }
          />
          <MetricTile label="Closed" value={days.filter((d) => d.status === 'Closed').length} />
        </MetricGrid>
      )}

      <Card>
        <Tabs
          value={tab}
          onChange={(id) => setTab(id as ListTab)}
          items={[
            { id: 'days', label: 'Store Days', count: shownDays.length },
            { id: 'late', label: 'Late stores', count: attention?.late_stores.length },
            { id: 'stuck', label: 'Stuck store days', count: attention?.stuck_store_days.length },
          ]}
        />
        {tab === 'late' && (
          <>
            <Toolbar
              count={
                attention
                  ? `${attention.late_stores.length} of ${attention.expected_stores} active stores`
                  : undefined
              }
            >
              <Field label="Business date">
                <input
                  type="date"
                  value={lateDate}
                  max={today}
                  onChange={(e) => setLateDate(e.target.value)}
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
              onRowClick={(d: StuckDay) => router.push(dayPath(d))}
              emptyTitle="No store day has been open that long"
            />
          </>
        )}
        {tab === 'days' && days.length > 0 && (
          <Toolbar>
            <input
              className="grow"
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search store or date"
              aria-label="Search Store Days"
            />
            <StoreSelect value={storeFilter} onChange={setStoreFilter} emptyLabel="All stores" />
            <Segmented
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: '', label: `All ${days.length}` },
                ...statuses.map((s) => ({
                  value: s,
                  label: `${s} ${days.filter((d) => d.status === s).length}`,
                })),
              ]}
            />
          </Toolbar>
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
            rows={loading ? null : shownDays}
            rowKey={dayKey}
            onRowClick={(d) => router.push(dayPath(d))}
            emptyTitle={days.length === 0 ? 'No Store Days yet' : 'No Store Days match this filter'}
            emptyHint={
              days.length === 0
                ? canOpen
                  ? 'Use "Open Store Day" above to start one.'
                  : 'An IT administrator needs to open one.'
                : undefined
            }
          />
        )}
      </Card>
    </div>
  );
}

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
  Toolbar,
} from '@/components/ui';
import { StoreSelect } from '@/components/StoreSelect';
import { apiError, dayKey, dayPath, dayStatusVariant } from '@/lib/storeDays';
import type { StoreDay } from '@/lib/storeDays';

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
            label="Totals out of date"
            value={days.filter((d) => d.totals_stale).length}
          />
          <MetricTile
            label="Awaiting approval"
            value={days.filter((d) => d.status === 'Pending').length}
          />
          <MetricTile label="Closed" value={days.filter((d) => d.status === 'Closed').length} />
        </MetricGrid>
      )}

      <Card title={`Store Days (${shownDays.length})`}>
        {days.length > 0 && (
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
        <DataTable
          columns={[
            {
              key: 'store',
              header: 'Store',
              primary: true,
              stopRowClick: true,
              render: (d) => <Link href={dayPath(d)}>{d.store_id}</Link>,
            },
            { key: 'date', header: 'Business date', nowrap: true, render: (d) => d.business_date },
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
              key: 'totals',
              header: 'Totals',
              render: (d) =>
                d.totals_stale ? (
                  <Badge variant="warning" size="sm">
                    Out of date
                  </Badge>
                ) : (
                  <Badge variant="success" size="sm">
                    Up to date
                  </Badge>
                ),
            },
            { key: 'ver', header: 'Version', numeric: true, render: (d) => `v${d.audit_version}` },
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
      </Card>
    </div>
  );
}

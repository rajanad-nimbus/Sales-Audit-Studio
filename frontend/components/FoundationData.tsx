'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { Alert, Badge, Button, Card, DataTable, Field, PageHeader, Tabs } from '@/components/ui';

type Ref = {
  id: string;
  category: string;
  code: string;
  name: string;
  active: boolean;
  attributes: Record<string, unknown> | null;
  updated_at: string;
};
type SyncResult = {
  stores: number;
  items: number;
  created: number;
  updated: number;
  deactivated: number;
  skipped_manual: number;
  duplicates: number;
};

/** Each tab shows one or more reference categories. Synced tabs are read-only: Shopify is the source. */
const GROUPS = [
  {
    id: 'stores',
    label: 'Stores',
    cats: ['Store'],
    synced: true,
    blurb: 'Your shop and its locations, synced from Shopify.',
  },
  {
    id: 'products',
    label: 'Products',
    cats: ['Item'],
    synced: true,
    blurb: 'Every product variant in the Shopify catalog, keyed by SKU.',
  },
  {
    id: 'calendar',
    label: 'Calendar',
    cats: ['BusinessCalendar'],
    synced: false,
    blurb: 'Business calendar entries: trading days, holidays and closures.',
  },
  {
    id: 'tenders',
    label: 'Tenders',
    cats: ['Tender'],
    synced: false,
    blurb: 'Payment types accepted at the till.',
  },
  {
    id: 'registers',
    label: 'Registers & cashiers',
    cats: ['Register', 'Cashier'],
    synced: false,
    blurb: 'The tills and the people who use them.',
  },
  {
    id: 'accounts',
    label: 'Accounts & tax',
    cats: ['BankAccount', 'GLAccount', 'TaxCode'],
    synced: false,
    blurb: 'Bank and general-ledger accounts, and tax codes.',
  },
] as const;

const MAX_ROWS = 200;
const LABELS: Record<string, string> = {
  Store: 'Store',
  Item: 'Product',
  BusinessCalendar: 'Calendar',
  Tender: 'Tender',
  Register: 'Register',
  Cashier: 'Cashier',
  BankAccount: 'Bank account',
  GLAccount: 'GL account',
  TaxCode: 'Tax code',
};
const apiError = (e: unknown, fallback: string) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
const sourceOf = (r: Ref) => (r.attributes?.source as string | undefined) ?? 'Manual';

export function FoundationData() {
  const toast = useToast();
  const [refs, setRefs] = useState<Ref[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string>(GROUPS[0].id);
  const [query, setQuery] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState<SyncResult | null>(null);
  const [form, setForm] = useState({ category: '', code: '', name: '' });

  const load = useCallback(async () => {
    try {
      setRefs((await api.get('/api/foundation')).data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load foundation data.'));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const group = GROUPS.find((g) => g.id === tab) ?? GROUPS[0];
  const category = form.category || group.cats[0];
  const inGroup = useMemo(
    () => (refs ?? []).filter((r) => (group.cats as readonly string[]).includes(r.category)),
    [refs, group]
  );
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? inGroup.filter((r) => `${r.code} ${r.name}`.toLowerCase().includes(q)) : inGroup;
  }, [inGroup, query]);
  const countOf = (cats: readonly string[]) =>
    (refs ?? []).filter((r) => cats.includes(r.category)).length;

  const pick = (id: string) => {
    setTab(id);
    setQuery('');
    setForm({ category: '', code: '', name: '' });
  };
  const syncShopify = async () => {
    setSyncing(true);
    setError(null);
    try {
      const { data } = await api.post('/api/ingest/shopify/sync-reference');
      setSynced(data);
      await load();
      toast('Shopify stores and products synced.', 'success');
    } catch (e) {
      setError(apiError(e, 'Shopify sync failed. Check the connection in Configuration.'));
    } finally {
      setSyncing(false);
    }
  };
  const add = async () => {
    try {
      await api.post('/api/foundation', {
        category,
        code: form.code,
        name: form.name,
        active: true,
        attributes: {},
      });
      setForm({ ...form, code: '', name: '' });
      await load();
      toast('Saved.', 'success');
    } catch (e) {
      setError(apiError(e, 'Could not save.'));
    }
  };

  return (
    <div className="page-container">
      <PageHeader
        title="Foundation data"
        subtitle="The stores, products, calendar and other reference data that audits and totals rely on."
      />
      <Tabs
        value={tab}
        onChange={pick}
        items={GROUPS.map((g) => ({ id: g.id, label: g.label, count: countOf(g.cats) }))}
      />
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      {group.synced && synced && (
        <Alert tone="success" onDismiss={() => setSynced(null)}>
          Synced {synced.stores} store(s) and {synced.items} product variant(s): {synced.created}{' '}
          new, {synced.updated} changed, {synced.deactivated} no longer in Shopify
          {synced.skipped_manual > 0 && `, ${synced.skipped_manual} left alone (entered by hand)`}
          {synced.duplicates > 0 && `, ${synced.duplicates} skipped (duplicate SKU)`}.
        </Alert>
      )}
      <Card title={group.label}>
        <p className="hint">{group.blurb}</p>
        {group.synced ? (
          <div className="form-row">
            <Button variant="primary" onClick={syncShopify} disabled={syncing}>
              {syncing ? 'Syncing…' : 'Sync from Shopify'}
            </Button>
            <span className="hint">
              Read-only here. Records you add elsewhere are never overwritten, and items removed
              from Shopify are marked inactive.
            </span>
          </div>
        ) : (
          <div className="form-row">
            {group.cats.length > 1 && (
              <Field label="Type">
                <select
                  value={category}
                  onChange={(e) => setForm({ ...form, category: e.target.value })}
                >
                  {group.cats.map((c) => (
                    <option key={c} value={c}>
                      {LABELS[c]}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Code">
              <input
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
              />
            </Field>
            <div className="grow">
              <Field label="Name">
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </Field>
            </div>
            <Button
              variant="primary"
              disabled={!form.code.trim() || !form.name.trim()}
              onClick={add}
            >
              Add
            </Button>
          </div>
        )}
        {inGroup.length > 8 && (
          <input
            aria-label={`Search ${group.label}`}
            placeholder={`Search ${group.label.toLowerCase()} by code or name`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
        <DataTable
          columns={[
            { key: 'code', header: 'Code', primary: true, render: (r) => r.code },
            { key: 'name', header: 'Name', render: (r) => r.name },
            ...(group.cats.length > 1
              ? [{ key: 'type', header: 'Type', render: (r: Ref) => LABELS[r.category] }]
              : []),
            {
              key: 'source',
              header: 'Source',
              render: (r) => (
                <Badge variant={sourceOf(r) === 'Shopify' ? 'info' : 'secondary'} size="sm">
                  {sourceOf(r)}
                </Badge>
              ),
            },
            {
              key: 'status',
              header: 'Status',
              render: (r) => (
                <Badge variant={r.active ? 'success' : 'secondary'} size="sm">
                  {r.active ? 'Active' : 'Inactive'}
                </Badge>
              ),
            },
            {
              key: 'updated',
              header: 'Updated',
              nowrap: true,
              render: (r) => new Date(r.updated_at).toLocaleDateString(),
            },
          ]}
          rows={refs === null ? null : shown.slice(0, MAX_ROWS)}
          rowKey={(r) => r.id}
          emptyTitle={`No ${group.label.toLowerCase()} yet`}
          emptyHint={
            group.synced ? 'Use "Sync from Shopify" to load them.' : 'Add the first one above.'
          }
        />
        {shown.length > MAX_ROWS && (
          <p className="hint">
            Showing the first {MAX_ROWS} of {shown.length}. Use search to narrow the list.
          </p>
        )}
      </Card>
    </div>
  );
}

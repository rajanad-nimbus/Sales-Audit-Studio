'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { StoreSelect } from '@/components/StoreSelect';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  Field,
  MetricGrid,
  MetricTile,
  Segmented,
  Toolbar,
} from '@/components/ui';
import type { BadgeVariant } from '@/components/ui';
import { apiError } from '@/lib/storeDays';

interface Voucher {
  voucher_number: string;
  issued_store_id: string;
  issued_on: string;
  expires_on: string | null;
  amount: string;
  currency: string;
  status: 'Issued' | 'Expired' | 'Redeemed' | 'Escheated';
  redeemed_store_id: string | null;
  redeemed_on: string | null;
}
type Totals = Record<string, { vouchers: number; amount: string }>;
interface Summary {
  outstanding: Totals;
  expired: Totals;
}

const variant = (s: Voucher['status']): BadgeVariant =>
  s === 'Issued'
    ? 'success'
    : s === 'Expired'
      ? 'warning'
      : s === 'Redeemed'
        ? 'info'
        : 'secondary';
const sum = (t: Totals | undefined) =>
  Object.entries(t ?? {})
    .map(([c, v]) => `${money(v.amount, c)} (${v.vouchers})`)
    .join(' · ') || '—';

/** Vouchers issued by stores: redeemed once, and escheated when they expire unclaimed. */
export function VoucherLedger() {
  const toast = useToast();
  const { me } = useMe();
  const canWrite = !!me && ['finance', 'it', 'store_manager', 'admin'].includes(me.role);
  const canEscheat = !!me && ['finance', 'admin'].includes(me.role);
  const [rows, setRows] = useState<Voucher[] | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ number: '', store: '', amount: '', expires: '' });
  const [redeemStore, setRedeemStore] = useState('');

  const load = useCallback(async () => {
    try {
      const [list, totals] = await Promise.all([
        api.get<Voucher[]>('/api/vouchers', {
          params: { status: status || undefined, q: q.trim() || undefined },
        }),
        api.get<Summary>('/api/vouchers/summary'),
      ]);
      setRows(list.data);
      setSummary(totals.data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load vouchers.'));
    }
  }, [status, q]);
  useEffect(() => {
    load();
  }, [load]);

  const run = async (call: () => Promise<unknown>, done: string, after?: () => void) => {
    setBusy(true);
    try {
      await call();
      toast(done, 'success');
      after?.();
      await load();
    } catch (e) {
      setError(apiError(e, 'That did not work.'));
    } finally {
      setBusy(false);
    }
  };

  const expired = (rows ?? []).filter((v) => v.status === 'Expired');

  return (
    <div className="cc-card">
      <h3>Vouchers</h3>
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}
      <MetricGrid>
        <MetricTile label="Outstanding" value={sum(summary?.outstanding)} />
        <MetricTile label="Expired, unclaimed" value={sum(summary?.expired)} />
      </MetricGrid>
      {canWrite && (
        <div className="form-row">
          <Field label="Voucher number">
            <input
              value={form.number}
              onChange={(e) => setForm({ ...form, number: e.target.value })}
            />
          </Field>
          <Field label="Issuing store">
            <StoreSelect value={form.store} onChange={(v) => setForm({ ...form, store: v })} />
          </Field>
          <Field label="Amount">
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })}
            />
          </Field>
          <Field label="Expires">
            <input
              type="date"
              value={form.expires}
              onChange={(e) => setForm({ ...form, expires: e.target.value })}
            />
          </Field>
          <Button
            variant="primary"
            disabled={busy || !form.number.trim() || !form.store || !form.amount}
            onClick={() =>
              void run(
                () =>
                  api.post('/api/vouchers', {
                    voucher_number: form.number,
                    store_id: form.store,
                    amount: form.amount,
                    expires_on: form.expires || null,
                  }),
                `Voucher ${form.number} issued.`,
                () => setForm({ number: '', store: form.store, amount: '', expires: '' })
              )
            }
          >
            Issue
          </Button>
        </div>
      )}
      <Toolbar>
        <Segmented
          value={status}
          onChange={setStatus}
          options={[
            { value: '', label: 'All' },
            { value: 'Issued', label: 'Outstanding' },
            { value: 'Expired', label: 'Expired' },
            { value: 'Redeemed', label: 'Redeemed' },
            { value: 'Escheated', label: 'Escheated' },
          ]}
        />
        <input
          className="grow"
          type="search"
          placeholder="Search voucher number"
          aria-label="Search vouchers"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {canWrite && (
          <StoreSelect value={redeemStore} onChange={setRedeemStore} emptyLabel="Redeem at…" />
        )}
        {canEscheat && (
          <Button
            disabled={busy || expired.length === 0}
            title="Escheat every expired voucher shown"
            onClick={() =>
              void run(
                () =>
                  api.post('/api/vouchers/escheat', {
                    voucher_numbers: expired.map((v) => v.voucher_number),
                  }),
                `${expired.length} voucher(s) escheated.`
              )
            }
          >
            Escheat {expired.length || ''} expired
          </Button>
        )}
      </Toolbar>
      <DataTable
        rows={rows}
        rowKey={(v) => v.voucher_number}
        emptyTitle="No vouchers"
        columns={[
          { key: 'n', header: 'Voucher', primary: true, render: (v) => v.voucher_number },
          { key: 's', header: 'Issued at', render: (v) => `${v.issued_store_id} · ${v.issued_on}` },
          { key: 'a', header: 'Amount', numeric: true, render: (v) => money(v.amount, v.currency) },
          { key: 'e', header: 'Expires', nowrap: true, render: (v) => v.expires_on ?? 'Never' },
          {
            key: 'st',
            header: 'Status',
            render: (v) => (
              <>
                <Badge variant={variant(v.status)} size="sm">
                  {v.status}
                </Badge>
                {v.redeemed_store_id && (
                  <small className="act-sub" style={{ display: 'block' }}>
                    {v.redeemed_store_id} · {v.redeemed_on}
                  </small>
                )}
              </>
            ),
          },
          ...(canWrite
            ? [
                {
                  key: 'redeem',
                  header: '',
                  stopRowClick: true,
                  render: (v: Voucher) =>
                    v.status === 'Issued' ? (
                      <Button
                        size="sm"
                        disabled={busy || !redeemStore}
                        title={redeemStore ? '' : 'Choose the redeeming store first'}
                        onClick={() =>
                          void run(
                            () =>
                              api.post(
                                `/api/vouchers/${encodeURIComponent(v.voucher_number)}/redeem`,
                                {
                                  store_id: redeemStore,
                                }
                              ),
                            `Voucher ${v.voucher_number} redeemed.`
                          )
                        }
                      >
                        Redeem
                      </Button>
                    ) : null,
                },
              ]
            : []),
        ]}
      />
    </div>
  );
}

'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, money } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { formatBusinessDate as formatDate } from '@/lib/preferences';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { StoreSelect } from '@/components/StoreSelect';
import { VoucherJurisdictions } from '@/components/VoucherJurisdictions';
import { VoucherRouting } from '@/components/VoucherRouting';
import { RetiredVouchers } from '@/components/RetiredVouchers';
import type { RetiredVoucher } from '@/components/RetiredVouchers';
import { locationDraft, locationsPayload, validLocations } from '@/lib/vouchers';
import type { RoutingDecision, VoucherLocations } from '@/lib/vouchers';
import {
  Alert,
  Badge,
  Button,
  DataTable,
  Field,
  MetricGrid,
  MetricTile,
  Modal,
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
  tender_type: string;
  eligible_on: string | null;
  escheat_eligible: boolean;
  eligibility_basis: string;
  escheat_locations?: VoucherLocations;
  routing?: RoutingDecision | null;
  routing_token?: string | null;
  status: 'Issued' | 'Expired' | 'Redeemed' | 'Escheated';
  redeemed_store_id: string | null;
  redeemed_on: string | null;
  redeemed_transaction_id: string | null;
}
type Totals = Record<string, { vouchers: number; amount: string }>;
interface Summary {
  outstanding: Totals;
  expired: Totals;
}
interface RedemptionCandidate {
  id: string;
  business_date: string;
  store_id: string;
  transaction_type: string;
  transaction_number: string | null;
  register_id: string | null;
  amount: string;
  currency: string;
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

/** Vouchers issued by stores, with tender aging eligibility and Finance review. */
export function VoucherLedger() {
  const toast = useToast();
  const { me } = useMe();
  const canWrite = !!me && ['finance', 'it', 'store_manager', 'admin'].includes(me.role);
  const canEscheat = !!me && ['finance', 'admin'].includes(me.role);
  const canEditLocations =
    !!me && ['analyst', 'finance', 'it', 'store_manager', 'admin'].includes(me.role);
  const [rows, setRows] = useState<Voucher[] | null>(null);
  const [retiredRows, setRetiredRows] = useState<RetiredVoucher[] | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [filtersInitialized, setFiltersInitialized] = useState(false);
  const [status, setStatus] = useState('');
  const [issuingStoreId, setIssuingStoreId] = useState('');
  const [q, setQ] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [form, setForm] = useState({
    number: '',
    store: '',
    amount: '',
    expires: '',
    tender: 'Voucher',
  });
  const [redeemStore, setRedeemStore] = useState('');
  const [redemptionEditor, setRedemptionEditor] = useState<Voucher | null>(null);
  const [redemptionCandidates, setRedemptionCandidates] = useState<RedemptionCandidate[] | null>(
    null
  );
  const [redemptionCandidatesTruncated, setRedemptionCandidatesTruncated] = useState(false);
  const [redemptionCandidatesError, setRedemptionCandidatesError] = useState<string | null>(null);
  const [selectedTransactionId, setSelectedTransactionId] = useState('');
  const [reviewRows, setReviewRows] = useState<Voucher[] | null>(null);
  const [escheatNote, setEscheatNote] = useState('');
  const [issueLocations, setIssueLocations] = useState(locationDraft);
  const [locationEditor, setLocationEditor] = useState<Voucher | null>(null);
  const [editLocations, setEditLocations] = useState(locationDraft);
  const closeLocations = useCallback(() => {
    if (!busy) setLocationEditor(null);
  }, [busy]);
  const closeReview = useCallback(() => {
    if (!busy) setReviewRows(null);
  }, [busy]);
  const closeRedemption = useCallback(() => {
    if (!busy) setRedemptionEditor(null);
  }, [busy]);

  const load = useCallback(async () => {
    try {
      if (status === 'Retired') {
        const [list, totals] = await Promise.all([
          api.get<RetiredVoucher[]>('/api/vouchers/retired', {
            params: { q: q.trim() || undefined, store_id: issuingStoreId || undefined },
          }),
          api.get<Summary>('/api/vouchers/summary'),
        ]);
        setRetiredRows(list.data);
        setSummary(totals.data);
        setError(null);
        return;
      }
      const [list, totals] = await Promise.all([
        api.get<Voucher[]>('/api/vouchers', {
          params: {
            status: status || undefined,
            q: q.trim() || undefined,
            store_id: issuingStoreId || undefined,
          },
        }),
        api.get<Summary>('/api/vouchers/summary'),
      ]);
      setRows(list.data);
      setSummary(totals.data);
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load vouchers.'));
    }
  }, [status, q, issuingStoreId]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const voucherNumber = params.get('voucher');
    setStatus(params.get('status') ?? '');
    setIssuingStoreId(params.get('issued_store_id') ?? '');
    setQ(voucherNumber ?? params.get('q') ?? '');
    setFiltersInitialized(true);
  }, []);
  useEffect(() => {
    if (filtersInitialized) void load();
  }, [filtersInitialized, load]);
  useEffect(() => {
    if (!redemptionEditor || !redeemStore) {
      setRedemptionCandidates(null);
      setRedemptionCandidatesTruncated(false);
      setSelectedTransactionId('');
      setRedemptionCandidatesError(null);
      return;
    }
    let active = true;
    setRedemptionCandidates(null);
    setRedemptionCandidatesTruncated(false);
    setSelectedTransactionId('');
    setRedemptionCandidatesError(null);
    api
      .get<{ items: RedemptionCandidate[]; truncated: boolean }>(
        '/api/vouchers/redeem-candidates',
        {
          params: { voucher_number: redemptionEditor.voucher_number, store_id: redeemStore },
        }
      )
      .then(({ data }) => {
        if (!active) return;
        setRedemptionCandidates(data.items);
        setRedemptionCandidatesTruncated(data.truncated);
      })
      .catch((e) => {
        if (active)
          setRedemptionCandidatesError(apiError(e, 'Could not find matching transactions.'));
      });
    return () => {
      active = false;
    };
  }, [redemptionEditor, redeemStore]);

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

  const exportLedger = async () => {
    setExporting(true);
    try {
      const retired = status === 'Retired';
      const { data, headers } = await api.get(
        retired ? '/api/vouchers/retired/export.csv' : '/api/vouchers/export.csv',
        {
          params: {
            status: !retired ? status || undefined : undefined,
            q: q.trim() || undefined,
            store_id: issuingStoreId || undefined,
          },
          responseType: 'blob',
        }
      );
      downloadBlob(retired ? 'retired-vouchers.csv' : 'vouchers.csv', data);
      const truncatedHeader = retired
        ? 'x-retired-voucher-export-truncated'
        : 'x-voucher-export-truncated';
      if (headers?.[truncatedHeader] === 'true') {
        toast(
          'The export reached 50,000 rows. Narrow the voucher search and export again.',
          'info'
        );
      } else {
        toast('Voucher export downloaded.', 'success');
      }
    } catch (e) {
      setError(apiError(e, 'Could not export vouchers.'));
    } finally {
      setExporting(false);
    }
  };

  const updateFilterUrl = (key: string, value: string) => {
    const url = new URL(window.location.href);
    url.searchParams.delete('voucher');
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    window.history.replaceState(null, '', url);
  };

  const eligible = status === 'Retired' ? [] : (rows ?? []).filter((v) => v.escheat_eligible);

  return (
    <div className="cc-card" id="vouchers">
      <h3>Vouchers</h3>
      {error && !reviewRows && !locationEditor && !redemptionEditor && (
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
          <Field label="Tender code">
            <input
              value={form.tender}
              maxLength={100}
              onChange={(e) => setForm({ ...form, tender: e.target.value })}
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
            disabled={
              busy ||
              !form.number.trim() ||
              !form.store ||
              !form.amount ||
              !form.tender.trim() ||
              !validLocations(issueLocations)
            }
            onClick={() =>
              void run(
                () =>
                  api.post('/api/vouchers', {
                    voucher_number: form.number,
                    store_id: form.store,
                    amount: form.amount,
                    tender_type: form.tender.trim(),
                    expires_on: form.expires || null,
                    ...(Object.keys(locationsPayload(issueLocations)).length
                      ? { locations: locationsPayload(issueLocations) }
                      : {}),
                  }),
                `Voucher ${form.number} issued.`,
                () => {
                  setForm({
                    number: '',
                    store: form.store,
                    amount: '',
                    expires: '',
                    tender: form.tender,
                  });
                  setIssueLocations(locationDraft());
                }
              )
            }
          >
            Issue
          </Button>
        </div>
      )}
      {canWrite && (
        <details>
          <summary>Voucher jurisdictions</summary>
          <p className="hint">
            The issuing store jurisdiction is captured from foundation data unless supplied here.
          </p>
          <VoucherJurisdictions
            value={issueLocations}
            onChange={setIssueLocations}
            disabled={busy}
          />
        </details>
      )}
      <Toolbar>
        <Segmented
          value={status}
          onChange={(value) => {
            setStatus(value);
            updateFilterUrl('status', value);
          }}
          options={[
            { value: '', label: 'All' },
            { value: 'Issued', label: 'Outstanding' },
            { value: 'Expired', label: 'Expired' },
            { value: 'Eligible', label: 'Eligible for escheat' },
            { value: 'Redeemed', label: 'Redeemed' },
            { value: 'Escheated', label: 'Escheated' },
            { value: 'Retired', label: 'Retired' },
          ]}
        />
        <input
          className="grow"
          type="search"
          placeholder="Search voucher number"
          aria-label="Search vouchers"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            updateFilterUrl('q', e.target.value);
          }}
        />
        <StoreSelect
          value={issuingStoreId}
          onChange={(value) => {
            setIssuingStoreId(value);
            updateFilterUrl('issued_store_id', value);
          }}
          emptyLabel="All issuing stores"
        />
        <Button variant="secondary" disabled={exporting} onClick={() => void exportLedger()}>
          {exporting ? 'Preparing CSV…' : 'Export matching CSV'}
        </Button>
        {canWrite && (
          <StoreSelect value={redeemStore} onChange={setRedeemStore} emptyLabel="Redeem at…" />
        )}
        {canEscheat && (
          <Button
            disabled={busy || eligible.length === 0}
            title="Review the eligible vouchers currently shown"
            onClick={() => {
              setError(null);
              setEscheatNote('');
              setReviewRows(eligible);
            }}
          >
            Review {eligible.length || ''} for escheat
          </Button>
        )}
      </Toolbar>
      {status === 'Retired' ? (
        <RetiredVouchers
          rows={retiredRows}
          detachTitle={`Retired vouchers${issuingStoreId ? ` · Store ${issuingStoreId}` : ''}`}
        />
      ) : (
        <DataTable
          rows={rows}
          rowKey={(v) => v.voucher_number}
          emptyTitle="No vouchers"
          csvFileName="vouchers-visible.csv"
          detachTitle={`Vouchers · ${status || 'all statuses'}${issuingStoreId ? ` · Store ${issuingStoreId}` : ''}`}
          columns={[
            {
              key: 'n',
              header: 'Voucher',
              primary: true,
              render: (v) => v.voucher_number,
              csvValue: (v) => v.voucher_number,
            },
            { key: 't', header: 'Tender', render: (v) => v.tender_type, csvValue: (v) => v.tender_type },
            {
              key: 's',
              header: 'Issued at',
              render: (v) => `${v.issued_store_id} · ${formatDate(v.issued_on)}`,
              csvValue: (v) => `${v.issued_store_id} · ${v.issued_on}`,
            },
            {
              key: 'a',
              header: 'Amount',
              numeric: true,
              render: (v) => money(v.amount, v.currency),
              csvValue: (v) => `${v.currency} ${v.amount}`,
            },
            {
              key: 'e',
              header: 'Expires',
              nowrap: true,
              render: (v) => (v.expires_on ? formatDate(v.expires_on) : 'Never'),
              csvValue: (v) => v.expires_on ?? 'Never',
            },
            {
              key: 'eligible',
              header: 'Escheat eligibility',
              render: (v) =>
                ['Redeemed', 'Escheated'].includes(v.status) ? (
                  '—'
                ) : v.eligible_on ? (
                  <>
                    {formatDate(v.eligible_on)}
                    <small className="act-sub" style={{ display: 'block' }}>
                      {v.eligibility_basis}
                      {v.escheat_eligible ? ' · Eligible' : ''}
                    </small>
                  </>
                ) : (
                  'No date configured'
                ),
              csvValue: (v) =>
                ['Redeemed', 'Escheated'].includes(v.status)
                  ? ''
                  : `${v.eligible_on ?? 'No date configured'} · ${v.eligibility_basis}${v.escheat_eligible ? ' · Eligible' : ''}`,
            },
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
                  {v.redeemed_transaction_id && (
                    <Link
                      href={`/transactions/${encodeURIComponent(v.redeemed_transaction_id)}`}
                      style={{ display: 'block' }}
                    >
                      Open transaction
                    </Link>
                  )}
                </>
              ),
              csvValue: (v) =>
                `${v.status}${v.redeemed_store_id ? ` · ${v.redeemed_store_id} · ${v.redeemed_on ?? ''}` : ''}${v.redeemed_transaction_id ? ` · ${v.redeemed_transaction_id}` : ''}`,
            },
            {
              key: 'routing',
              header: 'Escheat destination',
              render: (v) =>
                v.status === 'Redeemed' ? (
                  '—'
                ) : (
                  <VoucherRouting decision={v.routing} explain={v.status === 'Escheated'} />
                ),
              csvValue: (v) => (v.status === 'Redeemed' ? '' : JSON.stringify(v.routing ?? {})),
            },
            ...(canEditLocations
              ? [
                  {
                    key: 'locations',
                    header: '',
                    stopRowClick: true,
                    csvSkip: true,
                    render: (v: Voucher) =>
                      ['Issued', 'Expired'].includes(v.status) ? (
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() => {
                            setError(null);
                            setLocationEditor(v);
                            setEditLocations(locationDraft(v.escheat_locations));
                          }}
                        >
                          Jurisdictions
                        </Button>
                      ) : null,
                  },
                ]
              : []),
            ...(canWrite
              ? [
                  {
                    key: 'redeem',
                    header: '',
                    stopRowClick: true,
                    csvSkip: true,
                    render: (v: Voucher) =>
                      v.status === 'Issued' ? (
                        <Button
                          size="sm"
                          disabled={busy || !redeemStore}
                          title={redeemStore ? '' : 'Choose the redeeming store first'}
                          onClick={() => {
                            setError(null);
                            setRedemptionEditor(v);
                          }}
                        >
                          Redeem
                        </Button>
                      ) : null,
                  },
                ]
              : []),
          ]}
        />
      )}
      {redemptionEditor && (
        <Modal
          title={`Redeem voucher: ${redemptionEditor.voucher_number}`}
          onClose={closeRedemption}
          footer={
            <>
              <Button disabled={busy} onClick={closeRedemption}>
                Cancel
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void run(
                    () =>
                      api.post(
                        `/api/vouchers/${encodeURIComponent(redemptionEditor.voucher_number)}/redeem`,
                        { store_id: redeemStore }
                      ),
                    `Voucher ${redemptionEditor.voucher_number} redeemed without a transaction link.`,
                    () => setRedemptionEditor(null)
                  )
                }
              >
                Redeem without link
              </Button>
              <Button
                variant="primary"
                disabled={busy || !selectedTransactionId}
                onClick={() =>
                  void run(
                    () =>
                      api.post(
                        `/api/vouchers/${encodeURIComponent(redemptionEditor.voucher_number)}/redeem`,
                        { store_id: redeemStore, transaction_id: selectedTransactionId }
                      ),
                    `Voucher ${redemptionEditor.voucher_number} linked and redeemed.`,
                    () => setRedemptionEditor(null)
                  )
                }
              >
                {busy ? 'Recording…' : 'Link and redeem'}
              </Button>
            </>
          }
        >
          <p>
            Redeeming at {redeemStore}. Matching POS transactions are found from this voucher’s
            tender reference or item voucher code.
          </p>
          {error && (
            <Alert tone="error" onDismiss={() => setError(null)}>
              {error}
            </Alert>
          )}
          {redemptionCandidatesError && <Alert tone="error">{redemptionCandidatesError}</Alert>}
          {redemptionCandidates === null && !redemptionCandidatesError && (
            <p>Searching transactions…</p>
          )}
          {redemptionCandidates?.length ? (
            <Field label="Matching transaction">
              <select
                value={selectedTransactionId}
                onChange={(event) => setSelectedTransactionId(event.target.value)}
                disabled={busy}
              >
                <option value="">Choose a transaction to link</option>
                {redemptionCandidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {formatDate(candidate.business_date)} · {candidate.store_id} ·{' '}
                    {candidate.transaction_number || candidate.id} · {candidate.transaction_type} ·{' '}
                    {money(candidate.amount, candidate.currency)}
                  </option>
                ))}
              </select>
            </Field>
          ) : redemptionCandidates && !redemptionCandidatesError ? (
            <p>No matching transactions were found for this store and voucher.</p>
          ) : null}
          {redemptionCandidatesTruncated && (
            <p className="hint">More than 50 matches exist; select from the most recent 50.</p>
          )}
          <p className="hint">
            Linking is available only when the transaction contains this exact voucher reference. A
            redemption without a link can still be recorded.
          </p>
        </Modal>
      )}
      {reviewRows && (
        <Modal
          title="Review voucher escheatment"
          onClose={closeReview}
          footer={
            <>
              <Button disabled={busy} onClick={closeReview}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={busy}
                onClick={() =>
                  void run(
                    () =>
                      api.post('/api/vouchers/escheat', {
                        voucher_numbers: reviewRows.map((row) => row.voucher_number),
                        note: escheatNote.trim() || null,
                        ...(reviewRows.every((row) => row.routing_token)
                          ? {
                              routing_tokens: Object.fromEntries(
                                reviewRows.map((row) => [row.voucher_number, row.routing_token])
                              ),
                            }
                          : {}),
                      }),
                    `${reviewRows.length} voucher(s) escheated.`,
                    () => setReviewRows(null)
                  )
                }
              >
                {busy ? 'Recording…' : `Confirm escheatment of ${reviewRows.length}`}
              </Button>
            </>
          }
        >
          <p>These vouchers will leave the outstanding liability and can no longer be redeemed.</p>
          {error && (
            <Alert tone="error">
              {error}
              <Button
                disabled={busy}
                onClick={() => {
                  setReviewRows(null);
                  void load();
                }}
              >
                Refresh vouchers
              </Button>
            </Alert>
          )}
          <DataTable
            rows={reviewRows}
            rowKey={(row) => row.voucher_number}
            columns={[
              {
                key: 'voucher',
                header: 'Voucher',
                primary: true,
                render: (row) => row.voucher_number,
              },
              {
                key: 'amount',
                header: 'Amount',
                numeric: true,
                render: (row) => money(row.amount, row.currency),
              },
              {
                key: 'due',
                header: 'Eligible on',
                render: (row) => (row.eligible_on ? formatDate(row.eligible_on) : '—'),
              },
              { key: 'basis', header: 'Basis', render: (row) => row.eligibility_basis },
              {
                key: 'routing',
                header: 'Destination',
                render: (row) => <VoucherRouting decision={row.routing} explain />,
              },
            ]}
          />
          <Field label="Escheatment note">
            <textarea
              value={escheatNote}
              maxLength={2000}
              onChange={(event) => setEscheatNote(event.target.value)}
              disabled={busy}
            />
          </Field>
        </Modal>
      )}
      {locationEditor && (
        <Modal
          title={`Voucher jurisdictions: ${locationEditor.voucher_number}`}
          onClose={closeLocations}
          footer={
            <>
              <Button disabled={busy} onClick={closeLocations}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={busy || !validLocations(editLocations)}
                onClick={() =>
                  void run(
                    () =>
                      api.put(
                        `/api/vouchers/${encodeURIComponent(locationEditor.voucher_number)}/jurisdictions`,
                        {
                          locations: locationsPayload(editLocations),
                          expected_locations: locationEditor.escheat_locations ?? {},
                        }
                      ),
                    'Voucher jurisdictions saved.',
                    () => setLocationEditor(null)
                  )
                }
              >
                {busy ? 'Saving…' : 'Save jurisdictions'}
              </Button>
            </>
          }
        >
          <p className="hint">
            Blank Store uses the current foundation jurisdiction. Issuer and recipient are optional.
            Changes are recorded in the audit trail.
          </p>
          {error && (
            <Alert tone="error">
              {error}
              <Button
                disabled={busy}
                onClick={() => {
                  setLocationEditor(null);
                  void load();
                }}
              >
                Refresh vouchers
              </Button>
            </Alert>
          )}
          <VoucherJurisdictions value={editLocations} onChange={setEditLocations} disabled={busy} />
        </Modal>
      )}
    </div>
  );
}

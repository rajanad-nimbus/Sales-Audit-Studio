'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, money } from '@/lib/api';
import { Alert, Badge, Button, Field } from '@/components/ui';
import { apiError } from '@/lib/storeDays';
import { useMe } from '@/components/MeProvider';
import { useToast } from '@/components/Feedback';

interface Item {
  id: string;
  event_time: string;
  event_type: string;
  sub_type: string | null;
  transaction_number: string;
  amount: string | null;
  currency: string;
  audit_code: string | null;
  cashier_id: string | null;
  details: {
    vendor_number?: string;
    vendor_invoice_number?: string;
    proof_of_delivery_number?: string;
    payment_reference_number?: string;
    user_references?: { field: number; value: string }[];
  };
}
interface Context {
  event_id: string;
  store_id: string;
  register_id: string | null;
  business_date: string;
  earlier_not_shown: boolean;
  later_not_shown: boolean;
  events: Item[];
}
interface PaidOutCorrection {
  id: string;
  proposed_values: {
    before: Record<string, string | null>;
    after: Record<string, string | null>;
  };
  rationale: string;
  status: string;
  proposed_by: string;
  reviewed_by: string | null;
}
const PAID_OUT_FIELDS = [
  ['vendor_number', 'Vendor number', 10],
  ['vendor_invoice_number', 'Vendor invoice number', 30],
  ['proof_of_delivery_number', 'Proof of delivery number', 30],
  ['payment_reference_number', 'Payment reference number', 16],
] as const;

/**
 * The events either side of this one on the same register and day. A void or a cash pull reads
 * differently next to what came before it. Loads when the row opens, a few events at a time.
 */
export function EventContext({ id, typeLabel }: { id: string; typeLabel: (t: string) => string }) {
  const { me } = useMe();
  const toast = useToast();
  const canPropose = me?.role === 'finance' || me?.role === 'it';
  const canReview = me?.role === 'finance';
  const [context, setContext] = useState<Context | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [corrections, setCorrections] = useState<PaidOutCorrection[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [original, setOriginal] = useState<Record<string, string>>({});
  const [rationale, setRationale] = useState('');
  const [rejectionReasons, setRejectionReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const currentEvent = useMemo(
    () => context?.events.find((event) => event.id === id) ?? null,
    [context, id]
  );

  const loadCorrections = useCallback(async () => {
    if (!context || currentEvent?.event_type !== 'paid_out') {
      setCorrections([]);
      return;
    }
    try {
      const { data } = await api.get<{ items: PaidOutCorrection[] }>(
        `/api/register-events/${id}/detail-corrections`
      );
      setCorrections(data.items);
    } catch (e) {
      setError(apiError(e, 'Could not load paid-out corrections.'));
    }
  }, [context, currentEvent?.event_type, id]);

  useEffect(() => {
    let current = true;
    api
      .get<Context>(`/api/register-events/${id}/context`)
      .then(({ data }) => current && setContext(data))
      .catch((e) => current && setError(apiError(e, 'Could not load the surrounding events.')));
    return () => {
      current = false;
    };
  }, [id]);

  useEffect(() => {
    void loadCorrections();
  }, [loadCorrections]);

  useEffect(() => {
    if (currentEvent?.event_type !== 'paid_out') return;
    const next = Object.fromEntries(
      PAID_OUT_FIELDS.map(([field]) => [field, currentEvent.details?.[field] ?? ''])
    );
    setOriginal(next);
    setDraft(next);
  }, [currentEvent]);

  const proposeCorrection = async () => {
    if (!currentEvent || !rationale.trim()) return;
    const proposed_values = Object.fromEntries(
      PAID_OUT_FIELDS.flatMap(([field]) => {
        const value = draft[field] ?? '';
        if (value === original[field]) return [];
        return [[field, value || null]];
      })
    );
    if (!Object.keys(proposed_values).length) return;
    setBusy(true);
    try {
      await api.post(`/api/register-events/${id}/detail-corrections`, {
        proposed_values,
        rationale: rationale.trim(),
      });
      toast('Paid-out details sent for Finance review.', 'success');
      setRationale('');
      await loadCorrections();
    } catch (e) {
      setError(apiError(e, 'Could not propose paid-out detail changes.'));
    } finally {
      setBusy(false);
    }
  };

  const reviewCorrection = async (
    adjustmentId: string,
    action: 'approve' | 'reject',
    reason = ''
  ) => {
    setBusy(true);
    try {
      if (action === 'approve') {
        await api.post(`/api/register-events/detail-corrections/${adjustmentId}/approve`);
      } else {
        if (!reason.trim()) return;
        await api.post(`/api/register-events/detail-corrections/${adjustmentId}/reject`, {
          reason: reason.trim(),
        });
      }
      toast(action === 'approve' ? 'Paid-out details corrected.' : 'Correction rejected.', 'success');
      await loadCorrections();
      const { data } = await api.get<Context>(`/api/register-events/${id}/context`);
      setContext(data);
    } catch (e) {
      setError(apiError(e, 'Could not review this correction.'));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!context) return <p className="hint">Loading…</p>;

  return (
    <div className="stack stack-sm">
      <p>
        <b>
          {context.store_id} · {context.register_id ?? 'no register'} · {context.business_date}
        </b>
      </p>
      {context.earlier_not_shown && (
        <p className="hint">Earlier events on this register are not shown.</p>
      )}
      <ol className="case-timeline">
        {context.events.map((e) => (
          <li key={e.id} aria-current={e.id === context.event_id ? 'true' : undefined}>
            <time>{e.event_time.slice(11, 16)}</time>
            <div>
              <b>
                {e.id === context.event_id ? '▶ ' : ''}
                {typeLabel(e.event_type)}
                {e.sub_type ? ` · ${e.sub_type}` : ''}
              </b>
              <p>
                {e.transaction_number === '0'
                  ? 'No transaction number'
                  : `Transaction ${e.transaction_number}`}
                {e.amount ? ` · ${money(e.amount, e.currency)}` : ''}
                {e.cashier_id ? ` · ${e.cashier_id}` : ''}{' '}
                {e.audit_code && (
                  <Badge variant="error" size="sm">
                    {e.audit_code}
                  </Badge>
                )}
              </p>
              {Object.entries({
                'Vendor number': e.details?.vendor_number,
                'Vendor invoice': e.details?.vendor_invoice_number,
                'Proof of delivery': e.details?.proof_of_delivery_number,
                'Payment reference': e.details?.payment_reference_number,
              }).some(([, value]) => value) || e.details?.user_references?.length ? (
                <p className="hint">
                  {[
                    ['Vendor number', e.details?.vendor_number],
                    ['Vendor invoice', e.details?.vendor_invoice_number],
                    ['Proof of delivery', e.details?.proof_of_delivery_number],
                    ['Payment reference', e.details?.payment_reference_number],
                    ...(e.details?.user_references ?? []).map((r) => [
                      `Reference ${r.field}`,
                      r.value,
                    ]),
                  ]
                    .filter(([, value]) => value)
                    .map(([label, value]) => `${label}: ${value}`)
                    .join(' · ')}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {currentEvent?.event_type === 'paid_out' && (
        <section className="stack stack-sm" aria-label="Paid-out detail corrections">
          <h3>Paid-out details</h3>
          <p className="hint">
            Correct vendor and payment-reference metadata with a reason. Finance approval is required
            before the event detail changes.
          </p>
          {canPropose && (
            <div className="stack stack-sm">
              {PAID_OUT_FIELDS.map(([field, label, maxLength]) => (
                <Field key={field} label={label}>
                  <input
                    value={draft[field] ?? ''}
                    maxLength={maxLength}
                    autoComplete="off"
                    onChange={(event) =>
                      setDraft((values) => ({ ...values, [field]: event.target.value }))
                    }
                  />
                </Field>
              ))}
              <Field label="Reason for correction">
                <textarea
                  value={rationale}
                  maxLength={2000}
                  rows={3}
                  onChange={(event) => setRationale(event.target.value)}
                />
              </Field>
              <Button
                variant="secondary"
                disabled={busy || !rationale.trim() || !Object.keys(draft).some((key) => draft[key] !== original[key])}
                onClick={() => void proposeCorrection()}
              >
                {busy ? 'Submitting…' : 'Propose detail correction'}
              </Button>
            </div>
          )}
          {corrections.map((correction) => (
            <article className="card stack stack-sm" key={correction.id}>
              <div className="row row-between">
                <b>{correction.status}</b>
                <span className="hint">Proposed by {correction.proposed_by}</span>
              </div>
              <dl className="kv">
                {Object.entries(correction.proposed_values.after).map(([field, value]) => (
                  <div key={field}>
                    <dt>{PAID_OUT_FIELDS.find(([name]) => name === field)?.[1] ?? field}</dt>
                    <dd>
                      {correction.proposed_values.before[field] || '—'} → {value || '—'}
                    </dd>
                  </div>
                ))}
              </dl>
              <p>{correction.rationale}</p>
              {canReview && correction.status === 'Proposed' && (
                <div className="stack stack-sm">
                  <Field label="Rejection reason">
                    <input
                      value={rejectionReasons[correction.id] ?? ''}
                      maxLength={2000}
                      onChange={(event) =>
                        setRejectionReasons((values) => ({
                          ...values,
                          [correction.id]: event.target.value,
                        }))
                      }
                    />
                  </Field>
                  <div className="row">
                  <Button disabled={busy} onClick={() => void reviewCorrection(correction.id, 'approve')}>
                    Approve
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={busy || !rejectionReasons[correction.id]?.trim()}
                    onClick={() =>
                      void reviewCorrection(
                        correction.id,
                        'reject',
                        rejectionReasons[correction.id] ?? ''
                      )
                    }
                  >
                    Reject
                  </Button>
                  </div>
                </div>
              )}
            </article>
          ))}
        </section>
      )}
      {context.later_not_shown && (
        <p className="hint">Later events on this register are not shown.</p>
      )}
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { apiError } from '@/lib/storeDays';
import { formatDateTime } from '@/lib/preferences';
import { useMe } from '@/components/MeProvider';
import { useToast } from '@/components/Feedback';
import { Alert, Button, Card, Field } from '@/components/ui';

interface Option {
  tender_type: string;
  escheat_days: number;
  purge_days?: number | null;
  updated_by: string;
  updated_at: string;
}
interface Draft {
  tender_type: string;
  days: string;
  purge: string;
}
const toDrafts = (options: Option[]): Draft[] =>
  options.map((option) => ({
    tender_type: option.tender_type,
    days: String(option.escheat_days),
    purge: option.purge_days == null ? '' : String(option.purge_days),
  }));

export function VoucherOptions({ onSaved }: { onSaved?: () => void } = {}) {
  const { me } = useMe();
  const toast = useToast();
  const canEdit = ['it', 'admin'].includes(me?.role ?? '');
  const [options, setOptions] = useState<Option[] | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    api
      .get<Option[]>('/api/vouchers/options')
      .then(({ data }) => {
        if (current) {
          setOptions(data);
          setDrafts(toDrafts(data));
        }
      })
      .catch((e) => {
        if (current) setError(apiError(e, 'Could not load voucher policy.'));
      });
    return () => {
      current = false;
    };
  }, []);

  const names = drafts.map((row) => row.tender_type.trim().toLowerCase());
  const duplicate = new Set(names).size !== names.length;
  const invalid =
    duplicate ||
    drafts.some(
      (row) =>
        !row.tender_type.trim() ||
        !/^\d+$/.test(row.days) ||
        Number(row.days) > 36500 ||
        (row.purge !== '' && (!/^\d+$/.test(row.purge) || Number(row.purge) > 36500))
    );
  const dirty = options !== null && JSON.stringify(drafts) !== JSON.stringify(toDrafts(options));
  const change = (index: number, patch: Partial<Draft>) =>
    setDrafts((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const save = async () => {
    if (!dirty || invalid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.put<Option[]>('/api/vouchers/options', {
        options: drafts.map((row) => ({
          tender_type: row.tender_type.trim(),
          escheat_days: Number(row.days),
          purge_days: row.purge === '' ? null : Number(row.purge),
        })),
      });
      setOptions(data);
      setDrafts(toDrafts(data));
      toast('Voucher policy saved.', 'success');
      onSaved?.();
    } catch (e) {
      setError(apiError(e, 'Could not save voucher policy.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Voucher timing">
      <p className="hint">
        Set the number of days from issuance before an outstanding voucher becomes eligible. Rules
        apply to existing and newly issued vouchers, including vouchers with no expiry date. Tender
        codes match without regard to case.
      </p>
      <p className="hint">
        A tender without a rule becomes eligible after its expiry date. A value of 0 makes a voucher
        eligible on its issue date. Finance reviews eligible vouchers in Cash Office.
      </p>
      <p className="hint">
        Purge windows count from issuance. Blank disables purging for that tender. Only redeemed or
        escheated vouchers are eligible; outstanding liabilities are retained.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      {options === null ? (
        !error && <p className="hint">Loading voucher policy…</p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          {drafts.length === 0 && <p className="hint">No tender aging rules configured.</p>}
          {drafts.map((row, index) => {
            const saved = options.find(
              (option) => option.tender_type.toLowerCase() === row.tender_type.toLowerCase()
            );
            return (
              <div
                className="form-row"
                key={index}
                role="group"
                aria-label={`Tender rule ${index + 1}`}
              >
                <Field label="Tender code">
                  <input
                    value={row.tender_type}
                    maxLength={100}
                    disabled={!canEdit || busy}
                    onChange={(event) => change(index, { tender_type: event.target.value })}
                  />
                </Field>
                <Field
                  label="Purge after days"
                  hint="Blank disables purge. Whole days, up to 36,500."
                >
                  <input
                    type="number"
                    min="0"
                    max="36500"
                    step="1"
                    value={row.purge}
                    disabled={!canEdit || busy}
                    onChange={(event) => change(index, { purge: event.target.value })}
                  />
                </Field>
                <Field label="Escheat after days" hint="Whole days from issuance, up to 36,500.">
                  <input
                    type="number"
                    min="0"
                    max="36500"
                    step="1"
                    value={row.days}
                    disabled={!canEdit || busy}
                    onChange={(event) => change(index, { days: event.target.value })}
                  />
                </Field>
                {canEdit && (
                  <Button
                    disabled={busy}
                    aria-label={`Remove tender rule ${index + 1}`}
                    onClick={() => setDrafts((rows) => rows.filter((_, i) => i !== index))}
                  >
                    Remove
                  </Button>
                )}
                {saved?.updated_at && (
                  <small className="hint">
                    Updated by {saved.updated_by} · {formatDateTime(saved.updated_at)}
                  </small>
                )}
              </div>
            );
          })}
          {duplicate && <Alert tone="warning">Each tender code can have only one rule.</Alert>}
          {canEdit && (
            <div className="form-row">
              <Button
                disabled={busy || drafts.length >= 100}
                onClick={() =>
                  setDrafts((rows) => [...rows, { tender_type: '', days: '', purge: '' }])
                }
              >
                Add tender rule
              </Button>
              <Button type="submit" variant="primary" disabled={busy || !dirty || invalid}>
                {busy ? 'Saving…' : 'Save voucher policy'}
              </Button>
            </div>
          )}
        </form>
      )}
    </Card>
  );
}

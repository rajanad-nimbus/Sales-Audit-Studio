'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { useToast } from '@/components/Feedback';
import { useMe } from '@/components/MeProvider';
import { Alert, Button, Field } from '@/components/ui';

type PolicyPackage = {
  format: 'zetsa.reconciliation-policy';
  version: 1;
  policy: {
    amount_tolerance: string;
    amount_tolerance_currency: string;
    settlement_day_tolerance: number;
    duplicate_lookback_days: number;
    gap_min_count: number;
    gap_share: string;
  };
};

type Preview = {
  valid: boolean;
  preview_token: string;
  changed: boolean;
  current: PolicyPackage['policy'];
  proposed: PolicyPackage['policy'];
};

function errorMessage(cause: unknown, fallback: string) {
  const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

export function ReconciliationPolicyPackages({ onApplied }: { onApplied: () => Promise<void> }) {
  const { me } = useMe();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<{ fileName: string; bundle: PolicyPackage } | null>(
    null
  );
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canApply = me?.role === 'finance' || me?.role === 'admin';

  const downloadPackage = async () => {
    setError(null);
    try {
      const { data } = await api.get<PolicyPackage>('/api/reconciliation/policy/package');
      downloadBlob(
        `reconciliation-policy-${new Date().toISOString().slice(0, 10)}.json`,
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
      );
      toast('Reconciliation policy package downloaded.', 'success');
    } catch (cause) {
      setError(errorMessage(cause, 'Could not download the reconciliation policy package.'));
    }
  };

  const previewPackage = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    try {
      if (file.size > 100_000) throw new Error('Policy packages must be smaller than 100 KB.');
      const bundle = JSON.parse(await file.text()) as PolicyPackage;
      const { data } = await api.post<Preview>('/api/reconciliation/policy/package/preview', {
        package: bundle,
      });
      setSelected({ fileName: file.name, bundle });
      setPreview(data);
      setReason('');
    } catch (cause) {
      setError(
        cause instanceof SyntaxError
          ? 'The selected policy package is not valid JSON.'
          : cause instanceof Error && !('response' in cause)
            ? cause.message
            : errorMessage(cause, 'Could not preview the policy package.')
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = '';
    }
  };

  const applyPackage = async () => {
    if (!selected || !preview?.valid || !canApply) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{ changed: boolean }>(
        '/api/reconciliation/policy/package/apply',
        { package: selected.bundle, preview_token: preview.preview_token, reason }
      );
      setSelected(null);
      setPreview(null);
      setReason('');
      await onApplied();
      toast(
        data.changed ? 'Reconciliation policy package applied.' : 'Policy already matches package.',
        'success'
      );
    } catch (cause) {
      setError(errorMessage(cause, 'Could not apply the package. Preview it again and retry.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack stack-sm">
      <p className="hint">
        Export approved thresholds as a versioned JSON package. Import previews current and proposed
        settings and detects policy changes made after preview.
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="cluster">
        <Button size="sm" variant="secondary" onClick={() => void downloadPackage()}>
          Download policy package
        </Button>
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={(event) => void previewPackage(event.target.files?.[0])}
        />
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {busy ? 'Preparing…' : 'Preview package import'}
        </Button>
      </div>
      {selected && preview && (
        <div className="stack stack-sm">
          <p>
            <b>Preview: {selected.fileName}</b> ·{' '}
            {preview.changed ? 'Policy changes detected' : 'No changes'}
          </p>
          <dl className="kv">
            <dt>Amount tolerance</dt>
            <dd>
              {preview.current.amount_tolerance} {preview.current.amount_tolerance_currency} →{' '}
              {preview.proposed.amount_tolerance} {preview.proposed.amount_tolerance_currency}
            </dd>
            <dt>Settlement days</dt>
            <dd>
              {preview.current.settlement_day_tolerance} →{' '}
              {preview.proposed.settlement_day_tolerance}
            </dd>
            <dt>Duplicate lookback</dt>
            <dd>
              {preview.current.duplicate_lookback_days} → {preview.proposed.duplicate_lookback_days}{' '}
              days
            </dd>
            <dt>Large gap threshold</dt>
            <dd>
              {preview.current.gap_min_count} / {Number(preview.current.gap_share) * 100}% →{' '}
              {preview.proposed.gap_min_count} / {Number(preview.proposed.gap_share) * 100}%
            </dd>
          </dl>
          <Field
            label="Reason for promotion"
            hint="At least 10 characters; recorded in the audit trail."
          >
            <textarea
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          <div className="cluster">
            <Button
              size="sm"
              variant="primary"
              disabled={
                !preview.valid || !preview.changed || busy || !canApply || reason.trim().length < 10
              }
              onClick={() => void applyPackage()}
            >
              {busy ? 'Applying…' : 'Apply policy package'}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setSelected(null);
                setPreview(null);
              }}
            >
              Cancel
            </Button>
            {!canApply && (
              <span className="page-sub">Finance role is required to apply policies.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
